package alert

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	model "github.com/ongridio/ongrid/internal/manager/model/alert"
	"github.com/ongridio/ongrid/internal/pkg/notify"
)

// scrapeDownIncident builds the persisted incident the operator reported:
// a monitoring_pipeline scrape_down with device_id only in the labels.
func scrapeDownIncident(summary string, at time.Time) *model.Incident {
	return &model.Incident{
		ID:           42,
		Rule:         "scrape_down",
		RuleName:     "Scrape Down",
		Severity:     string(notify.SeverityWarning),
		Status:       model.IncidentStatusOpen,
		ScopeType:    model.RuleScopeMonitoringPipeline,
		Summary:      summary,
		DedupeKey:    wantScrapeDownDedupe,
		LabelsJSON:   `{"device_id":"28","kind":"custom","plugin":"custommetrics","target_id":"autoapm","target_name":"autoapm"}`,
		FirstFiredAt: at.Add(-time.Minute),
		LastFiredAt:  at.Add(-time.Second),
	}
}

// TestBuildIncidentMessage_ScrapeDownReadable is the retry-path regression:
// buildIncidentMessage must render the same readable message (Chinese title,
// 对象/说明/详情, source + dedupe) as the first-attempt path, not the raw
// English technical summary.
func TestBuildIncidentMessage_ScrapeDownReadable(t *testing.T) {
	at := time.Date(2026, 5, 2, 12, 0, 0, 0, time.UTC)
	summary := "scrape_down: up == 0 ⇒ device_id=28,kind=custom,plugin=custommetrics,target_id=autoapm,target_name=autoapm (value=0)"
	incident := scrapeDownIncident(summary, at)

	msg := buildIncidentMessage(incident, at)

	if msg.Subject != "监控采集目标不可达" {
		t.Errorf("subject = %q, want the built-in Chinese title", msg.Subject)
	}
	for _, want := range []string{
		"对象：autoapm（自定义指标采集）",
		"说明：该采集目标已无法拉取",
		"详情：" + summary, // no resolver on retry → raw device_id kept verbatim
	} {
		if !strings.Contains(msg.Body, want) {
			t.Errorf("body missing %q:\n%s", want, msg.Body)
		}
	}
	// No resolver is wired on the retry path: no phantom host, no empty
	// device= substitution — the raw device_id stays for troubleshooting.
	if strings.Contains(msg.Body, "device=") {
		t.Errorf("body has an empty device= substitution: %q", msg.Body)
	}
	if objectLine, _, _ := strings.Cut(msg.Body, "\n"); strings.Contains(objectLine, "主机") {
		t.Errorf("object line claims a host that was never resolved: %q", objectLine)
	}
	if msg.Source != model.RuleScopeMonitoringPipeline {
		t.Errorf("source = %q, want monitoring_pipeline", msg.Source)
	}
	if msg.DedupeKey != incident.DedupeKey {
		t.Errorf("dedupe = %q, want %q (verbatim)", msg.DedupeKey, incident.DedupeKey)
	}
	if msg.Severity != notify.SeverityWarning {
		t.Errorf("severity = %q, want warning (routing must not change)", msg.Severity)
	}
	if !msg.OccurredAt.Equal(incident.LastFiredAt) {
		t.Errorf("occurred_at = %v, want %v", msg.OccurredAt, incident.LastFiredAt)
	}
	if msg.Labels["rule"] != "scrape_down" || msg.Labels["incident_id"] != "42" || msg.Labels["device_id"] != "28" {
		t.Errorf("labels = %#v", msg.Labels)
	}
	for _, key := range []string{"device_hostname", "device_ip"} {
		if v, ok := msg.Labels[key]; ok {
			t.Errorf("label %q = %q set without a resolved identity", key, v)
		}
	}

	// End-to-end through the real DingTalk formatter: the source: / dedupe:
	// lines (and the "dedupe" keyword) must survive the retry render.
	var payload map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Errorf("decode dingtalk payload: %v", err)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()
	if err := notify.NewDingTalkSender("dingtalk", srv.URL, "", srv.Client()).Send(context.Background(), msg); err != nil {
		t.Fatalf("DingTalk Send: %v", err)
	}
	text, _ := payload["text"].(map[string]any)
	content, _ := text["content"].(string)
	t.Logf("rendered retry message:\n%s", content)
	lines := strings.Split(content, "\n")
	if lines[0] != "[警告] 监控采集目标不可达" {
		t.Errorf("first line = %q", lines[0])
	}
	if len(lines) < 2 || lines[len(lines)-2] != "source: monitoring_pipeline" || lines[len(lines)-1] != "dedupe: "+incident.DedupeKey {
		t.Errorf("trailing source/dedupe lines changed: %#v", lines)
	}
	if !strings.Contains(content, "dedupe") {
		t.Fatal("DingTalk keyword 'dedupe' missing on the retry message")
	}
}

