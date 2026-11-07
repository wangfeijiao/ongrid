package alert

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	model "github.com/ongridio/ongrid/internal/manager/model/alert"
	"github.com/ongridio/ongrid/internal/pkg/notify"
)

// QA second-round coverage for the retry-delivery fix. Written independently
// of retry_readable_test.go so the Major I blocked on cannot be "closed" by
// the engineer's own assertions alone.

// wantScrapeDownRetryText is the exact text an operator must receive on the
// retry path for the reported scrape_down alert (no resolver wired there, so
// the 对象 line has no host and 详情 keeps the raw device_id).
const wantScrapeDownRetryText = `[警告] 监控采集目标不可达
对象：autoapm（自定义指标采集）
说明：该采集目标已无法拉取，指标停止上报；请检查采集插件与目标进程。
详情：scrape_down: up == 0 ⇒ device_id=28,kind=custom,plugin=custommetrics,target_id=autoapm,target_name=autoapm (value=0)
source: monitoring_pipeline
dedupe: pipeline:scrape_down:device_id=28,kind=custom,plugin=custommetrics,target_id=autoapm,target_name=autoapm`

// qaRenderDingTalk pushes msg through the real DingTalk sender and returns
// the exact text.content the bot would post.
func qaRenderDingTalk(t *testing.T, msg notify.Message) string {
	t.Helper()
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
	if content == "" {
		t.Fatalf("dingtalk content empty: %#v", payload)
	}
	return content
}

// TestQARetryPathNoLongerRendersRawSummary is the round-1 reproduction rerun
// against HEAD: the retry message must be the readable form, byte for byte,
// and must no longer be the old "[警告] <technical summary>" shape.
func TestQARetryPathNoLongerRendersRawSummary(t *testing.T) {
	at := time.Date(2026, 5, 2, 12, 0, 0, 0, time.UTC)
	summary := "scrape_down: up == 0 ⇒ device_id=28,kind=custom,plugin=custommetrics,target_id=autoapm,target_name=autoapm (value=0)"
	incident := scrapeDownIncident(summary, at)

	msg := buildIncidentMessage(incident, at)

	// The old defect, stated as a negative assertion: Subject must not be the
	// raw technical summary any more.
	if msg.Subject == incident.Summary {
		t.Fatalf("retry Subject is still the raw summary: %q", msg.Subject)
	}
	if !strings.Contains(msg.Subject, "监控采集目标不可达") {
		t.Errorf("retry Subject = %q, want the Chinese title", msg.Subject)
	}
	if strings.TrimSpace(msg.Body) == "" {
		t.Fatalf("retry Body is empty; the 对象/说明/详情 rows are missing")
	}
	if msg.DedupeKey != incident.DedupeKey {
		t.Errorf("dedupe = %q, want %q (verbatim)", msg.DedupeKey, incident.DedupeKey)
	}

	content := qaRenderDingTalk(t, msg)
	if wantScrapeDownRetryText != content {
		t.Errorf("retry render mismatch\n--- got ---\n%s\n--- want ---\n%s", content, wantScrapeDownRetryText)
	}
	if strings.HasPrefix(content, "[警告] scrape_down:") {
		t.Fatalf("retry still renders the raw-defect shape: %q", content)
	}
	// The 详情 row legitimately keeps the raw id (no resolver on retry), but it
	// must never become an empty device= substitution.
	if strings.Contains(content, "device=") {
		t.Errorf("retry render has an empty device= substitution: %q", content)
	}
	if !strings.Contains(content, "source: monitoring_pipeline") {
		t.Errorf("source row lost: %q", content)
	}
	if !strings.Contains(content, "dedupe: "+incident.DedupeKey) {
		t.Errorf("dedupe row not byte-identical to incident.DedupeKey: %q", content)
	}
}

