package alert

import (
	"context"
	"errors"
	"testing"

	model "github.com/ongridio/ongrid/internal/manager/model/alert"
)

// fakeIncidentLookup mimics the store's ListIncidents predicate subset the
// inhibitor relies on. It intentionally implements ONLY ListIncidents, so
// the test fails to compile if the inhibitor ever goes back to probing
// exact dedupe keys.
type fakeIncidentLookup struct {
	incidents []*model.Incident
	err       error
}

func (f fakeIncidentLookup) ListIncidents(_ context.Context, filter IncidentFilter) ([]*model.Incident, error) {
	if f.err != nil {
		return nil, f.err
	}
	var out []*model.Incident
	for _, in := range f.incidents {
		if filter.RuleKey != "" && in.Rule != filter.RuleKey {
			continue
		}
		if filter.DeviceID != nil && (in.DeviceID == nil || *in.DeviceID != *filter.DeviceID) {
			continue
		}
		if filter.Status != "" && in.Status != filter.Status {
			continue
		}
		if filter.ActiveOnly && in.Status == model.IncidentStatusResolved {
			continue
		}
		out = append(out, in)
		if filter.Limit > 0 && len(out) >= filter.Limit {
			break
		}
	}
	return out, nil
}

// offlineRoot builds a device_offline root exactly as the pipeline creates
// it: global scope, label-set dedupe key, device_id set from the metric.
func offlineRoot(id, deviceID uint64, status string) *model.Incident {
	dev := deviceID
	return &model.Incident{
		ID:        id,
		Rule:      "device_offline",
		ScopeType: model.RuleScopeGlobal,
		Status:    status,
		DeviceID:  &dev,
		DedupeKey: "pipeline:device_offline:device_id=43,device_name=srv-01,instance=ongrid:9100,job=ongrid-manager",
	}
}

// TestBuiltinInhibitorMatchesPipelineKeyShape is the regression test for the
// defect documented in §11 of the platform overview: the inhibitor used to
// probe the non-existent key "host:<id>:edge_offline", so a real
// device_offline row (pipeline key, global scope) never inhibited anything
// and every CPU/mem alert on an unreachable host still paged on-call.
func TestBuiltinInhibitorMatchesPipelineKeyShape(t *testing.T) {
	dev := uint64(43)
	lookup := fakeIncidentLookup{incidents: []*model.Incident{
		offlineRoot(900, dev, model.IncidentStatusOpen),
	}}
	inh := NewBuiltinInhibitor(lookup)

	cpu := &model.Incident{
		ID:        100,
		ScopeType: model.RuleScopeHost,
		Rule:      "cpu_high",
		DeviceID:  &dev,
		DedupeKey: "pipeline:cpu_high:device_id=43",
	}
	reason, suppressed := inh.Suppress(context.Background(), cpu)
	if !suppressed {
		t.Fatalf("cpu_high on an offline device must be inhibited by the pipeline-shaped device_offline root")
	}
	if reason == "" {
		t.Errorf("inhibitor must return a human-readable reason")
	}
}

// Acknowledged (or silenced) roots still cover the scope — only resolved
// stops inhibition. The old single-status probe would have missed these.
func TestBuiltinInhibitorHonoursUnresolvedRootStates(t *testing.T) {
	for _, status := range []string{
		model.IncidentStatusOpen,
		model.IncidentStatusAcknowledged,
		model.IncidentStatusSilenced,
	} {
		t.Run(status, func(t *testing.T) {
			dev := uint64(43)
			lookup := fakeIncidentLookup{incidents: []*model.Incident{offlineRoot(900, dev, status)}}
			inh := NewBuiltinInhibitor(lookup)
			inc := &model.Incident{ID: 100, Rule: "mem_high", DeviceID: &dev}
			if _, suppressed := inh.Suppress(context.Background(), inc); !suppressed {
				t.Errorf("root in status %q should still inhibit", status)
			}
		})
	}
}

func TestBuiltinInhibitorIgnoresResolvedRoot(t *testing.T) {
	dev := uint64(43)
	lookup := fakeIncidentLookup{incidents: []*model.Incident{
		offlineRoot(900, dev, model.IncidentStatusResolved),
	}}
	inh := NewBuiltinInhibitor(lookup)

	cpu := &model.Incident{ID: 100, ScopeType: model.RuleScopeHost, Rule: "cpu_high", DeviceID: &dev}
	if _, suppressed := inh.Suppress(context.Background(), cpu); suppressed {
		t.Errorf("resolved root must not inhibit downstream incidents")
	}
}

