package alert

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	model "github.com/ongridio/ongrid/internal/manager/model/alert"
)

// QA regression coverage added on top of the engineer's tests. It targets the
// degradation paths of the readable-notification rewrite that the original
// suite left open: resolver failure, malformed labels, missing identity, and
// the incident-vs-label device_id precedence.
//
// The shared invariants asserted here are the ones the rewrite must never
// break: the notification is still delivered, the Subject is never empty (the
// router rejects an empty subject), the raw device_id is kept when no readable
// identity is available (no information loss), and the "对象" line never shows
// a dangling host marker or an empty device=.

// newEdgeEvaluator wires a scrape_down monitoring_pipeline rule over the
// supplied Prom series labels, with the supplied (possibly failing) resolver.
func newEdgeEvaluator(
	t *testing.T,
	labels map[string]string,
	resolver DeviceIdentityResolver,
) (*PipelineEvaluator, *fakeRepo, *fakeNotifier) {
	t.Helper()
	repo, notifier := newFakeRepo(), &fakeNotifier{}
	prom := &fakePromQuerier{result: readablePromVector(labels, "0")}
	rules := NewStaticRulesProvider(WithMetricRawRules([]MetricRawRule{{
		ID: 201, RuleKey: "scrape_down", Name: "Scrape Down", Severity: "warning",
		ScopeType: model.RuleScopeMonitoringPipeline, Expr: "up == 0",
	}}))
	eval := newPipelineEvaluator(t, repo, notifier, rules, PipelineEvaluatorOpts{
		PromQuerier:            prom,
		DeviceIdentityResolver: resolver,
		Cooldown:               time.Minute,
	})
	return eval, repo, notifier
}

// assertDeliveredOnlyOnce is the shared "nothing was dropped" precondition.
func assertDeliveredOnlyOnce(t *testing.T, notifier *fakeNotifier) string {
	t.Helper()
	if len(notifier.msgs) != 1 {
		t.Fatalf("notifications = %d, want 1 (message must never be dropped)", len(notifier.msgs))
	}
	msg := notifier.msgs[0]
	if strings.TrimSpace(msg.Subject) == "" {
		t.Fatalf("empty subject: the router would reject this message")
	}
	return msg.Subject
}

// TestReadableNotification_ResolverErrorKeepsRawDevice locks the resolver
// failure path: a device-lookup outage must degrade to the original raw
// device_id, not to an empty device= substitution and not to a dropped alert.
func TestReadableNotification_ResolverErrorKeepsRawDevice(t *testing.T) {
	calls := 0
	resolver := func(_ context.Context, deviceID uint64) (DeviceIdentity, error) {
		calls++
		if deviceID != 28 {
			t.Errorf("resolver deviceID = %d, want 28", deviceID)
		}
		return DeviceIdentity{}, errors.New("device store unavailable")
	}

	eval, repo, notifier := newEdgeEvaluator(t, scrapeDownLabels(), resolver)
	eval.EvaluateOnce(context.Background())

	if calls != 1 {
		t.Errorf("resolver calls = %d, want 1", calls)
	}
	assertDeliveredOnlyOnce(t, notifier)
	msg := notifier.msgs[0]

	if msg.Subject != "监控采集目标不可达" {
		t.Errorf("subject = %q, want the built-in Chinese title", msg.Subject)
	}
	if !strings.Contains(msg.Body, "device_id=28") {
		t.Errorf("body lost the raw device id after resolver failure: %q", msg.Body)
	}
	if strings.Contains(msg.Body, "device=") {
		t.Errorf("body has an empty/incorrect device= substitution: %q", msg.Body)
	}
	if strings.Contains(msg.Body, "主机") {
		t.Errorf("body claims a host that could not be resolved: %q", msg.Body)
	}
	if msg.Labels["device_id"] != "28" {
		t.Errorf("label device_id = %q, want 28", msg.Labels["device_id"])
	}
	for _, key := range []string{"device_hostname", "device_ip"} {
		if v, ok := msg.Labels[key]; ok {
			t.Errorf("label %q = %q set without a resolved identity", key, v)
		}
	}
	for _, inc := range repo.incidents {
		if inc.DeviceID != nil {
			t.Errorf("incident.DeviceID = %d, want NULL (ownership red line)", *inc.DeviceID)
		}
	}
}

// TestReadableNotification_MalformedDeviceIDLabelDegrades covers a label set
// whose device_id is not a number: no resolver call, no panic, no bogus
// substitution, and the original label set stays visible in the dedupe key.
func TestReadableNotification_MalformedDeviceIDLabelDegrades(t *testing.T) {
	calls := 0
	resolver := func(_ context.Context, _ uint64) (DeviceIdentity, error) {
		calls++
		return DeviceIdentity{Hostname: "should-not-be-used"}, nil
	}
	labels := map[string]string{
		"device_id":   "abc",
		"kind":        "custom",
		"plugin":      "custommetrics",
		"target_id":   "autoapm",
		"target_name": "autoapm",
	}

	eval, _, notifier := newEdgeEvaluator(t, labels, resolver)
	eval.EvaluateOnce(context.Background())

	if calls != 0 {
		t.Errorf("resolver calls = %d, want 0 for a non-numeric device_id", calls)
	}
	assertDeliveredOnlyOnce(t, notifier)
	msg := notifier.msgs[0]

	if !strings.Contains(msg.Body, "对象：autoapm（自定义指标采集）") {
		t.Errorf("object line broken for malformed device_id: %q", msg.Body)
	}
	if strings.Contains(msg.Body, "device=") {
		t.Errorf("body has a device= substitution for a malformed id: %q", msg.Body)
	}
	if _, ok := msg.Labels["device_id"]; ok {
		t.Errorf("label device_id = %q, want it absent for a malformed id", msg.Labels["device_id"])
	}
	if !strings.Contains(msg.DedupeKey, "device_id=abc") {
		t.Errorf("dedupe key lost the raw label: %q", msg.DedupeKey)
	}
}

