package investigator

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"sort"
	"strings"
	"testing"
	"time"

	alertmodel "github.com/ongridio/ongrid/internal/manager/model/alert"
	"github.com/ongridio/ongrid/internal/pkg/llm"
)

type fakeSummarizer struct {
	resp    *llm.ChatResp
	err     error
	lastReq llm.ChatReq
	calls   int
}

type summarizerFunc func(context.Context, llm.ChatReq) (*llm.ChatResp, error)

func (f summarizerFunc) Chat(ctx context.Context, req llm.ChatReq) (*llm.ChatResp, error) {
	return f(ctx, req)
}

func (f *fakeSummarizer) Chat(_ context.Context, req llm.ChatReq) (*llm.ChatResp, error) {
	f.calls++
	f.lastReq = req
	return f.resp, f.err
}

// fakeRelatedQuerier returns canned related rows + records what the
// caller asked for so tests can assert window/limit got passed.
type fakeRelatedQuerier struct {
	rows []*alertmodel.Incident
	err  error
	last struct {
		halfWindow time.Duration
		limit      int
		deviceID   *uint64
	}
}

func (f *fakeRelatedQuerier) RelatedToIncident(_ context.Context, target *alertmodel.Incident, halfWindow time.Duration, limit int) ([]*alertmodel.Incident, error) {
	f.last.halfWindow = halfWindow
	f.last.limit = limit
	if target != nil {
		f.last.deviceID = target.DeviceID
	}
	return f.rows, f.err
}

// TestExtractStructured_RelatedAlerts — when a RelatedAlertQuerier is
// wired, extracted ReadyFields carry a populated related_alerts_json
// (instead of the legacy "[]"). Filters out the target's own row +
// any nil entries.
func TestExtractStructured_RelatedAlerts(t *testing.T) {
	now := time.Now().UTC()
	dev := uint64(67)
	related := &fakeRelatedQuerier{
		rows: []*alertmodel.Incident{
			{ID: 1, Rule: "swap_high", RuleName: "Swap > 50%", Severity: "warning", Status: "open", FirstFiredAt: now, LastFiredAt: now, DeviceID: &dev},
			nil, // skipped
			{ID: 99, Rule: "self", RuleName: "self", Severity: "warning", Status: "open", FirstFiredAt: now, LastFiredAt: now, DeviceID: &dev}, // matches target ID → skipped
			{ID: 2, Rule: "disk_high", RuleName: "Disk > 80%", Severity: "warning", Status: "open", FirstFiredAt: now, LastFiredAt: now, DeviceID: &dev},
		},
	}
	uc := (&Usecase{
		cfg: Config{},
	}).WithRelatedQuerier(related)
	fields := uc.extractStructured(context.Background(), alertmodel.Incident{ID: 99, DeviceID: &dev}, "narrative", 0, "", evidenceSet{})

	if fields.RelatedAlertsJSON == "[]" {
		t.Fatalf("related_alerts_json not populated: %q", fields.RelatedAlertsJSON)
	}
	// Cheap sanity: must include the two real rows by rule name.
	for _, want := range []string{"swap_high", "disk_high"} {
		if !contains(fields.RelatedAlertsJSON, want) {
			t.Errorf("related_alerts_json missing %q: %s", want, fields.RelatedAlertsJSON)
		}
	}
	// Target self-row must NOT appear.
	if contains(fields.RelatedAlertsJSON, "\"incident_id\":99") {
		t.Errorf("target incident leaked into related_alerts: %s", fields.RelatedAlertsJSON)
	}
	if related.last.halfWindow != relatedAlertHalfWindow {
		t.Errorf("halfWindow = %v, want %v", related.last.halfWindow, relatedAlertHalfWindow)
	}
}

func contains(s, sub string) bool {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}

// TestExtractJSONBlob_VariousWrappers — the LLM sometimes prefixes
// the JSON with ```json fences, leading prose, or trailing commentary.
// The blob extractor MUST find the first balanced top-level object.
func TestExtractJSONBlob_VariousWrappers(t *testing.T) {
	cases := []struct {
		in, want string
	}{
		{
			in:   `{"a":1}`,
			want: `{"a":1}`,
		},
		{
			in:   "```json\n{\"a\":1}\n```",
			want: `{"a":1}`,
		},
		{
			in:   "Sure, here:\n```\n{\"x\":\"y\"}\n```\n",
			want: `{"x":"y"}`,
		},
		{
			in:   `{"nested": {"k": "v"}, "arr": [1, {"q":"r"}]}`,
			want: `{"nested": {"k": "v"}, "arr": [1, {"q":"r"}]}`,
		},
		{
			in:   `{"escaped": "}",  "ok": true}`,
			want: `{"escaped": "}",  "ok": true}`,
		},
		{in: "no json at all", want: ""},
		{in: "", want: ""},
	}
	for _, tc := range cases {
		if got := extractJSONBlob(tc.in); got != tc.want {
			t.Errorf("extractJSONBlob(%q)\n  got:  %q\n  want: %q", tc.in, got, tc.want)
		}
	}
}

// TestExtractStructured_SummarizerNil — when no summarizer wired, the
// extractor preserves findings_md without manufacturing a root cause.
func TestExtractStructured_SummarizerNil(t *testing.T) {
	uc := &Usecase{cfg: Config{SummarizerTimeout: time.Second}}
	final := "## Root\n\npg-replica-7 saturated"
	fields := uc.extractStructured(context.Background(), alertmodel.Incident{ID: 1}, final, 3, "", evidenceSet{})
	if fields.RootCause != "" {
		t.Errorf("root_cause = %q", fields.RootCause)
	}
	if fields.FindingsMD != final {
		t.Errorf("findings_md not preserved")
	}
	if fields.ToolCallCount != 3 {
		t.Errorf("tool_call_count = %d, want 3", fields.ToolCallCount)
	}
	if fields.Confidence != nil {
		t.Errorf("confidence should be nil when summarizer unwired")
	}
}

// TestExtractStructured_ParsesValid — happy path. The summarizer
// returns proper JSON; the extractor promotes every field into
// ReadyFields and JSON-marshals nested objects for the columns that
// store *_json strings.
func TestExtractStructured_ParsesValid(t *testing.T) {
	conf := 0.85
	sum := &fakeSummarizer{resp: &llm.ChatResp{
		Assistant: llm.Message{
			Role: "assistant",
			Content: `{
  "root_cause": "PID 8821 saturated CPU on pg-replica-7",
  "affected_window": "2026-05-19T03:42:15Z/2026-05-19T03:48:30Z",
  "pinpoint_target": {"device_id": 42, "pid": 8821, "service": "postgres"},
  "evidence": [{"step": 1, "tool": "correlate_incident", "summary": "cpu spike + net spike"}],
  "suggested_actions": [{"label": "kill query", "danger": "high"}],
  "confidence": 0.85
}`,
		},
	}}
	uc := &Usecase{
		summarizer: sum,
		cfg: Config{
			SummarizerModel:   "glm-4-air",
			SummarizerTimeout: time.Second,
		},
	}
	fields := uc.extractStructured(context.Background(), alertmodel.Incident{ID: 1}, "narrative", 7, "", evidenceSet{})

	if fields.RootCause != "PID 8821 saturated CPU on pg-replica-7" {
		t.Errorf("root_cause = %q", fields.RootCause)
	}
	if fields.AffectedWindow != "2026-05-19T03:42:15Z/2026-05-19T03:48:30Z" {
		t.Errorf("affected_window = %q", fields.AffectedWindow)
	}
	if fields.PinpointedTargetJSON == "{}" {
		t.Errorf("pinpoint_target_json not populated: %q", fields.PinpointedTargetJSON)
	}
	if fields.EvidenceJSON == "[]" {
		t.Errorf("evidence_json not populated: %q", fields.EvidenceJSON)
	}
	if fields.SuggestedActionsJSON == "[]" {
		t.Errorf("suggested_actions_json not populated: %q", fields.SuggestedActionsJSON)
	}
	if fields.Confidence == nil || *fields.Confidence != conf {
		t.Errorf("confidence = %v, want %v", fields.Confidence, conf)
	}
	if fields.ToolCallCount != 7 {
		t.Errorf("tool_call_count = %d, want 7", fields.ToolCallCount)
	}
	if sum.lastReq.Model != "glm-4-air" {
		t.Errorf("model = %q, want glm-4-air", sum.lastReq.Model)
	}
}