// TestQARetryPreservesLegacyMessageSemantics pins the non-text semantics of
// the pre-fix buildIncidentMessage (main: internal/manager/biz/alert/retry.go)
// so the refactor did not silently change retry routing / cooldown inputs.
func TestQARetryPreservesLegacyMessageSemantics(t *testing.T) {
	at := time.Date(2026, 5, 2, 12, 0, 0, 0, time.UTC)

	t.Run("severity defaults to warning", func(t *testing.T) {
		incident := scrapeDownIncident("scrape_down: up == 0 (value=0)", at)
		incident.Severity = ""
		if got := buildIncidentMessage(incident, at).Severity; got != notify.SeverityWarning {
			t.Errorf("severity = %q, want warning", got)
		}
	})

	t.Run("source is the incident scope type", func(t *testing.T) {
		incident := scrapeDownIncident("scrape_down: up == 0 (value=0)", at)
		if got := buildIncidentMessage(incident, at).Source; got != incident.ScopeType {
			t.Errorf("source = %q, want %q", got, incident.ScopeType)
		}
	})

	t.Run("dedupe key passes through verbatim", func(t *testing.T) {
		incident := scrapeDownIncident("scrape_down: up == 0 (value=0)", at)
		if got := buildIncidentMessage(incident, at).DedupeKey; got != incident.DedupeKey {
			t.Errorf("dedupe = %q, want %q", got, incident.DedupeKey)
		}
	})

	t.Run("occurred_at is last_fired_at", func(t *testing.T) {
		incident := scrapeDownIncident("scrape_down: up == 0 (value=0)", at)
		if got := buildIncidentMessage(incident, at).OccurredAt; !got.Equal(incident.LastFiredAt) {
			t.Errorf("occurred_at = %v, want last_fired_at %v", got, incident.LastFiredAt)
		}
	})

	t.Run("occurred_at falls back to now when last_fired_at is zero", func(t *testing.T) {
		incident := scrapeDownIncident("scrape_down: up == 0 (value=0)", at)
		incident.LastFiredAt = time.Time{}
		if got := buildIncidentMessage(incident, at).OccurredAt; !got.Equal(at) {
			t.Errorf("occurred_at = %v, want now %v", got, at)
		}
	})

	t.Run("summary falls back to title", func(t *testing.T) {
		incident := scrapeDownIncident("", at)
		incident.Summary = ""
		incident.Title = "采集目标 autoapm 不可达"
		msg := buildIncidentMessage(incident, at)
		if !strings.Contains(msg.Body, "详情：采集目标 autoapm 不可达") {
			t.Errorf("title fallback lost from the 详情 row: %q", msg.Body)
		}
	})

	t.Run("control labels come from the incident, not the label blob", func(t *testing.T) {
		incident := scrapeDownIncident("scrape_down: up == 0 (value=0)", at)
		incident.ID = 77
		incident.Rule = "scrape_down"
		incident.LabelsJSON = `{"rule":"spoofed","incident_id":"spoofed","device_id":"28","kind":"custom"}`
		msg := buildIncidentMessage(incident, at)
		if msg.Labels["incident_id"] != "77" {
			t.Errorf("incident_id = %q, want 77 (control label must not be spoofable)", msg.Labels["incident_id"])
		}
		if msg.Labels["rule"] != "scrape_down" {
			t.Errorf("rule = %q, want scrape_down", msg.Labels["rule"])
		}
	})
}

