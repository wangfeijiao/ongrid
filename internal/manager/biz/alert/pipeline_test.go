package alert

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	edgebiz "github.com/ongridio/ongrid/internal/manager/biz/edge"
	model "github.com/ongridio/ongrid/internal/manager/model/alert"
	edgemodel "github.com/ongridio/ongrid/internal/manager/model/edge"
	"github.com/ongridio/ongrid/internal/pkg/promquery"
)

type fakeEdgeLister struct {
	edges []*edgemodel.Edge
	err   error
}

func TestPipelineNotificationPreservesApplicationIdentity(t *testing.T) {
	repo, notifier := newFakeRepo(), &fakeNotifier{}
	prom := &fakePromQuerier{result: &promquery.InstantResult{ResultType: "vector", Result: json.RawMessage(`[{"metric":{"service":"orders","service_namespace":"trade","deployment_environment_name":"production","span_name":"GET /orders/{id}","incident_id":"spoofed"},"value":[1,"25"]}]`)}}
	rules := NewStaticRulesProvider(WithMetricRawRules([]MetricRawRule{{ID: 1, RuleKey: "apm_error", Name: "APM errors", ScopeType: "global", Expr: "up > 0"}}))
	evaluator := newPipelineEvaluator(t, repo, notifier, rules, PipelineEvaluatorOpts{PromQuerier: prom})
	evaluator.EvaluateOnce(t.Context())
	if len(notifier.msgs) != 1 {
		t.Fatalf("notifications=%d", len(notifier.msgs))
	}
	labels := notifier.msgs[0].Labels
	for key, want := range map[string]string{"service": "orders", "service_namespace": "trade", "deployment_environment_name": "production", "span_name": "GET /orders/{id}"} {
		if labels[key] != want {
			t.Fatalf("lost %s in notification: %+v", key, labels)
		}
	}
	if labels["rule"] != "apm_error" || labels["incident_id"] == "spoofed" || labels["incident_id"] == "" {
		t.Fatalf("control identity overwritten: %+v", labels)
	}
}

func (f *fakeEdgeLister) List(_ context.Context, _ edgebiz.ListFilter) ([]*edgemodel.Edge, error) {
	return f.edges, f.err
}

type fakePromQuerier struct {
	result *promquery.InstantResult
	err    error
}

func (f *fakePromQuerier) Query(_ context.Context, _ string, _ time.Time) (*promquery.InstantResult, error) {
	return f.result, f.err
}

func newPipelineEvaluator(t *testing.T, repo *fakeRepo, notifier Notifier, rules RulesProvider, opts PipelineEvaluatorOpts) *PipelineEvaluator {
	t.Helper()
	if opts.Usecase == nil {
		opts.Usecase = NewUsecase(repo, nil)
	}
	if opts.Notifier == nil {
		opts.Notifier = notifier
	}
	if opts.Rules == nil {
		opts.Rules = rules
	}
	if opts.DefaultChannels == nil {
		opts.DefaultChannels = []string{"log"}
	}
	if opts.Now == nil {
		fixed := time.Date(2026, 5, 2, 12, 0, 0, 0, time.UTC)
		opts.Now = func() time.Time { return fixed }
	}
	repo.channels["log"] = &model.Channel{ID: 1, Name: "log", ChannelType: model.ChannelTypeWebhook, Enabled: true, ConfigJSON: `{"url":"http://test.local/hook"}`}
	return NewPipelineEvaluator(opts)
}