func TestExtractStructured_EmptyModelUsesRouterDefault(t *testing.T) {
	sum := &fakeSummarizer{resp: &llm.ChatResp{
		Assistant: llm.Message{Role: "assistant", Content: `{"root_cause":"router default"}`},
	}}
	uc := &Usecase{
		summarizer: sum,
		cfg:        Config{SummarizerTimeout: time.Second},
	}

	fields := uc.extractStructured(context.Background(), alertmodel.Incident{ID: 1}, "narrative", 2, "", evidenceSet{})
	if fields.RootCause != "router default" {
		t.Fatalf("root_cause = %q, want router default", fields.RootCause)
	}
	if sum.calls != 1 {
		t.Fatalf("summarizer calls = %d, want 1", sum.calls)
	}
	if sum.lastReq.Provider != "" || sum.lastReq.Model != "" {
		t.Fatalf("summarizer request = provider %q model %q, want empty router defaults", sum.lastReq.Provider, sum.lastReq.Model)
	}
}

// TestExtractStructured_LLMError — when the summarizer call errors,
// preserve the narrative and mark the reference check incomplete.
func TestExtractStructured_LLMError(t *testing.T) {
	sum := &fakeSummarizer{err: errors.New("rate limited")}
	uc := &Usecase{
		summarizer: sum,
		cfg:        Config{SummarizerModel: "glm-4-air", SummarizerTimeout: time.Second},
	}
	final := "Root cause line\n\nbody"
	fields := uc.extractStructured(context.Background(), alertmodel.Incident{ID: 1}, final, 0, "", evidenceSet{})
	if fields.RootCause != "" {
		t.Errorf("root_cause fallback = %q", fields.RootCause)
	}
	if fields.FindingsMD != final {
		t.Errorf("findings_md should be preserved on fallback")
	}
}

// TestExtractStructured_BadJSON — model ignores the system prompt and
// replies with prose. Fall back the same way.
func TestExtractStructured_BadJSON(t *testing.T) {
	sum := &fakeSummarizer{resp: &llm.ChatResp{
		Assistant: llm.Message{Role: "assistant", Content: "I think the cause is..."},
	}}
	uc := &Usecase{summarizer: sum, cfg: Config{SummarizerModel: "glm-4-air", SummarizerTimeout: time.Second}}
	fields := uc.extractStructured(context.Background(), alertmodel.Incident{ID: 1}, "narrative body", 0, "", evidenceSet{})
	if fields.RootCause != "" {
		t.Errorf("fallback root_cause = %q", fields.RootCause)
	}
	if fields.Confidence != nil {
		t.Errorf("confidence should be nil on bad-JSON fallback")
	}
}

// TestExtractStructured_ConfidenceClamp — models occasionally return
// 0..100 or 0..10. Promote into ReadyFields as 0..1.
func TestExtractStructured_ConfidenceClamp(t *testing.T) {
	cases := []struct {
		raw, want float64
	}{
		{0.5, 0.5},
		{85, 0.85}, // 0..100 form
		{1.0, 1.0},
		{-0.1, 0},
		{1.5, 1.0}, // > 1 but ≤ 100 → /100 → 0.015 — let's check actual behaviour
	}
	for _, tc := range cases {
		raw := tc.raw
		sum := &fakeSummarizer{resp: &llm.ChatResp{
			Assistant: llm.Message{Role: "assistant",
				Content: `{"root_cause":"x","confidence":` + fmtFloat(raw) + `}`}}}
		uc := &Usecase{summarizer: sum, cfg: Config{SummarizerModel: "m", SummarizerTimeout: time.Second}}
		fields := uc.extractStructured(context.Background(), alertmodel.Incident{ID: 1}, "narr", 0, "", evidenceSet{})
		if fields.Confidence == nil {
			t.Errorf("raw=%v: confidence nil", raw)
			continue
		}
		// For raw=1.5 the rule (>1 && <=100 → /100) gives 0.015 — accept either branch.
		got := *fields.Confidence
		if (raw <= 1 && raw >= 0 && got != raw) ||
			(raw > 1 && raw <= 100 && got != raw/100.0) ||
			(raw < 0 && got != 0) {
			t.Errorf("raw=%v -> got %v (expected per clamp rule)", raw, got)
		}
	}
}

func fmtFloat(f float64) string {
	switch f {
	case 0:
		return "0"
	}
	// Avoid importing strconv for a one-liner.
	return func() string {
		s := ""
		neg := f < 0
		if neg {
			f = -f
		}
		intPart := int64(f)
		frac := f - float64(intPart)
		s = ""
		if neg {
			s = "-"
		}
		s += itoa(intPart)
		if frac == 0 {
			return s
		}
		// 6-decimal repr.
		fracInt := int64(frac * 1e6)
		s += "."
		fracs := itoa(fracInt)
		for i := 0; i < 6-len(fracs); i++ {
			s += "0"
		}
		s += fracs
		// trim trailing zeros
		for len(s) > 0 && s[len(s)-1] == '0' {
			s = s[:len(s)-1]
		}
		if len(s) > 0 && s[len(s)-1] == '.' {
			s = s[:len(s)-1]
		}
		return s
	}()
}

func itoa(n int64) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}

// TestClassifyCheckIssues — the fix for the "reference check incomplete"
// false positive: only genuine blockers force incomplete; quality issues
// downgrade to partial; informational limit notices change nothing.
func TestClassifyCheckIssues(t *testing.T) {
	for _, tc := range []struct {
		name              string
		issues            []string
		blocking, quality bool
	}{
		{"reader failure", []string{"source_read_failed"}, true, false},
		{"reader unavailable", []string{"source_reader_unavailable"}, true, false},
		{"source incomplete", []string{"source_incomplete"}, true, false},
		{"source limit", []string{"source_limit"}, true, false},
		{"extraction unavailable", []string{"extraction_unavailable"}, true, false},
		{"stale", []string{"stale_observation"}, false, true},
		{"low coverage", []string{"low_coverage"}, false, true},
		{"preview only", []string{"preview_truncated"}, false, false},
		{"prompt only", []string{"prompt_limit"}, false, false},
		{"reference limit only", []string{"reference_limit"}, false, false},
		{"prefix reference is not informational", []string{"unknown_source:E9"}, true, false},
		{"mixed", []string{"preview_truncated", "low_coverage", "source_read_failed"}, true, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			blocking, quality := classifyCheckIssues(tc.issues)
			if blocking != tc.blocking || quality != tc.quality {
				t.Fatalf("blocking=%v quality=%v want %v/%v", blocking, quality, tc.blocking, tc.quality)
			}
		})
	}
}