// TestQAIdentityLabelsSurviveSharedRenderer checks the label assembly the
// refactor centralised: all five application-identity labels plus the device
// labels must still reach Message.Labels on BOTH paths, and control labels
// must stay unforgeable. Compares the shared renderer against the label set
// the pre-refactor notify() built.
func TestQAIdentityLabelsSurviveSharedRenderer(t *testing.T) {
	at := time.Date(2026, 5, 2, 12, 0, 0, 0, time.UTC)
	summary := "scrape_down: up == 0 ⇒ device_id=28,kind=custom (value=0)"
	incident := scrapeDownIncident(summary, at)
	incident.LastFiredAt = at
	incident.LabelsJSON = `{"device_id":"28","kind":"custom","plugin":"custommetrics",` +
		`"service":"orders","service_namespace":"trade","deployment_environment_name":"production",` +
		`"service_instance_id":"orders-0","span_name":"GET /orders/{id}",` +
		`"rule":"spoofed","incident_id":"spoofed"}`

	wantIdentity := map[string]string{
		"service":                     "orders",
		"service_namespace":           "trade",
		"deployment_environment_name": "production",
		"service_instance_id":         "orders-0",
		"span_name":                   "GET /orders/{id}",
	}

	repo, notifier := newFakeRepo(), &fakeNotifier{}
	resolver := func(_ context.Context, _ uint64) (DeviceIdentity, error) {
		return DeviceIdentity{Hostname: "i-vaaqvt1i", IPAddress: "10.7.30.49"}, nil
	}
	eval := newPipelineEvaluator(t, repo, notifier, NewStaticRulesProvider(), PipelineEvaluatorOpts{
		DeviceIdentityResolver: resolver,
	})
	eval.notify(context.Background(), &FiringResult{Incident: incident}, summary, incident.ScopeType, at)
	if len(notifier.msgs) != 1 {
		t.Fatalf("notifications = %d, want 1", len(notifier.msgs))
	}

	products := map[string]notify.Message{
		"first-attempt": notifier.msgs[0],
		"retry":         buildIncidentMessage(incident, at),
	}
	for name, msg := range products {
		for key, want := range wantIdentity {
			if msg.Labels[key] != want {
				t.Errorf("%s: identity label %q = %q, want %q", name, key, msg.Labels[key], want)
			}
		}
		if msg.Labels["device_id"] != "28" {
			t.Errorf("%s: device_id = %q, want 28", name, msg.Labels["device_id"])
		}
		// Control labels come from the incident, never the label blob.
		if msg.Labels["incident_id"] != "42" || msg.Labels["rule"] != "scrape_down" {
			t.Errorf("%s: control labels spoofable: %#v", name, msg.Labels)
		}
	}
	// The first attempt resolves the host; the retry cannot and must not fake it.
	if products["first-attempt"].Labels["device_hostname"] != "i-vaaqvt1i" {
		t.Errorf("first-attempt device_hostname = %q", products["first-attempt"].Labels["device_hostname"])
	}
	if products["retry"].Labels["device_hostname"] != "" {
		t.Errorf("retry device_hostname = %q, want empty", products["retry"].Labels["device_hostname"])
	}
}

// TestQARetryWorkerEmitsReadableMessage closes the end-to-end wiring gap:
// the existing TestRetryWorkerSucceedsOnRecover only asserts delivery
// bookkeeping, never the text. This drives RetryWorker.RunOnce and checks the
// notifier actually received the readable message built from the DB row.
func TestQARetryWorkerEmitsReadableMessage(t *testing.T) {
	repo := newFakeRepo()
	now := time.Date(2026, 5, 2, 12, 0, 0, 0, time.UTC)
	summary := "scrape_down: up == 0 ⇒ device_id=28,kind=custom,plugin=custommetrics,target_id=autoapm,target_name=autoapm (value=0)"
	incident := scrapeDownIncident(summary, now)
	incident.FirstFiredAt = now.Add(-10 * time.Minute)
	incident.LastFiredAt = now.Add(-time.Minute)
	if err := repo.CreateIncident(context.Background(), incident); err != nil {
		t.Fatalf("seed incident: %v", err)
	}
	repo.channels["log"] = &model.Channel{ID: 7, Name: "log", ChannelType: model.ChannelTypeWebhook, Enabled: true}
	failedAt := now.Add(-5 * time.Minute)
	repo.deliveries = append(repo.deliveries, &model.Delivery{
		ID: 1, IncidentID: &incident.ID, ChannelID: 7,
		Status: model.DeliveryStatusFailed, AttemptCount: 1,
		SentAt: &failedAt, FinishedAt: &failedAt,
	})

	notifier := &fakeNotifier{}
	worker := NewRetryWorker(RetryWorkerOpts{
		Repo: repo, Notifier: notifier, Usecase: NewUsecase(repo, nil),
		MaxAttempts: 5, BackoffPerAttempt: time.Minute, Tick: time.Hour,
		Now: func() time.Time { return now },
	})
	worker.RunOnce(context.Background())

	if len(notifier.msgs) != 1 {
		t.Fatalf("retry sent %d messages, want 1", len(notifier.msgs))
	}
	msg := notifier.msgs[0]
	if msg.Subject != "监控采集目标不可达" {
		t.Errorf("retry worker subject = %q, want the Chinese title", msg.Subject)
	}
	if strings.HasPrefix(msg.Subject, "scrape_down:") {
		t.Fatalf("retry worker still emits the raw technical summary: %q", msg.Subject)
	}
	for _, want := range []string{"对象：autoapm（自定义指标采集）", "说明：", "详情：" + summary} {
		if !strings.Contains(msg.Body, want) {
			t.Errorf("retry worker body missing %q: %q", want, msg.Body)
		}
	}
	objectLine, _, _ := strings.Cut(msg.Body, "\n")
	if strings.Contains(objectLine, "主机") {
		t.Errorf("retry worker resolved a host it cannot know: %q", objectLine)
	}
	if msg.DedupeKey != incident.DedupeKey {
		t.Errorf("dedupe = %q, want %q", msg.DedupeKey, incident.DedupeKey)
	}
	if msg.Labels["incident_id"] != "1" || msg.Labels["rule"] != "scrape_down" {
		t.Errorf("control labels = %#v", msg.Labels)
	}
	content := qaRenderDingTalk(t, msg)
	if !strings.Contains(content, "dedupe: "+incident.DedupeKey) {
		t.Errorf("dedupe row not byte-identical after the worker round trip: %q", content)
	}
}

