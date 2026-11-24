package alert

import (
	"encoding/json"
	"testing"

	"github.com/ongridio/ongrid/internal/pkg/promquery"
)

// Regression (OPT-030): device_offline runs at global scope, but its series
// carry device_id. Before the fix the evaluator only parsed device_id for
// host-scope rules, so every device_offline incident landed with
// device_id = NULL and the built-in inhibitor could never tie derived host
// alerts (cpu_high & co.) back to an offline device.
func TestPipelineDeviceOfflineCarriesDeviceID(t *testing.T) {
	repo, notifier := newFakeRepo(), &fakeNotifier{}
	prom := &fakePromQuerier{result: &promquery.InstantResult{
		ResultType: "vector",
		Result:     json.RawMessage(`[{"metric":{"device_id":"1119","device_name":"srv-10-7-30-35","instance":"ongrid:9100","job":"ongrid-manager"},"value":[1,"35822"]}]`),
	}}
	rules := NewStaticRulesProvider(WithMetricRawRules([]MetricRawRule{{
		ID: 1, RuleKey: "device_offline", Name: "设备离线",
		Severity: "critical", ScopeType: "global",
		Expr: "device_last_seen_seconds_ago > 180",
	}}))
	eval := newPipelineEvaluator(t, repo, notifier, rules, PipelineEvaluatorOpts{PromQuerier: prom})

	eval.EvaluateOnce(t.Context())

	if len(repo.incidents) != 1 {
		t.Fatalf("incidents=%d, want 1", len(repo.incidents))
	}
	for _, inc := range repo.incidents {
		if inc.DeviceID == nil {
			t.Fatalf("device_offline incident lost device_id; inhibitor cannot scope to the device")
		}
		if *inc.DeviceID != 1119 {
			t.Fatalf("device_id=%d, want 1119", *inc.DeviceID)
		}
	}
}

// The counterpart red line: monitoring_pipeline rules must NOT take on a
// device_id even when the scraped series carries one (alert ownership and
// the dedupe key depend on staying scope-level).
func TestPipelineScrapeDownKeepsNullDeviceID(t *testing.T) {
	repo, notifier := newFakeRepo(), &fakeNotifier{}
	prom := &fakePromQuerier{result: &promquery.InstantResult{
		ResultType: "vector",
		Result:     json.RawMessage(`[{"metric":{"device_id":"19","kind":"redis","service":"redis-6379"},"value":[1,"0"]}]`),
	}}
	rules := NewStaticRulesProvider(WithMetricRawRules([]MetricRawRule{{
		ID: 2, RuleKey: "scrape_down", Name: "采集目标不可达",
		Severity: "warning", ScopeType: "monitoring_pipeline", Expr: "up == 0",
	}}))
	eval := newPipelineEvaluator(t, repo, notifier, rules, PipelineEvaluatorOpts{PromQuerier: prom})

	eval.EvaluateOnce(t.Context())

	if len(repo.incidents) != 1 {
		t.Fatalf("incidents=%d, want 1", len(repo.incidents))
	}
	for _, inc := range repo.incidents {
		if inc.DeviceID != nil {
			t.Fatalf("monitoring_pipeline incident must keep device_id NULL, got %d", *inc.DeviceID)
		}
	}
}