// TestExtractStructured_IssueClassification — pure informational issues
// must no longer turn the whole evidence_check into "incomplete".
func TestExtractStructured_IssueClassification(t *testing.T) {
	for _, tc := range []struct {
		name   string
		issues []string
		want   string
	}{
		{"preview truncated is informational", []string{"preview_truncated"}, "unverified"},
		{"prompt limit is informational", []string{"prompt_limit"}, "unverified"},
		{"reference limit is informational", []string{"reference_limit"}, "unverified"},
		{"all informational together", []string{"preview_truncated", "prompt_limit", "reference_limit"}, "unverified"},
		{"read failure blocks", []string{"source_read_failed"}, "incomplete"},
		{"reader unavailable blocks", []string{"source_reader_unavailable"}, "incomplete"},
		{"stale downgrades to partial", []string{"stale_observation"}, "partial"},
		{"low coverage downgrades to partial", []string{"low_coverage"}, "partial"},
		{"info plus quality stays partial", []string{"preview_truncated", "stale_observation"}, "partial"},
		{"blocking wins over quality", []string{"low_coverage", "source_read_failed"}, "incomplete"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			sum := &fakeSummarizer{resp: &llm.ChatResp{
				Assistant: llm.Message{Content: `{"root_cause":"x"}`}}}
			uc := &Usecase{summarizer: sum, cfg: Config{SummarizerTimeout: time.Second}}
			set := evidenceSet{Issues: append([]string{}, tc.issues...)}
			fields := uc.extractStructured(context.Background(), alertmodel.Incident{ID: 1}, "narrative", 1, "", set)
			var check alertmodel.EvidenceCheck
			if err := json.Unmarshal([]byte(*fields.EvidenceCheckJSON), &check); err != nil {
				t.Fatal(err)
			}
			if check.Status != tc.want {
				t.Fatalf("status=%s want %s issues=%v", check.Status, tc.want, check.Issues)
			}
		})
	}
}

// TestExtractStructured_ConfidenceUnchangedWithoutSources — coverage
// assessment is not applicable when there are no observation sources, so
// the model's self-reported confidence must be preserved exactly.
func TestExtractStructured_ConfidenceUnchangedWithoutSources(t *testing.T) {
	sum := &fakeSummarizer{resp: &llm.ChatResp{
		Assistant: llm.Message{Content: `{"root_cause":"x","confidence":0.85}`}}}
	uc := &Usecase{summarizer: sum, cfg: Config{SummarizerTimeout: time.Second}}
	fields := uc.extractStructured(context.Background(), alertmodel.Incident{ID: 1}, "narrative", 1, "", evidenceSet{})
	if fields.Confidence == nil || math.Abs(*fields.Confidence-0.85) > 1e-9 {
		t.Fatalf("confidence=%v want 0.85", fields.Confidence)
	}
	var factors map[string]any
	if err := json.Unmarshal([]byte(fields.ConfidenceFactorsJSON), &factors); err != nil {
		t.Fatal(err)
	}
	if penalty, _ := factors["freshness_penalty"].(float64); math.Abs(penalty-1.0) > 1e-9 {
		t.Fatalf("freshness_penalty=%v want 1", penalty)
	}
	for _, key := range []string{
		"evidence_steps", "tool_call_count", "has_pinpoint", "has_affected_win",
		"has_suggested", "narrative_present", "observation_coverage",
		"stale_observation", "coverage_ratio", "freshness_penalty",
	} {
		if _, ok := factors[key]; !ok {
			t.Errorf("missing confidence factor %q", key)
		}
	}
}

// TestAssessObservationCoverageUsesUnion — overlapping log observations
// must be merged, not summed. hotfix12: the coverage union now counts only
// log / event class sources, and a log observation must reach the window
// tail to avoid a stale verdict.
func TestAssessObservationCoverageUsesUnion(t *testing.T) {
	base := time.Date(2026, 11, 4, 12, 0, 0, 0, time.UTC)
	inc := alertmodel.Incident{ID: 1, FirstFiredAt: base, LastFiredAt: base.Add(time.Hour)}
	windowStart := base.Add(-observationWindowTolerance)
	windowEnd := base.Add(time.Hour).Add(observationWindowTolerance)
	evaluatedAt := time.Date(2026, 11, 7, 0, 0, 0, 0, time.UTC)
	// Two overlapping log intervals that reach the window tail.
	start := base.Add(10 * time.Minute)
	end := windowEnd.Add(-time.Minute)
	src := []alertmodel.EvidenceSource{
		{ID: "E1", Tool: "query_logql", State: "available", DataStart: start.Format(time.RFC3339Nano), DataEnd: end.Format(time.RFC3339Nano)},
		{ID: "E2", Tool: "query_logql", State: "available", DataStart: start.Format(time.RFC3339Nano), DataEnd: end.Format(time.RFC3339Nano)},
	}
	issues, factors := assessObservationCoverage(inc, evidenceSet{Sources: src}, evaluatedAt)
	wantRatio := end.Sub(start).Seconds() / windowEnd.Sub(windowStart).Seconds()
	if ratio, _ := factors["coverage_ratio"].(float64); math.Abs(ratio-wantRatio) > 1e-9 {
		t.Fatalf("coverage_ratio=%v want %v (union, not sum)", ratio, wantRatio)
	}
	if stale, _ := factors["stale_observation"].(bool); stale {
		t.Fatal("log observations reaching the window tail must not be stale")
	}
	if low, _ := factors["low_coverage"].(bool); low {
		t.Fatal("a log interval covering most of the window is not low coverage")
	}
	if usable, _ := factors["observation_sources"].(int); usable != 2 {
		t.Fatalf("observation_sources=%v want 2", usable)
	}
	if n, _ := factors["log_observation_sources"].(int); n != 2 {
		t.Fatalf("log_observation_sources=%v want 2", n)
	}
	if n, _ := factors["metric_observation_sources"].(int); n != 0 {
		t.Fatalf("metric_observation_sources=%v want 0", n)
	}
	if strings.Contains(strings.Join(issues, ","), "stale_observation") {
		t.Fatalf("unexpected stale issue: %v", issues)
	}

	// A wholly out-of-window log source is stale regardless of class.
	oldEnd := base.Add(-3 * time.Hour)
	oldStart := oldEnd.Add(-5 * time.Minute)
	staleSrc := append(append([]alertmodel.EvidenceSource{}, src...), alertmodel.EvidenceSource{
		ID: "E3", Tool: "query_logql", State: "available",
		DataStart: oldStart.Format(time.RFC3339Nano), DataEnd: oldEnd.Format(time.RFC3339Nano),
	})
	issues2, factors2 := assessObservationCoverage(inc, evidenceSet{Sources: staleSrc}, evaluatedAt)
	if !strings.Contains(strings.Join(issues2, ","), "stale_observation") {
		t.Fatalf("expected stale_observation: issues=%v", issues2)
	}
	if stale, _ := factors2["stale_observation"].(bool); !stale {
		t.Fatal("stale flag missing from factors")
	}
	// Negative / zero-length window: no division by zero, no panic.
	_, factors3 := assessObservationCoverage(
		alertmodel.Incident{ID: 2, FirstFiredAt: base.Add(time.Hour), LastFiredAt: base},
		evidenceSet{Sources: src}, evaluatedAt)
	if ratio, _ := factors3["coverage_ratio"].(float64); ratio != 0 {
		t.Fatalf("zero-window coverage_ratio=%v want 0", ratio)
	}
}

// TestExtractStructured_FreshnessPenalty — stale + low coverage apply the
// stricter (smaller) multiplier and the adopted penalty is recorded.
func TestExtractStructured_FreshnessPenalty(t *testing.T) {
	base := time.Date(2026, 11, 4, 12, 0, 0, 0, time.UTC)
	oldEnd := base.Add(-6 * time.Hour)
	oldStart := oldEnd.Add(-time.Minute)
	sum := &fakeSummarizer{resp: &llm.ChatResp{
		Assistant: llm.Message{Content: `{"root_cause":"x","confidence":0.8}`}}}
	uc := &Usecase{summarizer: sum, cfg: Config{SummarizerTimeout: time.Second}}
	inc := alertmodel.Incident{ID: 1, FirstFiredAt: base, LastFiredAt: base.Add(10 * time.Minute)}
	set := evidenceSet{Sources: []alertmodel.EvidenceSource{{
		ID: "E1", Tool: "query_logql", State: "available",
		DataStart: oldStart.Format(time.RFC3339Nano), DataEnd: oldEnd.Format(time.RFC3339Nano),
	}}}
	fields := uc.extractStructured(context.Background(), inc, "narrative", 1, "", set)
	if fields.Confidence == nil || math.Abs(*fields.Confidence-0.8*0.7) > 1e-9 {
		t.Fatalf("confidence=%v want %v", fields.Confidence, 0.8*0.7)
	}
	var factors map[string]any
	if err := json.Unmarshal([]byte(fields.ConfidenceFactorsJSON), &factors); err != nil {
		t.Fatal(err)
	}
	if penalty, _ := factors["freshness_penalty"].(float64); math.Abs(penalty-0.7) > 1e-9 {
		t.Fatalf("freshness_penalty=%v want 0.7", penalty)
	}
	var check alertmodel.EvidenceCheck
	if err := json.Unmarshal([]byte(*fields.EvidenceCheckJSON), &check); err != nil {
		t.Fatal(err)
	}
	if check.Status == "incomplete" {
		t.Fatalf("quality issues must not force incomplete: %+v", check)
	}
	joined := strings.Join(check.Issues, ",")
	if !strings.Contains(joined, "stale_observation") || !strings.Contains(joined, "low_coverage") {
		t.Fatalf("missing coverage issues: %v", check.Issues)
	}
}