// TestQARetryVsFirstAttemptDiffersOnlyInDeviceDisplay is the honest boundary
// of the "no drift" guarantee: the two paths may differ only where the retry
// worker cannot know more than the DB row (the resolved device display).
// Everything that drives routing / dedupe / cooldown must be identical.
func TestQARetryVsFirstAttemptDiffersOnlyInDeviceDisplay(t *testing.T) {
	at := time.Date(2026, 5, 2, 12, 0, 0, 0, time.UTC)
	summary := "scrape_down: up == 0 ⇒ device_id=28,kind=custom,plugin=custommetrics,target_id=autoapm,target_name=autoapm (value=0)"
	incident := scrapeDownIncident(summary, at)
	incident.LastFiredAt = at

	repo, notifier := newFakeRepo(), &fakeNotifier{}
	resolver := func(_ context.Context, deviceID uint64) (DeviceIdentity, error) {
		if deviceID != 28 {
			t.Errorf("resolver deviceID = %d, want 28", deviceID)
		}
		return DeviceIdentity{Name: "i-vaaqvt1i", Hostname: "i-vaaqvt1i", IPAddress: "10.7.30.49"}, nil
	}
	eval := newPipelineEvaluator(t, repo, notifier, NewStaticRulesProvider(), PipelineEvaluatorOpts{
		DeviceIdentityResolver: resolver,
	})
	eval.notify(context.Background(), &FiringResult{Incident: incident}, summary, incident.ScopeType, at)
	if len(notifier.msgs) != 1 {
		t.Fatalf("notifications = %d, want 1", len(notifier.msgs))
	}
	first := notifier.msgs[0]
	retry := buildIncidentMessage(incident, at)

	// Invariant part: routing / dedupe / cooldown inputs must match.
	if first.Subject != retry.Subject {
		t.Errorf("subject drift: first=%q retry=%q", first.Subject, retry.Subject)
	}
	if first.Source != retry.Source || first.DedupeKey != retry.DedupeKey || first.Severity != retry.Severity {
		t.Errorf("metadata drift: first=%+v retry=%+v", first, retry)
	}
	if !first.OccurredAt.Equal(retry.OccurredAt) {
		t.Errorf("occurred_at drift: first=%v retry=%v", first.OccurredAt, retry.OccurredAt)
	}
	if first.Labels["device_id"] != retry.Labels["device_id"] || first.Labels["device_id"] != "28" {
		t.Errorf("device_id label drift: first=%q retry=%q", first.Labels["device_id"], retry.Labels["device_id"])
	}
	for _, key := range identityLabelKeys {
		if first.Labels[key] != retry.Labels[key] {
			t.Errorf("identity label %q drift: first=%q retry=%q", key, first.Labels[key], retry.Labels[key])
		}
	}

	// Allowed difference: the resolved host only exists on the first attempt.
	if !strings.Contains(first.Body, " · 主机 i-vaaqvt1i / 10.7.30.49") {
		t.Errorf("first-attempt body lost the resolved host: %q", first.Body)
	}
	if strings.Contains(retry.Body, "主机") {
		t.Errorf("retry body claims a host it cannot resolve: %q", retry.Body)
	}
	if first.Body == retry.Body {
		t.Errorf("expected the device display to differ; both bodies are identical")
	}
	if first.Labels["device_hostname"] != "i-vaaqvt1i" || retry.Labels["device_hostname"] != "" {
		t.Errorf("device_hostname: first=%q retry=%q", first.Labels["device_hostname"], retry.Labels["device_hostname"])
	}
	// Both keep the readable skeleton, so the difference is display-only.
	for _, product := range []notify.Message{first, retry} {
		for _, want := range []string{"对象：autoapm（自定义指标采集）", "说明：该采集目标已无法拉取", "详情：scrape_down: up == 0"} {
			if !strings.Contains(product.Body, want) {
				t.Errorf("body missing %q: %q", want, product.Body)
			}
		}
	}
}