// TestPipelineEdgeOfflineMetricRawFiresAndResolves verifies the
// replacement path for edge_offline alerts: a
// metric_raw rule on the edge_last_seen_seconds_ago gauge fires when
// any edge crosses the threshold and resolves once the gauge drops
// back below.
func TestPipelineEdgeOfflineMetricRawFiresAndResolves(t *testing.T) {
	repo := newFakeRepo()
	notifier := &fakeNotifier{}
	now := time.Date(2026, 5, 2, 12, 0, 0, 0, time.UTC)

	// Phase-3 collapse: the expr IS the predicate. fakePromQuerier
	// simulates Prom-side filtering: when the predicate matches, the
	// vector entry is in the response; on recovery we swap to an empty
	// vector (Prom drops the series when comparison returns false).
	prom := &fakePromQuerier{result: vectorEdgeStaleness(map[string]string{
		"1|node-a": "180", // stale: 180s > 90s ⇒ Prom keeps the series
	})}
	rules := NewStaticRulesProvider(WithMetricRawRules([]MetricRawRule{
		{ID: 100, RuleKey: "edge_offline", Name: "Edge Offline", Severity: "critical",
			ScopeType: "global", Expr: "edge_last_seen_seconds_ago > 90"},
	}))
	clock := now
	eval := newPipelineEvaluator(t, repo, notifier, rules, PipelineEvaluatorOpts{
		EdgeLister:  &fakeEdgeLister{},
		PromQuerier: prom,
		Cooldown:    time.Minute,
		Now:         func() time.Time { return clock },
	})

	eval.EvaluateOnce(context.Background())

	if len(repo.incidents) != 1 {
		t.Fatalf("expect 1 incident for stale edge, got %d", len(repo.incidents))
	}
	var inc *model.Incident
	for _, i := range repo.incidents {
		inc = i
	}
	if inc.Rule != "edge_offline" {
		t.Errorf("rule = %q", inc.Rule)
	}
	if len(notifier.msgs) != 1 {
		t.Errorf("notifications = %d, want 1", len(notifier.msgs))
	}

	// Recovery: predicate clears ⇒ Prom drops the series ⇒ empty vector.
	prom.result = vectorEdgeStaleness(map[string]string{})
	clock = now.Add(2 * time.Second)
	eval.EvaluateOnce(context.Background())

	if inc.Status != model.IncidentStatusResolved {
		t.Errorf("after recovery status = %q, want resolved", inc.Status)
	}
}

func TestPipelinePromQueryFiresAndResolves(t *testing.T) {
	repo := newFakeRepo()
	notifier := &fakeNotifier{}
	now := time.Date(2026, 5, 2, 12, 0, 0, 0, time.UTC)

	prom := &fakePromQuerier{result: vectorUp(map[string]string{
		"localhost:9100|node": "0",
	})}
	rules := NewStaticRulesProvider(WithMetricRawRules([]MetricRawRule{
		{ID: 200, RuleKey: "scrape_down", Name: "Scrape Down", Severity: "warning",
			Expr: "up == 0"},
	}))
	clock := now
	eval := newPipelineEvaluator(t, repo, notifier, rules, PipelineEvaluatorOpts{
		EdgeLister:  &fakeEdgeLister{},
		PromQuerier: prom,
		Cooldown:    time.Minute,
		Now:         func() time.Time { return clock },
	})

	eval.EvaluateOnce(context.Background())

	if len(repo.incidents) != 1 {
		t.Fatalf("incidents = %d, want 1", len(repo.incidents))
	}
	var inc *model.Incident
	for _, i := range repo.incidents {
		inc = i
	}
	if inc.Rule != "scrape_down" {
		t.Errorf("rule = %q", inc.Rule)
	}
	wantDedupe := "pipeline:scrape_down:instance=localhost:9100,job=node"
	if inc.DedupeKey != wantDedupe {
		t.Errorf("dedupe = %q, want %q", inc.DedupeKey, wantDedupe)
	}

	// Recovery: PromQL `up == 0` no longer matches when up=1 ⇒ empty
	// vector ⇒ evaluator resolves the prior incident via snapshot diff.
	prom.result = vectorUp(map[string]string{})
	clock = now.Add(time.Second)
	eval.EvaluateOnce(context.Background())

	if inc.Status != model.IncidentStatusResolved {
		t.Errorf("status after recovery = %q", inc.Status)
	}
}

func TestPipelinePromQueryErrorIsSafe(t *testing.T) {
	repo := newFakeRepo()
	prom := &fakePromQuerier{err: errors.New("connection refused")}
	rules := NewStaticRulesProvider(WithMetricRawRules([]MetricRawRule{
		{ID: 1, RuleKey: "scrape_down", Name: "Scrape Down", Severity: "warning",
			Expr: "up == 0"},
	}))
	eval := newPipelineEvaluator(t, repo, &fakeNotifier{}, rules, PipelineEvaluatorOpts{
		EdgeLister:  &fakeEdgeLister{},
		PromQuerier: prom,
		Cooldown:    time.Minute,
	})

	// Must not panic, must not resolve anything.
	eval.EvaluateOnce(context.Background())

	if len(repo.incidents) != 0 {
		t.Errorf("prom query failure must not create incidents, got %d", len(repo.incidents))
	}
}