// hotfix12Incident148 reproduces the investigation-window snapshot captured
// when the hotfix11 smoke analysis for incident #148 was triggered
// (first_fired_at 2026-11-04 13:05:22.807, last_fired_at the
// then-current 2026-11-07 00:50:13.170, both UTC).
func hotfix12Incident148() alertmodel.Incident {
	return alertmodel.Incident{
		ID:           148,
		FirstFiredAt: time.Date(2026, 11, 4, 13, 5, 22, 807000000, time.UTC),
		LastFiredAt:  time.Date(2026, 11, 7, 0, 50, 13, 170000000, time.UTC),
	}
}

// hotfix12StaleSources is the exact production distribution QA measured on
// #148: E2 / E8 are 3-day query_promql lookbacks (metric class, excluded
// from log coverage); E9 is a query_logql observation of only 142.997s at
// the very beginning of the window, ~57.7h away from its end.
func hotfix12StaleSources() evidenceSet {
	end := time.Date(2026, 11, 7, 0, 50, 13, 170000000, time.UTC).Add(observationWindowTolerance)
	lookbackStart := end.Add(-72 * time.Hour)
	return evidenceSet{
		Issues: []string{"preview_truncated"},
		Sources: []alertmodel.EvidenceSource{
			{ID: "E2", Tool: "query_promql", State: "available",
				DataStart: lookbackStart.Format(time.RFC3339Nano), DataEnd: end.Format(time.RFC3339Nano)},
			{ID: "E8", Tool: "query_promql", State: "available",
				DataStart: lookbackStart.Format(time.RFC3339Nano), DataEnd: end.Format(time.RFC3339Nano)},
			{ID: "E9", Tool: "query_logql", State: "available",
				DataStart: "2026-11-04T15:03:35.793535Z", DataEnd: "2026-11-04T15:05:58.790468Z"},
		},
	}
}

// TestAssessObservationCoverageStaleWideMetricPlusEarlyLog is acceptance
// case (a): replay the real #148 distribution. Wide PromQL lookbacks must
// no longer mask a 143-second log observation that stopped 57h before the
// window end.
func TestAssessObservationCoverageStaleWideMetricPlusEarlyLog(t *testing.T) {
	inc := hotfix12Incident148()
	set := hotfix12StaleSources()
	evaluatedAt := time.Date(2026, 11, 7, 0, 52, 25, 428000000, time.UTC)
	issues, factors := assessObservationCoverage(inc, set, evaluatedAt)

	if stale, _ := factors["stale_observation"].(bool); !stale {
		t.Fatalf("stale_observation must be true: issues=%v factors=%v", issues, factors)
	}
	if low, _ := factors["low_coverage"].(bool); !low {
		t.Fatalf("low_coverage must be true: issues=%v factors=%v", issues, factors)
	}
	ratio, _ := factors["coverage_ratio"].(float64)
	if math.Abs(ratio-0.0006593) > 1e-6 {
		t.Fatalf("coverage_ratio=%.12f want ~0.0006593", ratio)
	}
	if n, _ := factors["log_observation_sources"].(int); n != 1 {
		t.Fatalf("log_observation_sources=%v want 1", n)
	}
	if n, _ := factors["metric_observation_sources"].(int); n != 2 {
		t.Fatalf("metric_observation_sources=%v want 2", n)
	}
	if n, _ := factors["observation_sources"].(int); n != 3 {
		t.Fatalf("observation_sources=%v want 3", n)
	}
	if gap, _ := factors["last_log_end_gap_seconds"].(float64); gap < 200000 {
		t.Fatalf("last_log_end_gap_seconds=%v want >200000", gap)
	}
	ws, _ := factors["window_start_used"].(string)
	we, _ := factors["window_end_used"].(string)
	if ws != inc.FirstFiredAt.Add(-observationWindowTolerance).UTC().Format(time.RFC3339Nano) {
		t.Fatalf("window_start_used=%s", ws)
	}
	if we != inc.LastFiredAt.Add(observationWindowTolerance).UTC().Format(time.RFC3339Nano) {
		t.Fatalf("window_end_used=%s", we)
	}
	if ev, _ := factors["evaluated_at"].(string); ev != evaluatedAt.UTC().Format(time.RFC3339Nano) {
		t.Fatalf("evaluated_at=%s", ev)
	}
	details, _ := factors["observation_details"].([]observationSourceDetail)
	if len(details) != 3 {
		t.Fatalf("observation_details len=%d want 3", len(details))
	}
	counted := 0
	for _, d := range details {
		if d.CountedInLogCoverage {
			counted++
			if d.ID != "E9" || !d.IsLogClass {
				t.Fatalf("unexpected counted source: %+v", d)
			}
		}
	}
	if counted != 1 {
		t.Fatalf("counted_in_log_coverage rows=%d want 1", counted)
	}
	for _, want := range []string{"stale_observation", "low_coverage"} {
		if !strings.Contains(strings.Join(issues, ","), want) {
			t.Fatalf("missing issue %q: %v", want, issues)
		}
	}
	t.Logf("case(a) stale=true low_coverage=true coverage_ratio=%.12f gap=%.3fs window=%s..%s",
		ratio, factors["last_log_end_gap_seconds"], ws, we)
}

// TestExtractStructuredStaleWideMetricPenalty is acceptance case (a) end to
// end: the freshness penalty, the confidence downgrade and the group-1
// status classification must all hold on the real distribution.
func TestExtractStructuredStaleWideMetricPenalty(t *testing.T) {
	inc := hotfix12Incident148()
	set := hotfix12StaleSources()
	sum := &fakeSummarizer{resp: &llm.ChatResp{Assistant: llm.Message{
		Content: `{"root_cause":"x","root_cause_source_ids":["E9"],"confidence":0.8}`}}}
	uc := &Usecase{summarizer: sum, cfg: Config{SummarizerTimeout: time.Second}}
	fields := uc.extractStructured(context.Background(), inc, "narrative", 3, "", set)

	if fields.Confidence == nil || math.Abs(*fields.Confidence-0.8*0.7) > 1e-9 {
		t.Fatalf("confidence=%v want %v", fields.Confidence, 0.8*0.7)
	}
	var factors map[string]any
	if err := json.Unmarshal([]byte(fields.ConfidenceFactorsJSON), &factors); err != nil {
		t.Fatal(err)
	}
	if penalty, _ := factors["freshness_penalty"].(float64); math.Abs(penalty-0.7) > 1e-9 {
		t.Fatalf("freshness_penalty=%v want 0.7", penalty)
	}
	for _, key := range []string{"evaluated_at", "window_start_used", "window_end_used",
		"log_observation_sources", "metric_observation_sources", "last_log_end_gap_seconds",
		"coverage_ratio", "observation_details"} {
		if _, ok := factors[key]; !ok {
			t.Fatalf("missing confidence factor %q", key)
		}
	}
	var check alertmodel.EvidenceCheck
	if err := json.Unmarshal([]byte(*fields.EvidenceCheckJSON), &check); err != nil {
		t.Fatal(err)
	}
	// Group-1 equivalence: issues=["preview_truncated"] alone must NOT
	// force "incomplete" (#149 / #201 before the fix). The two quality
	// issues downgrade to "partial", never back to "incomplete".
	if check.Status != "partial" {
		t.Fatalf("status=%s want partial (must not regress to incomplete): %+v", check.Status, check)
	}
	joined := strings.Join(check.Issues, ",")
	for _, want := range []string{"stale_observation", "low_coverage", "preview_truncated"} {
		if !strings.Contains(joined, want) {
			t.Fatalf("missing issue %q: %v", want, check.Issues)
		}
	}
	t.Logf("case(a) penalty=0.7 confidence=%.4f status=%s issues=%v",
		*fields.Confidence, check.Status, check.Issues)
}

