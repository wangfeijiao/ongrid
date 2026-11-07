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
	"github.com/ongridio/ongrid/internal/pkg/promquery"
)

// scrapeDownLabels mirrors the exact label set of the operator-reported
// DingTalk alert: up == 0 on a custommetrics target of device 28.
func scrapeDownLabels() map[string]string {
	return map[string]string{
		"device_id":   "28",
		"kind":        "custom",
		"plugin":      "custommetrics",
		"target_id":   "autoapm",
		"target_name": "autoapm",
	}
}

// readablePromVector builds a one-entry Prom vector with the supplied
// metric labels and value.
func readablePromVector(labels map[string]string, value string) *promquery.InstantResult {
	type entry struct {
		Metric map[string]string `json:"metric"`
		Value  []json.RawMessage `json:"value"`
	}
	metric := map[string]string{"__name__": "up"}
	for k, v := range labels {
		metric[k] = v
	}
	valJSON, _ := json.Marshal(value)
	entries := []entry{{
		Metric: metric,
		Value:  []json.RawMessage{json.RawMessage("1"), valJSON},
	}}
	raw, _ := json.Marshal(entries)
	return &promquery.InstantResult{ResultType: "vector", Result: raw}
}

// newReadableEvaluator wires a scrape_down-style monitoring_pipeline rule.
// ruleName is passed explicitly so tests can simulate the legacy English
// "Scrape Down" row that production databases still carry.
func newReadableEvaluator(
	t *testing.T,
	ruleName string,
	resolver DeviceIdentityResolver,
) (*PipelineEvaluator, *fakeRepo, *fakeNotifier) {
	t.Helper()
	repo, notifier := newFakeRepo(), &fakeNotifier{}
	prom := &fakePromQuerier{result: readablePromVector(scrapeDownLabels(), "0")}
	rules := NewStaticRulesProvider(WithMetricRawRules([]MetricRawRule{{
		ID: 200, RuleKey: "scrape_down", Name: ruleName, Severity: "warning",
		ScopeType: model.RuleScopeMonitoringPipeline, Expr: "up == 0",
	}}))
	eval := newPipelineEvaluator(t, repo, notifier, rules, PipelineEvaluatorOpts{
		PromQuerier:            prom,
		DeviceIdentityResolver: resolver,
		Cooldown:               time.Minute,
	})
	return eval, repo, notifier
}

func scrapeDownIdentityResolver(t *testing.T) DeviceIdentityResolver {
	t.Helper()
	return func(_ context.Context, deviceID uint64) (DeviceIdentity, error) {
		if deviceID != 28 {
			t.Fatalf("resolved deviceID = %d, want 28", deviceID)
		}
		// Production device 28 is i-vaaqvt1i / 10.7.30.49.
		return DeviceIdentity{Name: "i-vaaqvt1i", Hostname: "i-vaaqvt1i", IPAddress: "10.7.30.49"}, nil
	}
}

const wantScrapeDownDedupe = "pipeline:scrape_down:device_id=28,kind=custom,plugin=custommetrics,target_id=autoapm,target_name=autoapm"

// TestReadableNotification_ScrapeDownChineseTitleAndDevice locks the whole
// reported defect: a monitoring_pipeline alert must carry a Chinese title,
// a human-readable object + host, an operator hint, and the untouched
// technical details / source / dedupe key.
func TestReadableNotification_ScrapeDownChineseTitleAndDevice(t *testing.T) {
	eval, repo, notifier := newReadableEvaluator(t, "Scrape Down", scrapeDownIdentityResolver(t))
	eval.EvaluateOnce(context.Background())

	if len(notifier.msgs) != 1 {
		t.Fatalf("notifications = %d, want 1", len(notifier.msgs))
	}
	msg := notifier.msgs[0]

	// The rule row still says "Scrape Down" — the built-in Chinese title
	// must win so operators can read the headline at a glance.
	if msg.Subject != "监控采集目标不可达" {
		t.Fatalf("subject = %q, want Chinese title", msg.Subject)
	}

	for _, want := range []string{
		"对象：autoapm（自定义指标采集） · 主机 i-vaaqvt1i / 10.7.30.49",
		"说明：该采集目标已无法拉取，指标停止上报；请检查采集插件与目标进程。",
		"详情：scrape_down: up == 0 ⇒ device=i-vaaqvt1i (10.7.30.49),kind=custom,plugin=custommetrics,target_id=autoapm,target_name=autoapm (value=0)",
		"up == 0", "(value=0)", "kind=custom", "plugin=custommetrics",
		"target_id=autoapm", "target_name=autoapm",
	} {
		if !strings.Contains(msg.Body, want) {
			t.Errorf("body missing %q:\n%s", want, msg.Body)
		}
	}
	if strings.Contains(msg.Body, "device_id=28") {
		t.Errorf("detail should show resolved device, not raw id: %s", msg.Body)
	}

	// source: and dedupe: must be byte-identical to the original alert —
	// the DingTalk bot has a "dedupe" custom-keyword safety policy.
	if msg.Source != "monitoring_pipeline" {
		t.Errorf("source = %q, want monitoring_pipeline", msg.Source)
	}
	if msg.DedupeKey != wantScrapeDownDedupe {
		t.Errorf("dedupe = %q, want %q", msg.DedupeKey, wantScrapeDownDedupe)
	}
	if msg.Severity != notify.SeverityWarning {
		t.Errorf("severity = %q, want warning (routing must not change)", msg.Severity)
	}

	// Red line: incident ownership / dedupe key must not change for a
	// monitoring_pipeline rule (device_id stays NULL on the incident).
	for _, inc := range repo.incidents {
		if inc.DeviceID != nil {
			t.Errorf("incident.DeviceID changed to %d; must stay NULL", *inc.DeviceID)
		}
		if inc.DedupeKey != wantScrapeDownDedupe {
			t.Errorf("incident dedupe = %q, want %q", inc.DedupeKey, wantScrapeDownDedupe)
		}
	}

	for key, want := range map[string]string{
		"device_id": "28", "device_hostname": "i-vaaqvt1i", "device_ip": "10.7.30.49",
	} {
		if msg.Labels[key] != want {
			t.Errorf("label %q = %q, want %q", key, msg.Labels[key], want)
		}
	}
}

