package alert

import (
	"context"
	"fmt"

	model "github.com/ongridio/ongrid/internal/manager/model/alert"
)

// Inhibitor decides whether a freshly-fired incident's notification should
// be suppressed because a higher-priority incident has already fired and
// covers the same scope.
//
// Returns (reason, true) to suppress; (_, false) to let notification proceed.
type Inhibitor interface {
	Suppress(ctx context.Context, incident *model.Incident) (string, bool)
}

// IncidentLookup is the narrow Repo subset BuiltinInhibitor needs.
//
// Lookup is by (rule, device) rather than by exact dedupe key. Since the
// pipeline rewrite every evaluator incident is keyed
// "pipeline:<rule>:<labelSetKey>" (e.g.
// "pipeline:device_offline:device_id=43,device_name=srv-01,instance=..."),
// and device_offline runs at global scope. The pre-rewrite keys this
// inhibitor used to probe ("host:<id>:edge_offline",
// "pipeline:prom_ingest_fail") therefore never existed, so both built-in
// rules silently never matched. See OnGrid-平台业务逻辑与判定规则总览.md §11.
type IncidentLookup interface {
	ListIncidents(ctx context.Context, filter IncidentFilter) ([]*model.Incident, error)
}

// Rule keys the inhibitor reasons about. device_offline replaced the old
// edge_offline key in May 2026 (entity split); both spellings are honoured
// so historical rows keep inhibiting.
const (
	ruleDeviceOffline  = "device_offline"
	ruleLegacyEdgeDown = "edge_offline"
	rulePromIngestFail = "prom_ingest_fail"
	ruleScrapeDown     = "scrape_down"
)

// BuiltinInhibitor implements the canonical inhibition rules over the
// current incident key shape. Two rules cover the major operator pain
// points; admin-defined inhibition lands with a future inhibition_rules
// table.
//
//   - device_offline on device X inhibits every other incident carrying
//     device_id=X (cpu_high / mem_high / disk / APM / scrape_down …).
//     An unreachable host cannot produce trustworthy metrics, so those
//     alerts are all derived noise; only the root cause is signal.
//   - prom_ingest_fail inhibits every scrape_down: when remote_write
//     itself is down, every scrape target looks "down" at once.
type BuiltinInhibitor struct {
	repo IncidentLookup
}

// NewBuiltinInhibitor wires the inhibitor to the alert repo.
func NewBuiltinInhibitor(repo IncidentLookup) *BuiltinInhibitor {
	return &BuiltinInhibitor{repo: repo}
}

// Suppress applies the canonical rules. The two rules are evaluated
// independently (not as switch cases) so an incident that is both on an
// offline device AND a scrape_down gets the device-scoped reason first
// while still falling through to the pipeline check when the device has
// no offline incident.
//
// Lookup failures degrade to "do not inhibit": a missed inhibition only
// costs an extra notification, whereas a false inhibition would hide a
// real alert.
func (i *BuiltinInhibitor) Suppress(ctx context.Context, incident *model.Incident) (string, bool) {
	if i == nil || i.repo == nil || incident == nil {
		return "", false
	}

	// Rule 1: same-device offline root.
	if incident.DeviceID != nil &&
		incident.Rule != ruleDeviceOffline && incident.Rule != ruleLegacyEdgeDown {
		if root := i.activeForDevice(ctx, ruleDeviceOffline, *incident.DeviceID); root != nil {
			return fmt.Sprintf("inhibited by device_offline incident #%d", root.ID), true
		}
		// Historical rows may still carry the pre-rename rule key.
		if root := i.activeForDevice(ctx, ruleLegacyEdgeDown, *incident.DeviceID); root != nil {
			return fmt.Sprintf("inhibited by edge_offline incident #%d", root.ID), true
		}
	}

	// Rule 2: ingest pipeline down ⇒ every scrape target is noise.
	if incident.Rule == ruleScrapeDown {
		if root := i.activeForRule(ctx, rulePromIngestFail); root != nil {
			return fmt.Sprintf("inhibited by prom_ingest_fail incident #%d", root.ID), true
		}
	}

	return "", false
}

// activeForDevice returns the newest unresolved incident of rule on device.
func (i *BuiltinInhibitor) activeForDevice(ctx context.Context, rule string, deviceID uint64) *model.Incident {
	rows, err := i.repo.ListIncidents(ctx, IncidentFilter{
		RuleKey:    rule,
		DeviceID:   &deviceID,
		ActiveOnly: true,
		Limit:      1,
	})
	if err != nil || len(rows) == 0 {
		return nil
	}
	return rows[0]
}

// activeForRule returns the newest unresolved incident of rule, any scope.
func (i *BuiltinInhibitor) activeForRule(ctx context.Context, rule string) *model.Incident {
	rows, err := i.repo.ListIncidents(ctx, IncidentFilter{
		RuleKey:    rule,
		ActiveOnly: true,
		Limit:      1,
	})
	if err != nil || len(rows) == 0 {
		return nil
	}
	return rows[0]
}