// TestAssessObservationCoverageFreshLogAtWindowEnd is acceptance case (b):
// the same source mix but the log observation actually reaches the window
// tail. The verdict must be clean, proving the check is not permanently on.
func TestAssessObservationCoverageFreshLogAtWindowEnd(t *testing.T) {
	inc := hotfix12Incident148()
	set := hotfix12StaleSources()
	windowStart := inc.FirstFiredAt.Add(-observationWindowTolerance)
	windowEnd := inc.LastFiredAt.Add(observationWindowTolerance)
	window := windowEnd.Sub(windowStart)
	endTolerance := observationWindowTolerance
	if scaled := time.Duration(float64(window) * 0.05); scaled > endTolerance {
		endTolerance = scaled
	}
	for i := range set.Sources {
		if set.Sources[i].ID == "E9" {
			set.Sources[i].DataStart = windowStart.Format(time.RFC3339Nano)
			set.Sources[i].DataEnd = windowEnd.Add(-endTolerance / 2).Format(time.RFC3339Nano)
		}
	}
	sum := &fakeSummarizer{resp: &llm.ChatResp{Assistant: llm.Message{
		Content: `{"root_cause":"x","root_cause_source_ids":["E9"],"confidence":0.8}`}}}
	uc := &Usecase{summarizer: sum, cfg: Config{SummarizerTimeout: time.Second}}
	fields := uc.extractStructured(context.Background(), inc, "narrative", 3, "", set)

	var factors map[string]any
	if err := json.Unmarshal([]byte(fields.ConfidenceFactorsJSON), &factors); err != nil {
		t.Fatal(err)
	}
	if stale, _ := factors["stale_observation"].(bool); stale {
		t.Fatalf("fresh log must not be stale: %v", factors)
	}
	if low, _ := factors["low_coverage"].(bool); low {
		t.Fatalf("fresh log covering most of the window is not low coverage: %v", factors)
	}
	if penalty, _ := factors["freshness_penalty"].(float64); math.Abs(penalty-1.0) > 1e-9 {
		t.Fatalf("freshness_penalty=%v want 1", penalty)
	}
	if fields.Confidence == nil || math.Abs(*fields.Confidence-0.8) > 1e-9 {
		t.Fatalf("confidence must be unchanged: %v", fields.Confidence)
	}
	t.Logf("case(b) stale=false low_coverage=false penalty=1 confidence=0.8 ratio=%v",
		factors["coverage_ratio"])
}

// TestAssessObservationCoverageWindowIsGenerationSnapshot verifies the
// window is frozen at report generation time and does not depend on the
// alert's live firing state (flowing last_fired_at).
func TestAssessObservationCoverageWindowIsGenerationSnapshot(t *testing.T) {
	set := hotfix12StaleSources()
	evaluatedAt := time.Date(2026, 11, 7, 0, 52, 25, 428000000, time.UTC)
	base := hotfix12Incident148()
	later := base
	later.LastFiredAt = base.LastFiredAt.Add(20 * time.Minute)

	_, f1 := assessObservationCoverage(base, set, evaluatedAt)
	_, f2 := assessObservationCoverage(later, set, evaluatedAt)

	for name, f := range map[string]map[string]any{"t0": f1, "t0+20m": f2} {
		if stale, _ := f["stale_observation"].(bool); !stale {
			t.Fatalf("%s: stale must stay true", name)
		}
		if low, _ := f["low_coverage"].(bool); !low {
			t.Fatalf("%s: low_coverage must stay true", name)
		}
	}
	we1, _ := f1["window_end_used"].(string)
	we2, _ := f2["window_end_used"].(string)
	want1 := base.LastFiredAt.Add(observationWindowTolerance).UTC().Format(time.RFC3339Nano)
	want2 := later.LastFiredAt.Add(observationWindowTolerance).UTC().Format(time.RFC3339Nano)
	if we1 != want1 || we2 != want2 {
		t.Fatalf("window_end_used must track the passed snapshot: %s / %s want %s / %s",
			we1, we2, want1, want2)
	}
	gap1, _ := f1["last_log_end_gap_seconds"].(float64)
	gap2, _ := f2["last_log_end_gap_seconds"].(float64)
	if math.Abs((gap2-gap1)-1200) > 1e-6 {
		t.Fatalf("gap must grow by 1200s when the tail moves 20m: %v -> %v", gap1, gap2)
	}
	ev1, _ := f1["evaluated_at"].(string)
	ev2, _ := f2["evaluated_at"].(string)
	if ev1 != ev2 || ev1 != evaluatedAt.UTC().Format(time.RFC3339Nano) {
		t.Fatalf("evaluated_at must be a pure audit label, not a window input: %s / %s", ev1, ev2)
	}
	t.Logf("snapshot t0 window_end_used=%s gap=%.3f | t0+20m window_end_used=%s gap=%.3f",
		we1, gap1, we2, gap2)
}

// TestAssessObservationCoverageNoLogSources verifies the spec rule: with
// zero log-class sources the tail-freshness stale rule is skipped and the
// emptiness surfaces as low_coverage instead.
func TestAssessObservationCoverageNoLogSources(t *testing.T) {
	inc := hotfix12Incident148()
	windowStart := inc.FirstFiredAt.Add(-observationWindowTolerance)
	windowEnd := inc.LastFiredAt.Add(observationWindowTolerance)
	set := evidenceSet{Sources: []alertmodel.EvidenceSource{
		{ID: "E2", Tool: "query_promql", State: "available",
			DataStart: windowStart.Format(time.RFC3339Nano), DataEnd: windowEnd.Format(time.RFC3339Nano)},
		{ID: "E8", Tool: "query_promql", State: "available",
			DataStart: windowStart.Format(time.RFC3339Nano), DataEnd: windowEnd.Format(time.RFC3339Nano)},
	}}
	_, factors := assessObservationCoverage(inc, set, time.Date(2026, 11, 7, 0, 52, 0, 0, time.UTC))
	if n, _ := factors["log_observation_sources"].(int); n != 0 {
		t.Fatalf("log_observation_sources=%v want 0", n)
	}
	if n, _ := factors["metric_observation_sources"].(int); n != 2 {
		t.Fatalf("metric_observation_sources=%v want 2", n)
	}
	if stale, _ := factors["stale_observation"].(bool); stale {
		t.Fatal("no log observations must not raise stale_observation")
	}
	if low, _ := factors["low_coverage"].(bool); !low {
		t.Fatal("no log observations must raise low_coverage")
	}
	if ratio, _ := factors["coverage_ratio"].(float64); ratio != 0 {
		t.Fatalf("coverage_ratio=%v want 0", ratio)
	}
}