// TestQAEquivalenceTestIsNotVacuous guards the guard: it proves the operands
// compared by TestRetryAndFirstAttemptRenderIdentically carry real values and
// are driven by their inputs, so that test cannot degenerate into comparing
// two zero-valued structs.
func TestQAEquivalenceTestIsNotVacuous(t *testing.T) {
	at := time.Date(2026, 5, 2, 12, 0, 0, 0, time.UTC)
	summary := "scrape_down: up == 0 ⇒ device_id=28,kind=custom,plugin=custommetrics,target_id=autoapm,target_name=autoapm (value=0)"
	incident := scrapeDownIncident(summary, at)
	incident.LastFiredAt = at

	repo, notifier := newFakeRepo(), &fakeNotifier{}
	eval := newPipelineEvaluator(t, repo, notifier, NewStaticRulesProvider(), PipelineEvaluatorOpts{})
	eval.notify(context.Background(), &FiringResult{Incident: incident}, summary, incident.ScopeType, at)
	if len(notifier.msgs) != 1 {
		t.Fatalf("notifications = %d, want 1", len(notifier.msgs))
	}
	first := notifier.msgs[0]
	retry := buildIncidentMessage(incident, at)

	// Non-empty: the compared fields are not zero values.
	if strings.TrimSpace(first.Body) == "" || strings.TrimSpace(retry.Body) == "" {
		t.Fatalf("equivalence test compares empty bodies: first=%q retry=%q", first.Body, retry.Body)
	}
	if strings.TrimSpace(first.Subject) == "" || strings.TrimSpace(retry.Subject) == "" {
		t.Fatalf("equivalence test compares empty subjects")
	}
	for name, msg := range map[string]notify.Message{"first": first, "retry": retry} {
		if len(msg.Labels) == 0 {
			t.Fatalf("%s labels empty; the DeepEqual label check would be vacuous", name)
		}
		if msg.DedupeKey == "" || msg.Source == "" || msg.OccurredAt.IsZero() {
			t.Fatalf("%s message is missing routing metadata: %+v", name, msg)
		}
	}

	// Input-driven: shifting an input must move a compared field, otherwise
	// the equality assertion could not fail even against a wrong renderer.
	shifted := *incident
	shifted.LastFiredAt = at.Add(time.Hour)
	if got := buildIncidentMessage(&shifted, at).OccurredAt; got.Equal(retry.OccurredAt) {
		t.Fatal("occurred_at does not track last_fired_at; the equivalence check is vacuous")
	}
	shiftedSummary := *incident
	shiftedSummary.Summary = "scrape_down: up == 0 (value=1)"
	if got := buildIncidentMessage(&shiftedSummary, at).Body; got == retry.Body {
		t.Fatal("body does not track the incident summary; the equivalence check is vacuous")
	}
}