func TestBuiltinInhibitorNeverInhibitsOfflineRuleItself(t *testing.T) {
	dev := uint64(43)
	lookup := fakeIncidentLookup{incidents: []*model.Incident{
		offlineRoot(900, dev, model.IncidentStatusOpen),
	}}
	inh := NewBuiltinInhibitor(lookup)

	for _, rule := range []string{"device_offline", "edge_offline"} {
		inc := &model.Incident{ID: 901, Rule: rule, DeviceID: &dev}
		if _, suppressed := inh.Suppress(context.Background(), inc); suppressed {
			t.Errorf("%s must never inhibit itself", rule)
		}
	}
}

// Different device ⇒ unrelated failure, must not be inhibited.
func TestBuiltinInhibitorScopesToSameDevice(t *testing.T) {
	rootDev := uint64(43)
	otherDev := uint64(44)
	lookup := fakeIncidentLookup{incidents: []*model.Incident{
		offlineRoot(900, rootDev, model.IncidentStatusOpen),
	}}
	inh := NewBuiltinInhibitor(lookup)

	inc := &model.Incident{ID: 100, Rule: "cpu_high", DeviceID: &otherDev}
	if _, suppressed := inh.Suppress(context.Background(), inc); suppressed {
		t.Errorf("alert on device %d must not be inhibited by an offline root on device %d", otherDev, rootDev)
	}
}

// prom_ingest_fail carries a label-set suffix in production
// ("pipeline:prom_ingest_fail:<labels>"); the rule-level probe must match it.
func TestBuiltinInhibitorPromIngestSuppressesScrapeDown(t *testing.T) {
	lookup := fakeIncidentLookup{incidents: []*model.Incident{{
		ID:        7,
		Rule:      "prom_ingest_fail",
		ScopeType: model.RuleScopeMonitoringPipeline,
		Status:    model.IncidentStatusOpen,
		DedupeKey: "pipeline:prom_ingest_fail:instance=ongrid:9100",
	}}}
	inh := NewBuiltinInhibitor(lookup)

	for _, key := range []string{
		"pipeline:scrape_down:db_type=redis,device_id=19,kind=redis",
		"pipeline:scrape_down:instance=10.7.30.47:9100",
	} {
		t.Run(key, func(t *testing.T) {
			scrape := &model.Incident{ScopeType: model.RuleScopeMonitoringPipeline, Rule: "scrape_down", DedupeKey: key}
			if _, suppressed := inh.Suppress(context.Background(), scrape); !suppressed {
				t.Errorf("scrape_down should be inhibited while prom_ingest_fail is active")
			}
		})
	}

	other := &model.Incident{Rule: "cpu_high"}
	if _, suppressed := inh.Suppress(context.Background(), other); suppressed {
		t.Errorf("prom_ingest_fail must not inhibit unrelated rules")
	}
}

// scrape_down often carries a device_id AND the device may have no offline
// root; the pipeline rule must still be reachable (rules evaluated
// independently, not as mutually exclusive switch cases).
func TestBuiltinInhibitorScrapeDownFallsThroughToPipelineRule(t *testing.T) {
	dev := uint64(19)
	lookup := fakeIncidentLookup{incidents: []*model.Incident{{
		ID:     7,
		Rule:   "prom_ingest_fail",
		Status: model.IncidentStatusOpen,
	}}}
	inh := NewBuiltinInhibitor(lookup)

	scrape := &model.Incident{ID: 300, Rule: "scrape_down", DeviceID: &dev,
		DedupeKey: "pipeline:scrape_down:device_id=19,kind=redis"}
	if _, suppressed := inh.Suppress(context.Background(), scrape); !suppressed {
		t.Errorf("scrape_down with device_id but no offline root must still hit the prom_ingest_fail rule")
	}
}

// Lookup failure degrades to "not inhibited": a missed inhibition costs one
// extra notification, a false one hides a real alert.
func TestBuiltinInhibitorLookupErrorDoesNotInhibit(t *testing.T) {
	dev := uint64(43)
	inh := NewBuiltinInhibitor(fakeIncidentLookup{err: errors.New("db down")})

	inc := &model.Incident{ID: 100, Rule: "cpu_high", DeviceID: &dev}
	if _, suppressed := inh.Suppress(context.Background(), inc); suppressed {
		t.Errorf("inhibitor must fail open (no suppression) when the lookup errors")
	}
}

// An incident with no device binding cannot be device-scoped, but can still
// be a scrape_down (global). Guards against a nil-pointer regression.
func TestBuiltinInhibitorNilDeviceIncident(t *testing.T) {
	inh := NewBuiltinInhibitor(fakeIncidentLookup{})
	inc := &model.Incident{ID: 1, Rule: "apm_p95_high"}
	if _, suppressed := inh.Suppress(context.Background(), inc); suppressed {
		t.Errorf("incident without device_id and without matching rules must not be inhibited")
	}
	if _, suppressed := inh.Suppress(context.Background(), nil); suppressed {
		t.Errorf("nil incident must not be inhibited")
	}
}