// TestBuildIncidentMessage_Degrades proves the retry renderer never panics,
// never emits an empty device=, and always produces a deliverable message
// when there is no resolver and/or no device_id to work with.
func TestBuildIncidentMessage_Degrades(t *testing.T) {
	at := time.Date(2026, 5, 2, 12, 0, 0, 0, time.UTC)
	hostDevice := uint64(7)
	cases := []struct {
		name     string
		incident *model.Incident
	}{
		{
			name: "no device, no labels, empty summary and title",
			incident: &model.Incident{
				ID: 1, Rule: "my_rule", RuleName: "My rule", Severity: "warning",
				Status: model.IncidentStatusOpen, DedupeKey: "pipeline:my_rule:_",
			},
		},
		{
			name: "labels present but no device_id",
			incident: &model.Incident{
				ID: 2, Rule: "scrape_down", RuleName: "Scrape Down", Severity: "warning",
				Status: model.IncidentStatusOpen, DedupeKey: wantScrapeDownDedupe,
				Summary:    "scrape_down: up == 0 (value=0)",
				LabelsJSON: `{"kind":"custom","plugin":"custommetrics","target_name":"autoapm"}`,
			},
		},
		{
			name: "host-scope device_id without a resolver",
			incident: &model.Incident{
				ID: 3, Rule: "cpu_high", RuleName: "CPU 高负载", Severity: "warning",
				Status: model.IncidentStatusOpen, DedupeKey: "host:7:cpu_high",
				Summary:  "cpu_high: device_cpu_usage_percent > 80 ⇒ device_id=7 (value=91)",
				DeviceID: &hostDevice,
			},
		},
		{
			name: "malformed labels json",
			incident: &model.Incident{
				ID: 4, Rule: "scrape_down", RuleName: "Scrape Down", Severity: "warning",
				Status: model.IncidentStatusOpen, DedupeKey: wantScrapeDownDedupe,
				Summary: "scrape_down: up == 0 (value=0)", LabelsJSON: `{not json`,
			},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			defer func() {
				if r := recover(); r != nil {
					t.Fatalf("buildIncidentMessage panicked: %v", r)
				}
			}()
			msg := buildIncidentMessage(tc.incident, at)
			if strings.TrimSpace(msg.Subject) == "" {
				t.Fatalf("empty subject: the router would reject this message")
			}
			if strings.TrimSpace(msg.Body) == "" {
				t.Fatalf("empty body")
			}
			if strings.Contains(msg.Body, "device=") {
				t.Errorf("empty/incorrect device= substitution: %q", msg.Body)
			}
			if objectLine, _, _ := strings.Cut(msg.Body, "\n"); strings.Contains(objectLine, "主机") {
				t.Errorf("object line claims a host without a resolved identity: %q", objectLine)
			}
			if msg.DedupeKey != tc.incident.DedupeKey {
				t.Errorf("dedupe = %q, want %q", msg.DedupeKey, tc.incident.DedupeKey)
			}
		})
	}
}

// TestRetryAndFirstAttemptRenderIdentically locks the "no drift" invariant:
// for the same incident, no resolver and the same summary, the retry path
// produces exactly the same Message as the first-attempt path.
func TestRetryAndFirstAttemptRenderIdentically(t *testing.T) {
	at := time.Date(2026, 5, 2, 12, 0, 0, 0, time.UTC)
	summary := "scrape_down: up == 0 ⇒ device_id=28,kind=custom,plugin=custommetrics,target_id=autoapm,target_name=autoapm (value=0)"
	incident := scrapeDownIncident(summary, at)
	// Align the persisted firing time with the first-attempt `at` so the
	// OccurredAt comparison is meaningful (retry uses LastFiredAt).
	incident.LastFiredAt = at

	repo, notifier := newFakeRepo(), &fakeNotifier{}
	eval := newPipelineEvaluator(t, repo, notifier, NewStaticRulesProvider(), PipelineEvaluatorOpts{})
	eval.notify(context.Background(), &FiringResult{Incident: incident}, summary, incident.ScopeType, at)
	if len(notifier.msgs) != 1 {
		t.Fatalf("notifications = %d, want 1", len(notifier.msgs))
	}
	first := notifier.msgs[0]
	retry := buildIncidentMessage(incident, at)

	if first.Subject != retry.Subject {
		t.Errorf("subject drift: first=%q retry=%q", first.Subject, retry.Subject)
	}
	if first.Body != retry.Body {
		t.Errorf("body drift:\nfirst=%q\nretry=%q", first.Body, retry.Body)
	}
	if first.Source != retry.Source || first.DedupeKey != retry.DedupeKey || first.Severity != retry.Severity {
		t.Errorf("metadata drift: first=%+v retry=%+v", first, retry)
	}
	if !first.OccurredAt.Equal(retry.OccurredAt) {
		t.Errorf("occurred_at drift: first=%v retry=%v", first.OccurredAt, retry.OccurredAt)
	}
	if !reflect.DeepEqual(first.Labels, retry.Labels) {
		t.Errorf("labels drift:\nfirst=%#v\nretry=%#v", first.Labels, retry.Labels)
	}
}