func TestPipelineNotification_ResolvesDeviceIDToHostnameAndIP(t *testing.T) {
	repo := newFakeRepo()
	notifier := &fakeNotifier{}
	prom := &fakePromQuerier{result: vectorEdgeStaleness(map[string]string{
		"2|edge-197": "91",
	})}
	rules := NewStaticRulesProvider(WithMetricRawRules([]MetricRawRule{
		{ID: 201, RuleKey: "cpu_high_80", Name: "CPU High", Severity: "warning",
			ScopeType: model.RuleScopeHost, Expr: "device_cpu_usage_percent > 80"},
	}))
	eval := newPipelineEvaluator(t, repo, notifier, rules, PipelineEvaluatorOpts{
		PromQuerier: prom,
		DeviceIdentityResolver: func(_ context.Context, deviceID uint64) (DeviceIdentity, error) {
			if deviceID != 2 {
				t.Fatalf("deviceID = %d, want 2", deviceID)
			}
			return DeviceIdentity{Hostname: "VM-4-8-ubuntu", IPAddress: "10.2.4.8"}, nil
		},
	})

	eval.EvaluateOnce(context.Background())

	if len(notifier.msgs) != 1 {
		t.Fatalf("notifications = %d, want 1", len(notifier.msgs))
	}
	msg := notifier.msgs[0]
	// The readable subject is the rule title; the technical summary (with
	// the resolved device display) now lives in the 详情 body line.
	if msg.Subject == "" {
		t.Fatalf("subject must not be empty")
	}
	if strings.Contains(msg.Body, "device_id=2") {
		t.Fatalf("detail still exposes raw device id: %q", msg.Body)
	}
	if !strings.Contains(msg.Body, "device=VM-4-8-ubuntu (10.2.4.8)") {
		t.Fatalf("body = %q, want resolved device identity", msg.Body)
	}
	if !strings.Contains(msg.Body, "对象：") || !strings.Contains(msg.Body, "主机 VM-4-8-ubuntu / 10.2.4.8") {
		t.Fatalf("body = %q, want readable object line with host", msg.Body)
	}
	if msg.Labels["device_id"] != "2" || msg.Labels["device_hostname"] != "VM-4-8-ubuntu" || msg.Labels["device_ip"] != "10.2.4.8" {
		t.Fatalf("labels = %#v", msg.Labels)
	}
}

func TestDeviceDisplay_UsesAvailableIdentityFields(t *testing.T) {
	cases := []struct {
		name     string
		identity DeviceIdentity
		want     string
	}{
		{name: "hostname and ip", identity: DeviceIdentity{Hostname: "host-a", IPAddress: "10.0.0.1"}, want: "host-a (10.0.0.1)"},
		{name: "name fallback", identity: DeviceIdentity{Name: "gateway-a"}, want: "gateway-a"},
		{name: "ip only", identity: DeviceIdentity{IPAddress: "10.0.0.2"}, want: "10.0.0.2"},
		{name: "empty", identity: DeviceIdentity{}, want: ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := deviceDisplay(tc.identity); got != tc.want {
				t.Fatalf("deviceDisplay() = %q, want %q", got, tc.want)
			}
		})
	}
}

// TestPipelineEdgeOfflineMultipleMetricRawRules confirms two metric_raw
// rules with different thresholds against edge_last_seen_seconds_ago
// each create their own incident — the same multi-rule fan-out the
// deleted edge_absence path supported.
func TestPipelineEdgeOfflineMultipleMetricRawRules(t *testing.T) {
	repo := newFakeRepo()
	notifier := &fakeNotifier{}
	now := time.Date(2026, 5, 2, 12, 0, 0, 0, time.UTC)

	prom := &fakePromQuerier{result: vectorEdgeStaleness(map[string]string{
		"5|core": "120", // 120s stale: crosses both thresholds
	})}
	rules := NewStaticRulesProvider(WithMetricRawRules([]MetricRawRule{
		{ID: 100, RuleKey: "edge_offline", Name: "Edge Offline 90s", Severity: "warning",
			ScopeType: "global", Expr: "edge_last_seen_seconds_ago > 90"},
		{ID: 101, RuleKey: "edge_offline_strict", Name: "Edge Offline 30s", Severity: "critical",
			ScopeType: "global", Expr: "edge_last_seen_seconds_ago > 30"},
	}))
	eval := newPipelineEvaluator(t, repo, notifier, rules, PipelineEvaluatorOpts{
		EdgeLister:  &fakeEdgeLister{},
		PromQuerier: prom,
		Cooldown:    time.Minute,
		Now:         func() time.Time { return now },
	})

	eval.EvaluateOnce(context.Background())

	if len(repo.incidents) != 2 {
		t.Errorf("expect 2 incidents (one per rule), got %d", len(repo.incidents))
	}
}