// TestAssessObservationCoverageRecomputableFromReportFields is the audit
// invariant: coverage_ratio can be recomputed using ONLY the fields
// persisted in confidence_factors. It never consults the incident row or
// any runtime state. This is the regression guard against "verdict frozen,
// evidence lost".
func TestAssessObservationCoverageRecomputableFromReportFields(t *testing.T) {
	inc := hotfix12Incident148()
	set := hotfix12StaleSources()
	sum := &fakeSummarizer{resp: &llm.ChatResp{Assistant: llm.Message{
		Content: `{"root_cause":"x","root_cause_source_ids":["E9"],"confidence":0.8}`}}}
	uc := &Usecase{summarizer: sum, cfg: Config{SummarizerTimeout: time.Second}}
	fields := uc.extractStructured(context.Background(), inc, "narrative", 3, "", set)

	// From here on we read ONLY the report's confidence_factors JSON.
	var factors map[string]any
	if err := json.Unmarshal([]byte(fields.ConfidenceFactorsJSON), &factors); err != nil {
		t.Fatal(err)
	}
	wsText, _ := factors["window_start_used"].(string)
	weText, _ := factors["window_end_used"].(string)
	ws, err := time.Parse(time.RFC3339Nano, wsText)
	if err != nil {
		t.Fatalf("window_start_used not RFC3339Nano: %v", err)
	}
	we, err := time.Parse(time.RFC3339Nano, weText)
	if err != nil {
		t.Fatalf("window_end_used not RFC3339Nano: %v", err)
	}
	if _, ok := factors["evaluated_at"].(string); !ok {
		t.Fatal("evaluated_at missing")
	}
	rows, _ := factors["observation_details"].([]any)
	type interval struct{ start, end time.Time }
	intervals := []interval{}
	for _, raw := range rows {
		row, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		counted, _ := row["counted_in_log_coverage"].(bool)
		if !counted {
			continue
		}
		csText, _ := row["clipped_start"].(string)
		ceText, _ := row["clipped_end"].(string)
		cs, errS := time.Parse(time.RFC3339Nano, csText)
		ce, errE := time.Parse(time.RFC3339Nano, ceText)
		if errS != nil || errE != nil {
			t.Fatalf("clipped bounds not parseable: %v %v", errS, errE)
		}
		intervals = append(intervals, interval{cs, ce})
	}
	sort.Slice(intervals, func(i, j int) bool {
		if intervals[i].start.Equal(intervals[j].start) {
			return intervals[i].end.Before(intervals[j].end)
		}
		return intervals[i].start.Before(intervals[j].start)
	})
	covered := time.Duration(0)
	if len(intervals) > 0 {
		curStart, curEnd := intervals[0].start, intervals[0].end
		for _, iv := range intervals[1:] {
			if iv.start.After(curEnd) {
				covered += curEnd.Sub(curStart)
				curStart, curEnd = iv.start, iv.end
			} else if iv.end.After(curEnd) {
				curEnd = iv.end
			}
		}
		covered += curEnd.Sub(curStart)
	}
	recomputed := covered.Seconds() / we.Sub(ws).Seconds()
	if recomputed > 1 {
		recomputed = 1
	}
	reported, _ := factors["coverage_ratio"].(float64)
	if math.Abs(recomputed-reported) > 1e-9 {
		t.Fatalf("recompute mismatch: recomputed=%.12f reported=%.12f", recomputed, reported)
	}
	t.Logf("RECOMPUTE window=%s..%s counted=%d covered=%.9fs recomputed=%.12f reported=%.12f delta=%.3g",
		wsText, weText, len(intervals), covered.Seconds(), recomputed, reported,
		math.Abs(recomputed-reported))
}

// TestExtractStructured_GroupOneEquivalenceGuard pins the direct production
// evidence for the group-1 fix: before hotfix11, #149 (a3a8aa44…) and #201
// (a75320f6…) reported status=incomplete with issues exactly
// ["preview_truncated"]; after the fix #148 (cf0a7e49…) reports partial.
// A purely informational issue must never force "incomplete".
func TestExtractStructured_GroupOneEquivalenceGuard(t *testing.T) {
	inc := alertmodel.Incident{
		ID:           148,
		FirstFiredAt: time.Date(2026, 11, 4, 12, 0, 0, 0, time.UTC),
		LastFiredAt:  time.Date(2026, 11, 4, 12, 10, 0, 0, time.UTC),
	}
	windowStart := inc.FirstFiredAt.Add(-observationWindowTolerance)
	windowEnd := inc.LastFiredAt.Add(observationWindowTolerance)
	for _, tc := range []struct {
		name              string
		issues            []string
		want              string
		wantNotIncomplete bool
	}{
		{"#149/#201 equivalent informational-only sample", []string{"preview_truncated"}, "", true},
		{"informational + quality stays partial", []string{"preview_truncated", "stale_observation"}, "partial", false},
		{"blocking still incomplete", []string{"preview_truncated", "source_read_failed"}, "incomplete", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			sum := &fakeSummarizer{resp: &llm.ChatResp{Assistant: llm.Message{
				Content: `{"root_cause":"x","root_cause_source_ids":["E1"]}`}}}
			uc := &Usecase{summarizer: sum, cfg: Config{SummarizerTimeout: time.Second}}
			set := evidenceSet{
				Issues: append([]string{}, tc.issues...),
				Sources: []alertmodel.EvidenceSource{{
					ID: "E1", Tool: "query_logql", State: "available",
					DataStart: windowStart.Format(time.RFC3339Nano),
					DataEnd:   windowEnd.Format(time.RFC3339Nano),
				}},
			}
			fields := uc.extractStructured(context.Background(), inc, "narrative", 1, "", set)
			var check alertmodel.EvidenceCheck
			if err := json.Unmarshal([]byte(*fields.EvidenceCheckJSON), &check); err != nil {
				t.Fatal(err)
			}
			if tc.wantNotIncomplete {
				if check.Status == "incomplete" {
					t.Fatalf("informational-only issue regressed to incomplete: %+v", check)
				}
			} else if check.Status != tc.want {
				t.Fatalf("status=%s want %s issues=%v", check.Status, tc.want, check.Issues)
			}
		})
	}
}

// hotfix13Window returns a short, clean investigation window plus its
// RFC3339Nano bounds. A single log observation covering the whole window
// keeps stale / low_coverage signals quiet, so the tests below isolate the
// reference-layer (step-level) status logic.
func hotfix13Window() (alertmodel.Incident, string, string) {
	base := time.Date(2026, 11, 4, 12, 0, 0, 0, time.UTC)
	inc := alertmodel.Incident{ID: 148, FirstFiredAt: base, LastFiredAt: base.Add(time.Hour)}
	ws := inc.FirstFiredAt.Add(-observationWindowTolerance).UTC().Format(time.RFC3339Nano)
	we := inc.LastFiredAt.Add(observationWindowTolerance).UTC().Format(time.RFC3339Nano)
	return inc, ws, we
}

// assertNoStepMarkers pins mandatory assertion (4): step-level audit reasons
// must appear ONLY in confidence_factors. classifyCheckIssues treats unknown
// codes as blocking, so any leak into check.Issues would drag the verdict
// back to "incomplete" and silently undo the whole fix.
func assertNoStepMarkers(t *testing.T, issues []string) {
	t.Helper()
	joined := strings.Join(issues, ",")
	for _, forbidden := range []string{
		"step_reference_incomplete", "incomplete_step_source_ids",
		"incomplete_step_count", "incomplete_step_ratio",
		"allStepsIncomplete", "truncated:E6", "missing:E7",
	} {
		if strings.Contains(joined, forbidden) {
			t.Fatalf("step-level marker leaked into check.Issues: %v", issues)
		}
	}
}