// TestReadableNotification_EmptyIdentityKeepsRawDetail covers a resolver that
// resolves without error but yields no usable name/IP: the "对象" line must
// not claim a host and the detail must keep the raw device_id.
func TestReadableNotification_EmptyIdentityKeepsRawDetail(t *testing.T) {
	resolver := func(_ context.Context, _ uint64) (DeviceIdentity, error) {
		return DeviceIdentity{}, nil
	}

	eval, _, notifier := newEdgeEvaluator(t, scrapeDownLabels(), resolver)
	eval.EvaluateOnce(context.Background())

	assertDeliveredOnlyOnce(t, notifier)
	msg := notifier.msgs[0]

	if strings.Contains(msg.Body, "主机") {
		t.Errorf("object line shows a host with an empty identity: %q", msg.Body)
	}
	if strings.Contains(msg.Body, "device=") {
		t.Errorf("detail shows an empty device= substitution: %q", msg.Body)
	}
	if !strings.Contains(msg.Body, "device_id=28") {
		t.Errorf("detail lost the raw device id: %q", msg.Body)
	}
	if msg.Labels["device_hostname"] != "" || msg.Labels["device_ip"] != "" {
		t.Errorf("labels carry a phantom identity: %#v", msg.Labels)
	}
}

// TestReadableNotification_IncidentDeviceIDWinsOverLabel proves the precedence
// documented in notify(): incident.device_id (host scope) wins over a
// device_id label, so a host-scope alert can never be re-attributed to a
// different device by its metric labels. Calls notify() directly because the
// pipeline evaluator never sets DeviceID for this rule shape.
func TestReadableNotification_IncidentDeviceIDWinsOverLabel(t *testing.T) {
	repo, notifier := newFakeRepo(), &fakeNotifier{}
	var resolved uint64
	resolver := func(_ context.Context, deviceID uint64) (DeviceIdentity, error) {
		resolved = deviceID
		return DeviceIdentity{Hostname: "host-99", IPAddress: "10.9.9.99"}, nil
	}
	eval := newPipelineEvaluator(t, repo, notifier, NewStaticRulesProvider(), PipelineEvaluatorOpts{
		DeviceIdentityResolver: resolver,
	})

	incidentDevice := uint64(99)
	incident := &model.Incident{
		ID:         7,
		Rule:       "scrape_down",
		RuleName:   "Scrape Down",
		Severity:   "warning",
		Status:     model.IncidentStatusOpen,
		DedupeKey:  "pipeline:scrape_down:device_id=28,kind=custom",
		LabelsJSON: `{"device_id":"28","kind":"custom","target_name":"autoapm"}`,
		DeviceID:   &incidentDevice,
	}
	summary := "scrape_down: up == 0 ⇒ device_id=99,kind=custom (value=0)"
	eval.notify(context.Background(), &FiringResult{Incident: incident}, summary,
		"monitoring_pipeline", time.Date(2026, 5, 2, 12, 0, 0, 0, time.UTC))

	if resolved != 99 {
		t.Errorf("resolver deviceID = %d, want the incident device 99", resolved)
	}
	assertDeliveredOnlyOnce(t, notifier)
	msg := notifier.msgs[0]

	if msg.Labels["device_id"] != "99" {
		t.Errorf("label device_id = %q, want 99 (incident wins over labels)", msg.Labels["device_id"])
	}
	if !strings.Contains(msg.Body, "device=host-99 (10.9.9.99)") {
		t.Errorf("detail = %q, want the resolved incident device", msg.Body)
	}
	if strings.Contains(msg.Body, "device_id=99") {
		t.Errorf("detail kept the raw incident device id: %q", msg.Body)
	}
	if !strings.Contains(msg.Body, "主机 host-99 / 10.9.9.99") {
		t.Errorf("object line = %q, want the resolved host", msg.Body)
	}
	if msg.DedupeKey != incident.DedupeKey {
		t.Errorf("dedupe key = %q, want %q (must pass through untouched)", msg.DedupeKey, incident.DedupeKey)
	}
	if msg.Source != "monitoring_pipeline" {
		t.Errorf("source = %q, want monitoring_pipeline", msg.Source)
	}
	if incident.DeviceID == nil || *incident.DeviceID != 99 {
		t.Errorf("notify() mutated incident.DeviceID: %v", incident.DeviceID)
	}
	if incident.DedupeKey != "pipeline:scrape_down:device_id=28,kind=custom" {
		t.Errorf("notify() mutated incident.DedupeKey: %q", incident.DedupeKey)
	}
}