// vectorEdgeStaleness builds a Prom-style vector response keyed
// "edge_id|edge_name" -> seconds-ago, mimicking the
// edge_last_seen_seconds_ago gauge PipelineEvaluator publishes.
func vectorEdgeStaleness(samples map[string]string) *promquery.InstantResult {
	type vEntry struct {
		Metric map[string]string `json:"metric"`
		Value  []json.RawMessage `json:"value"`
	}
	var entries []vEntry
	for k, v := range samples {
		edgeID := k
		edgeName := ""
		for i := range k {
			if k[i] == '|' {
				edgeID = k[:i]
				edgeName = k[i+1:]
				break
			}
		}
		ts, _ := json.Marshal(float64(time.Now().Unix()))
		val, _ := json.Marshal(v)
		entries = append(entries, vEntry{
			Metric: map[string]string{
				"__name__":  "edge_last_seen_seconds_ago",
				"device_id": edgeID,
				"edge_name": edgeName,
			},
			Value: []json.RawMessage{ts, val},
		})
	}
	raw, _ := json.Marshal(entries)
	return &promquery.InstantResult{ResultType: "vector", Result: raw}
}

// vectorUp formats a Prom-style vector response keyed "instance|job" -> value.
func vectorUp(samples map[string]string) *promquery.InstantResult {
	type vEntry struct {
		Metric map[string]string `json:"metric"`
		Value  []json.RawMessage `json:"value"`
	}
	var entries []vEntry
	for k, v := range samples {
		instance := k
		job := ""
		for i := range k {
			if k[i] == '|' {
				instance = k[:i]
				job = k[i+1:]
				break
			}
		}
		ts, _ := json.Marshal(float64(time.Now().Unix()))
		val, _ := json.Marshal(v)
		entries = append(entries, vEntry{
			Metric: map[string]string{
				"__name__": "up",
				"instance": instance,
				"job":      job,
			},
			Value: []json.RawMessage{ts, val},
		})
	}
	raw, _ := json.Marshal(entries)
	return &promquery.InstantResult{ResultType: "vector", Result: raw}
}

func coldStartIncident(repo *fakeRepo, rule, dedupe string) *model.Incident {
	repo.nextID++
	inc := &model.Incident{
		ID: repo.nextID, Rule: rule, Status: model.IncidentStatusOpen,
		DedupeKey: dedupe, Severity: "critical",
	}
	repo.incidents[inc.ID] = inc
	repo.byDedupe[inc.DedupeKey] = inc
	return inc
}

func coldStartVector(labels ...map[string]string) *promquery.InstantResult {
	type vEntry struct {
		Metric map[string]string `json:"metric"`
		Value  []json.RawMessage `json:"value"`
	}
	entries := make([]vEntry, 0, len(labels))
	for _, m := range labels {
		entries = append(entries, vEntry{
			Metric: m,
			Value:  []json.RawMessage{json.RawMessage("1"), json.RawMessage(`"120"`)},
		})
	}
	raw, _ := json.Marshal(entries)
	return &promquery.InstantResult{ResultType: "vector", Result: raw}
}

func coldStartRules() RulesProvider {
	return NewStaticRulesProvider(WithMetricRawRules([]MetricRawRule{
		{ID: 1, RuleKey: "device_offline", Name: "设备离线", Severity: "critical",
			Expr: "device_last_seen_seconds_ago > 90"},
	}))
}