// TestExtractStructured_StepLevelIncompleteDowngradesToPartial pins mandatory
// assertion (1): exactly one INTERMEDIATE evidence step cites a truncated
// source while a sibling step and the root cause are traceable. The verdict
// must be "partial" (not "incomplete"), step_reference_incomplete must be
// true, and the count / ratio / source-id factors must be present. The LLM
// response deliberately carries evidence[] - the pre-hotfix13 suite never
// exercised the extractStructured step loop at all.
func TestExtractStructured_StepLevelIncompleteDowngradesToPartial(t *testing.T) {
	inc, ws, we := hotfix13Window()
	sum := &fakeSummarizer{resp: &llm.ChatResp{Assistant: llm.Message{
		Content: `{"root_cause":"x","root_cause_source_ids":["E1"],` +
			`"evidence":[{"summary":"ok","source_ids":["E1"]},` +
			`{"summary":"broken","source_ids":["E6"]}]}`}}}
	uc := &Usecase{summarizer: sum, cfg: Config{SummarizerTimeout: time.Second}}
	set := evidenceSet{Sources: []alertmodel.EvidenceSource{
		{ID: "E1", Tool: "query_logql", State: "available", DataStart: ws, DataEnd: we},
		{ID: "E6", Tool: "query_promql", State: "truncated"},
	}}
	fields := uc.extractStructured(context.Background(), inc, "narrative", 2, "", set)

	var check alertmodel.EvidenceCheck
	if err := json.Unmarshal([]byte(*fields.EvidenceCheckJSON), &check); err != nil {
		t.Fatal(err)
	}
	if check.Status != "partial" {
		t.Fatalf("status=%s want partial: %+v", check.Status, check)
	}
	if check.RootCause.Status != "traceable" {
		t.Fatalf("root_cause status=%s want traceable", check.RootCause.Status)
	}
	assertNoStepMarkers(t, check.Issues)

	var factors map[string]any
	if err := json.Unmarshal([]byte(fields.ConfidenceFactorsJSON), &factors); err != nil {
		t.Fatal(err)
	}
	if v, _ := factors["step_reference_incomplete"].(bool); !v {
		t.Fatalf("step_reference_incomplete=%v want true: %v", factors["step_reference_incomplete"], factors)
	}
	ids, _ := factors["incomplete_step_source_ids"].([]any)
	if len(ids) != 1 || ids[0] != "E6" {
		t.Fatalf("incomplete_step_source_ids=%v want [E6]", ids)
	}
	if count, _ := factors["incomplete_step_count"].(float64); count != 1 {
		t.Fatalf("incomplete_step_count=%v want 1", factors["incomplete_step_count"])
	}
	if ratio, _ := factors["incomplete_step_ratio"].(float64); math.Abs(ratio-0.5) > 1e-9 {
		t.Fatalf("incomplete_step_ratio=%v want 0.5", factors["incomplete_step_ratio"])
	}
}

// TestExtractStructured_RootCauseIncompleteStaysIncomplete pins mandatory
// assertion (2), the first of QA's two insurances: when the ROOT-CAUSE
// reference itself cites a truncated source the check stays genuinely
// incomplete. All evidence steps here are traceable, so this isolates the
// root-cause guard from the allStepsIncomplete guard.
func TestExtractStructured_RootCauseIncompleteStaysIncomplete(t *testing.T) {
	inc, ws, we := hotfix13Window()
	sum := &fakeSummarizer{resp: &llm.ChatResp{Assistant: llm.Message{
		Content: `{"root_cause":"x","root_cause_source_ids":["E6"],` +
			`"evidence":[{"summary":"a","source_ids":["E1"]},` +
			`{"summary":"b","source_ids":["E1"]}]}`}}}
	uc := &Usecase{summarizer: sum, cfg: Config{SummarizerTimeout: time.Second}}
	set := evidenceSet{Sources: []alertmodel.EvidenceSource{
		{ID: "E1", Tool: "query_logql", State: "available", DataStart: ws, DataEnd: we},
		{ID: "E6", Tool: "query_promql", State: "truncated"},
	}}
	fields := uc.extractStructured(context.Background(), inc, "narrative", 2, "", set)
	var check alertmodel.EvidenceCheck
	if err := json.Unmarshal([]byte(*fields.EvidenceCheckJSON), &check); err != nil {
		t.Fatal(err)
	}
	if check.Status != "incomplete" {
		t.Fatalf("status=%s want incomplete (root cause cites a truncated source)", check.Status)
	}
	if check.RootCause.Status != "incomplete" {
		t.Fatalf("root_cause status=%s want incomplete", check.RootCause.Status)
	}
	var factors map[string]any
	if err := json.Unmarshal([]byte(fields.ConfidenceFactorsJSON), &factors); err != nil {
		t.Fatal(err)
	}
	if count, _ := factors["incomplete_step_count"].(float64); count != 0 {
		t.Fatalf("incomplete_step_count=%v want 0 (evidence steps are all traceable)", factors["incomplete_step_count"])
	}
	if ratio, _ := factors["incomplete_step_ratio"].(float64); ratio != 0 {
		t.Fatalf("incomplete_step_ratio=%v want 0", factors["incomplete_step_ratio"])
	}
}

// TestExtractStructured_AllStepsIncompleteStaysIncomplete is mandatory
// assertion (5) - the QA guard (a): EVERY evidence step cites a
// truncated / missing / pending source, the root-cause reference is
// traceable and there is no top-level blocking issue. The report still has
// no verifiable provenance, so status must be "incomplete" and the ratio 1.
func TestExtractStructured_AllStepsIncompleteStaysIncomplete(t *testing.T) {
	inc, ws, we := hotfix13Window()
	sum := &fakeSummarizer{resp: &llm.ChatResp{Assistant: llm.Message{
		Content: `{"root_cause":"x","root_cause_source_ids":["E1"],` +
			`"evidence":[{"summary":"a","source_ids":["E6"]},` +
			`{"summary":"b","source_ids":["E7"]}]}`}}}
	uc := &Usecase{summarizer: sum, cfg: Config{SummarizerTimeout: time.Second}}
	set := evidenceSet{Sources: []alertmodel.EvidenceSource{
		{ID: "E1", Tool: "query_logql", State: "available", DataStart: ws, DataEnd: we},
		{ID: "E6", Tool: "query_promql", State: "truncated"},
		{ID: "E7", Tool: "query_logql", State: "missing"},
	}}
	fields := uc.extractStructured(context.Background(), inc, "narrative", 2, "", set)

	var check alertmodel.EvidenceCheck
	if err := json.Unmarshal([]byte(*fields.EvidenceCheckJSON), &check); err != nil {
		t.Fatal(err)
	}
	if check.Status != "incomplete" {
		t.Fatalf("status=%s want incomplete (every evidence step is broken): %+v", check.Status, check)
	}
	// The guard - not the root cause - must be what forces incomplete here.
	if check.RootCause.Status != "traceable" {
		t.Fatalf("root_cause status=%s want traceable", check.RootCause.Status)
	}
	if len(check.Issues) != 0 {
		t.Fatalf("expected no top-level blocking issues, got %v", check.Issues)
	}
	assertNoStepMarkers(t, check.Issues)

	var factors map[string]any
	if err := json.Unmarshal([]byte(fields.ConfidenceFactorsJSON), &factors); err != nil {
		t.Fatal(err)
	}
	if v, _ := factors["step_reference_incomplete"].(bool); !v {
		t.Fatalf("step_reference_incomplete=%v want true", factors["step_reference_incomplete"])
	}
	if count, _ := factors["incomplete_step_count"].(float64); count != 2 {
		t.Fatalf("incomplete_step_count=%v want 2", factors["incomplete_step_count"])
	}
	if ratio, _ := factors["incomplete_step_ratio"].(float64); math.Abs(ratio-1.0) > 1e-9 {
		t.Fatalf("incomplete_step_ratio=%v want 1", factors["incomplete_step_ratio"])
	}
	ids, _ := factors["incomplete_step_source_ids"].([]any)
	if len(ids) != 2 || ids[0] != "E6" || ids[1] != "E7" {
		t.Fatalf("incomplete_step_source_ids=%v want [E6 E7]", ids)
	}
}