// TestReadableNotification_DingTalkFormatKeepsDedupeAndSource drives the
// real DingTalk sender with the real pipeline message and inspects the
// outbound text, so the "dedupe" keyword contract is verified end to end.
func TestReadableNotification_DingTalkFormatKeepsDedupeAndSource(t *testing.T) {
	eval, _, notifier := newReadableEvaluator(t, "Scrape Down", scrapeDownIdentityResolver(t))
	eval.EvaluateOnce(context.Background())
	if len(notifier.msgs) != 1 {
		t.Fatalf("notifications = %d, want 1", len(notifier.msgs))
	}

	var payload map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Errorf("decode dingtalk payload: %v", err)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	sender := notify.NewDingTalkSender("dingtalk", srv.URL, "", srv.Client())
	if err := sender.Send(context.Background(), notifier.msgs[0]); err != nil {
		t.Fatalf("DingTalk Send: %v", err)
	}
	text, _ := payload["text"].(map[string]any)
	content, _ := text["content"].(string)
	if content == "" {
		t.Fatalf("dingtalk content empty: %#v", payload)
	}
	t.Logf("rendered DingTalk message:\n%s", content)

	lines := strings.Split(content, "\n")
	if lines[0] != "[警告] 监控采集目标不可达" {
		t.Errorf("first line = %q, want localized severity + Chinese title", lines[0])
	}
	for _, want := range []string{
		"对象：autoapm（自定义指标采集） · 主机 i-vaaqvt1i / 10.7.30.49",
		"说明：该采集目标已无法拉取",
		"详情：scrape_down: up == 0",
		"source: monitoring_pipeline",
		"dedupe: " + wantScrapeDownDedupe,
	} {
		if !strings.Contains(content, want) {
			t.Errorf("dingtalk content missing %q:\n%s", want, content)
		}
	}
	if !strings.Contains(content, "dedupe") {
		t.Fatal("DingTalk custom keyword 'dedupe' was dropped")
	}
	// source/dedupe must remain their own trailing lines, in that order.
	if len(lines) < 2 || lines[len(lines)-2] != "source: monitoring_pipeline" || lines[len(lines)-1] != "dedupe: "+wantScrapeDownDedupe {
		t.Errorf("trailing lines changed: %#v", lines)
	}
}

// TestReadableNotification_UnknownRuleFallsBackWithoutLosingMessage proves
// user-created rules degrade gracefully (rule.Name → rule_key) and never
// produce an empty subject that the router would drop.
func TestReadableNotification_UnknownRuleFallsBackWithoutLosingMessage(t *testing.T) {
	repo, notifier := newFakeRepo(), &fakeNotifier{}
	prom := &fakePromQuerier{result: readablePromVector(map[string]string{"job": "orders-api"}, "1")}
	rules := NewStaticRulesProvider(WithMetricRawRules([]MetricRawRule{{
		ID: 300, RuleKey: "my_team_rule", Name: "My team rule", Severity: "warning",
		ScopeType: model.RuleScopeGlobal, Expr: "up > 0",
	}}))
	eval := newPipelineEvaluator(t, repo, notifier, rules, PipelineEvaluatorOpts{
		PromQuerier: prom,
		Cooldown:    time.Minute,
	})
	eval.EvaluateOnce(context.Background())

	if len(notifier.msgs) != 1 {
		t.Fatalf("notifications = %d, want 1 (unknown rule must not be dropped)", len(notifier.msgs))
	}
	msg := notifier.msgs[0]
	if msg.Subject != "My team rule" {
		t.Errorf("subject = %q, want rule.Name fallback", msg.Subject)
	}
	if !strings.Contains(msg.Body, "说明："+defaultReadableHint) {
		t.Errorf("missing generic hint: %s", msg.Body)
	}
	if !strings.Contains(msg.Body, "详情：my_team_rule: up > 0") || !strings.Contains(msg.Body, "job=orders-api") {
		t.Errorf("technical detail lost: %s", msg.Body)
	}
	if msg.DedupeKey == "" || msg.Source == "" {
		t.Errorf("metadata lost: source=%q dedupe=%q", msg.Source, msg.DedupeKey)
	}
}

// TestReadableNotification_EmptyRuleNameStillNonEmpty covers the last
// fallback step: no rule name and no dictionary entry → rule_key.
func TestReadableNotification_EmptyRuleNameStillNonEmpty(t *testing.T) {
	if got := readableTitle("orphan_rule", ""); got != "orphan_rule" {
		t.Errorf("readableTitle = %q, want rule_key", got)
	}
	if got := readableTitle("", ""); got == "" {
		t.Error("readableTitle must never return an empty subject")
	}
	if got := readableBody(nil, "orphan_rule", "", ""); got == "" || !strings.Contains(got, "详情：") {
		t.Errorf("readableBody = %q, want non-empty with 详情 line", got)
	}
}