// A manager restart discards firingSnapshot. Open incidents left by the
// previous process must still be resolved once the condition clears.
func TestPipelineColdStartResolvesOrphanedOpenIncident(t *testing.T) {
	repo := newFakeRepo()
	labels := map[string]string{"device_id": "13", "device_name": "srv-10-7-30-34"}
	inc := coldStartIncident(repo, "device_offline", "pipeline:device_offline:"+labelSetKey(labels))
	prom := &fakePromQuerier{result: coldStartVector()}
	eval := newPipelineEvaluator(t, repo, &fakeNotifier{}, coldStartRules(), PipelineEvaluatorOpts{PromQuerier: prom})

	eval.EvaluateOnce(t.Context())

	if inc.Status != model.IncidentStatusResolved {
		t.Fatalf("orphaned incident status = %q, want resolved", inc.Status)
	}
	found := false
	for _, ev := range repo.events {
		if ev.EventType == model.EventTypeResolved && ev.ActorType == model.ActorTypeSystem {
			found = true
		}
	}
	if !found {
		t.Error("missing system resolve audit event")
	}
}

// Seeding must not close an incident whose condition still matches.
func TestPipelineColdStartKeepsStillFiringIncident(t *testing.T) {
	repo := newFakeRepo()
	labels := map[string]string{"device_id": "13", "device_name": "srv-10-7-30-34"}
	inc := coldStartIncident(repo, "device_offline", "pipeline:device_offline:"+labelSetKey(labels))
	prom := &fakePromQuerier{result: coldStartVector(labels)}
	eval := newPipelineEvaluator(t, repo, &fakeNotifier{}, coldStartRules(), PipelineEvaluatorOpts{PromQuerier: prom})

	eval.EvaluateOnce(t.Context())

	if inc.Status != model.IncidentStatusOpen {
		t.Fatalf("still-firing incident status = %q, want open", inc.Status)
	}
}

// Incidents from other rules, or owned by a non-pipeline evaluator, are
// out of scope for this reconciliation.
func TestPipelineColdStartIgnoresForeignIncidents(t *testing.T) {
	repo := newFakeRepo()
	otherRule := coldStartIncident(repo, "log_volume_spike", "pipeline:log_volume_spike:job=x")
	foreignOwner := coldStartIncident(repo, "device_offline", "host:13:edge_offline")
	prom := &fakePromQuerier{result: coldStartVector()}
	eval := newPipelineEvaluator(t, repo, &fakeNotifier{}, coldStartRules(), PipelineEvaluatorOpts{PromQuerier: prom})

	eval.EvaluateOnce(t.Context())

	if otherRule.Status != model.IncidentStatusOpen || foreignOwner.Status != model.IncidentStatusOpen {
		t.Fatalf("foreign incidents resolved: other rule=%q, foreign owner=%q", otherRule.Status, foreignOwner.Status)
	}
}

// A failed Prom query defers reconciliation; nothing is resolved until
// the next successful tick.
func TestPipelineColdStartDefersOnPromFailure(t *testing.T) {
	repo := newFakeRepo()
	inc := coldStartIncident(repo, "device_offline", "pipeline:device_offline:device_id=13")
	prom := &fakePromQuerier{err: errors.New("prom unavailable")}
	eval := newPipelineEvaluator(t, repo, &fakeNotifier{}, coldStartRules(), PipelineEvaluatorOpts{PromQuerier: prom})

	eval.EvaluateOnce(t.Context())
	if inc.Status != model.IncidentStatusOpen {
		t.Fatalf("resolved during failed query: %q", inc.Status)
	}

	prom.err = nil
	prom.result = coldStartVector()
	eval.EvaluateOnce(t.Context())
	if inc.Status != model.IncidentStatusResolved {
		t.Fatalf("not resolved after recovery: %q", inc.Status)
	}
}

// A failed DB read must leave the snapshot untouched and retry next tick.
func TestPipelineColdStartRetriesAfterRepoFailure(t *testing.T) {
	repo := newFakeRepo()
	inc := coldStartIncident(repo, "device_offline", "pipeline:device_offline:device_id=13")
	repo.listIncidentsErr = errors.New("db unavailable")
	prom := &fakePromQuerier{result: coldStartVector()}
	eval := newPipelineEvaluator(t, repo, &fakeNotifier{}, coldStartRules(), PipelineEvaluatorOpts{PromQuerier: prom})

	eval.EvaluateOnce(t.Context())
	if inc.Status != model.IncidentStatusOpen {
		t.Fatalf("resolved with failed DB read: %q", inc.Status)
	}

	repo.listIncidentsErr = nil
	eval.EvaluateOnce(t.Context())
	if inc.Status != model.IncidentStatusResolved {
		t.Fatalf("not resolved after DB recovered: %q", inc.Status)
	}
}