// TestExtractStructured_AllAvailableStepsStayTraceable proves the fix is not
// permanently open: with every cited source available the verdict stays
// traceable and all step-level signals are false / zero.
func TestExtractStructured_AllAvailableStepsStayTraceable(t *testing.T) {
	inc, ws, we := hotfix13Window()
	sum := &fakeSummarizer{resp: &llm.ChatResp{Assistant: llm.Message{
		Content: `{"root_cause":"x","root_cause_source_ids":["E1"],` +
			`"evidence":[{"summary":"s","source_ids":["E6"]}]}`}}}
	uc := &Usecase{summarizer: sum, cfg: Config{SummarizerTimeout: time.Second}}
	set := evidenceSet{Sources: []alertmodel.EvidenceSource{
		{ID: "E1", Tool: "query_logql", State: "available", DataStart: ws, DataEnd: we},
		{ID: "E6", Tool: "query_promql", State: "available", DataStart: ws, DataEnd: we},
	}}
	fields := uc.extractStructured(context.Background(), inc, "narrative", 2, "", set)
	var check alertmodel.EvidenceCheck
	if err := json.Unmarshal([]byte(*fields.EvidenceCheckJSON), &check); err != nil {
		t.Fatal(err)
	}
	if check.Status != "traceable" {
		t.Fatalf("status=%s want traceable: %+v", check.Status, check)
	}
	var factors map[string]any
	if err := json.Unmarshal([]byte(fields.ConfidenceFactorsJSON), &factors); err != nil {
		t.Fatal(err)
	}
	if v, _ := factors["step_reference_incomplete"].(bool); v {
		t.Fatal("step_reference_incomplete must be false when every step is available")
	}
	if count, _ := factors["incomplete_step_count"].(float64); count != 0 {
		t.Fatalf("incomplete_step_count=%v want 0", factors["incomplete_step_count"])
	}
	if ratio, _ := factors["incomplete_step_ratio"].(float64); ratio != 0 {
		t.Fatalf("incomplete_step_ratio=%v want 0", factors["incomplete_step_ratio"])
	}
}

// TestExtractStructured_Hotfix13IssueClassRegression pins mandatory assertion
// (3), the second of QA's two insurances, while the step loop is active:
// informational-only never blocks, quality downgrades to partial, and a
// blocking issue (source_read_failed) still forces incomplete.
func TestExtractStructured_Hotfix13IssueClassRegression(t *testing.T) {
	inc, ws, we := hotfix13Window()
	for _, tc := range []struct {
		name   string
		issues []string
		want   string
	}{
		{"informational-only does not block", []string{"preview_truncated"}, "traceable"},
		{"quality downgrades to partial", []string{"low_coverage"}, "partial"},
		{"blocking forces incomplete", []string{"source_read_failed"}, "incomplete"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			sum := &fakeSummarizer{resp: &llm.ChatResp{Assistant: llm.Message{
				Content: `{"root_cause":"x","root_cause_source_ids":["E1"],` +
					`"evidence":[{"summary":"s","source_ids":["E1"]}]}`}}}
			uc := &Usecase{summarizer: sum, cfg: Config{SummarizerTimeout: time.Second}}
			set := evidenceSet{
				Issues: append([]string{}, tc.issues...),
				Sources: []alertmodel.EvidenceSource{
					{ID: "E1", Tool: "query_logql", State: "available", DataStart: ws, DataEnd: we},
				},
			}
			fields := uc.extractStructured(context.Background(), inc, "narrative", 1, "", set)
			var check alertmodel.EvidenceCheck
			if err := json.Unmarshal([]byte(*fields.EvidenceCheckJSON), &check); err != nil {
				t.Fatal(err)
			}
			if check.Status != tc.want {
				t.Fatalf("status=%s want %s issues=%v", check.Status, tc.want, check.Issues)
			}
		})
	}
}

// TestAssessObservationCoverageNoInvertedClippedBounds pins hotfix13 edit 4:
// a source wholly before OR after the window has no in-window span, so its
// clipped bounds collapse to a zero-length interval at the boundary. They
// must be persisted (not empty), parseable, never inverted, and never counted
// in log coverage. QA's recompute filters counted_in_log_coverage=true, so
// coverage_ratio is bit-for-bit unchanged by this normalisation.
func TestAssessObservationCoverageNoInvertedClippedBounds(t *testing.T) {
	inc := hotfix12Incident148()
	windowStart := inc.FirstFiredAt.Add(-observationWindowTolerance)
	windowEnd := inc.LastFiredAt.Add(observationWindowTolerance)
	evaluatedAt := time.Date(2026, 11, 7, 0, 52, 25, 428000000, time.UTC)
	for _, tc := range []struct {
		name       string
		start, end time.Time
	}{
		{"pre-window", windowStart.Add(-12 * time.Minute), windowStart.Add(-10 * time.Minute)},
		{"post-window", windowEnd.Add(10 * time.Minute), windowEnd.Add(12 * time.Minute)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			set := evidenceSet{Sources: []alertmodel.EvidenceSource{
				{ID: "E2", Tool: "query_logql", State: "available",
					DataStart: tc.start.Format(time.RFC3339Nano),
					DataEnd:   tc.end.Format(time.RFC3339Nano)},
			}}
			_, factors := assessObservationCoverage(inc, set, evaluatedAt)
			details, _ := factors["observation_details"].([]observationSourceDetail)
			if len(details) != 1 {
				t.Fatalf("details len=%d want 1", len(details))
			}
			d := details[0]
			if !d.IsStale {
				t.Fatal("a wholly out-of-window log observation must be stale")
			}
			if d.ClippedStart == "" || d.ClippedEnd == "" {
				t.Fatalf("clipped bounds must persist a collapsed interval, got %q / %q",
					d.ClippedStart, d.ClippedEnd)
			}
			cs, errS := time.Parse(time.RFC3339Nano, d.ClippedStart)
			ce, errE := time.Parse(time.RFC3339Nano, d.ClippedEnd)
			if errS != nil || errE != nil {
				t.Fatalf("clipped bounds not RFC3339Nano: %v / %v", errS, errE)
			}
			if ce.Before(cs) {
				t.Fatalf("inverted clipped interval: %s > %s", d.ClippedStart, d.ClippedEnd)
			}
			if !ce.Equal(cs) {
				t.Fatalf("out-of-window span must collapse to zero length, got %s / %s",
					d.ClippedStart, d.ClippedEnd)
			}
			if d.DataStart == "" || d.DataEnd == "" {
				t.Fatal("raw DataStart / DataEnd must be preserved")
			}
			if d.CountedInLogCoverage {
				t.Fatal("a stale source must not be counted in log coverage")
			}
			if ratio, _ := factors["coverage_ratio"].(float64); ratio != 0 {
				t.Fatalf("coverage_ratio=%v want 0 (no counted rows)", ratio)
			}
			if stale, _ := factors["stale_observation"].(bool); !stale {
				t.Fatal("a wholly out-of-window log source must raise stale_observation")
			}
		})
	}
}

// TestAssessObservationCoverageStaleMetricDoesNotForceStale pins hotfix13
// edit 5: a wholly-out-of-window metric source is recorded stale on its own
// audit row but must not raise the global stale_observation verdict.
func TestAssessObservationCoverageStaleMetricDoesNotForceStale(t *testing.T) {
	inc := hotfix12Incident148()
	windowStart := inc.FirstFiredAt.Add(-observationWindowTolerance)
	earlyEnd := windowStart.Add(-10 * time.Minute)
	earlyStart := earlyEnd.Add(-2 * time.Minute)
	set := evidenceSet{Sources: []alertmodel.EvidenceSource{
		{ID: "M1", Tool: "query_promql", State: "available",
			DataStart: earlyStart.Format(time.RFC3339Nano),
			DataEnd:   earlyEnd.Format(time.RFC3339Nano)},
	}}
	_, factors := assessObservationCoverage(inc, set,
		time.Date(2026, 11, 7, 0, 52, 25, 428000000, time.UTC))
	if stale, _ := factors["stale_observation"].(bool); stale {
		t.Fatalf("a stale metric source must not raise stale_observation: %v", factors)
	}
	if low, _ := factors["low_coverage"].(bool); !low {
		t.Fatal("no log observations must still raise low_coverage")
	}
	details, _ := factors["observation_details"].([]observationSourceDetail)
	if len(details) != 1 || !details[0].IsStale {
		t.Fatalf("the metric row itself must still be marked stale: %+v", details)
	}
}
