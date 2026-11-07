package alert

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	model "github.com/ongridio/ongrid/internal/manager/model/alert"
	"github.com/ongridio/ongrid/internal/pkg/notify"
)

// identityLabelKeys are the application-identity labels copied onto the
// outbound Message for routing and correlation. Control labels
// (rule / incident_id) always come from the incident, never the labels.
var identityLabelKeys = []string{
	"service", "service_namespace", "deployment_environment_name",
	"service_instance_id", "span_name",
}

// incidentDevice bundles the best-effort device resolution used to render a
// readable notification.
type incidentDevice struct {
	// Labels is the decoded incident label set (empty on malformed JSON).
	Labels model.Labels
	// DeviceID is the resolved device: incident.device_id when present,
	// otherwise the device_id metric label (monitoring_pipeline rules keep
	// it there). 0 means no device.
	DeviceID uint64
	// Identity is the resolved device identity (zero when unavailable).
	Identity DeviceIdentity
	// Display is "name (ip)" used to substitute device_id in the detail line.
	Display string
	// Host is "name / ip" used by the human "对象" line.
	Host string
}

// resolveIncidentDevice decodes the incident labels and resolves the
// best-effort device identity for display. resolver may be nil (the retry
// worker wires none); the caller then gets an empty Display/Host and the
// detail line keeps the raw device_id. A resolver error or a malformed label
// blob degrades gracefully — it never drops or blanks the notification.
//
// This is display-only: dedupe keys, incident ownership, inhibition and
// recovery never read anything produced here.
func resolveIncidentDevice(
	ctx context.Context,
	incident *model.Incident,
	resolver DeviceIdentityResolver,
	log *slog.Logger,
) incidentDevice {
	dev := incidentDevice{Labels: model.Labels{}}
	if labels, err := incident.Labels(); err != nil {
		if log != nil {
			log.Warn("alert: decode notification identity failed",
				slog.Uint64("incident_id", incident.ID), slog.Any("err", err))
		}
	} else if labels != nil {
		dev.Labels = labels
	}

	if incident.DeviceID != nil && *incident.DeviceID > 0 {
		dev.DeviceID = *incident.DeviceID
	} else if parsed := deviceIDFromLabels(dev.Labels); parsed != nil {
		dev.DeviceID = *parsed
	}
	if dev.DeviceID == 0 || resolver == nil {
		return dev
	}
	resolved, resolveErr := resolver(ctx, dev.DeviceID)
	if resolveErr != nil {
		if log != nil {
			log.Warn("alert: resolve device identity for notification failed",
				slog.Uint64("device_id", dev.DeviceID), slog.Any("err", resolveErr))
		}
		return dev
	}
	dev.Identity = resolved
	dev.Display = deviceDisplay(resolved)
	dev.Host = readableDevice(resolved)
	return dev
}

// notificationRender is the fully-resolved input to renderIncidentMessage.
type notificationRender struct {
	Incident *model.Incident
	Summary  string    // technical line for the 详情 row (may be "")
	Source   string    // Message.Source
	At       time.Time // Message.OccurredAt
	Device   incidentDevice
}

// renderIncidentMessage builds the outbound notify.Message for an incident.
// It is the single shared renderer for the first-attempt path
// (PipelineEvaluator.notify) and the retry path (buildIncidentMessage), so a
// retried delivery reads identically to the original instead of drifting back
// to the raw technical summary. Subject is always non-empty (the router
// rejects an empty subject) and dedupe / severity / source pass through
// untouched.
func renderIncidentMessage(r notificationRender) notify.Message {
	incident := r.Incident
	severity := notify.Severity(incident.Severity)
	if severity == "" {
		severity = notify.SeverityWarning
	}
	labels := map[string]string{
		"rule":        incident.Rule,
		"incident_id": fmt.Sprintf("%d", incident.ID),
	}
	for _, key := range identityLabelKeys {
		if value, ok := r.Device.Labels[key]; ok {
			labels[key] = value
		}
	}
	if r.Device.DeviceID > 0 {
		labels["device_id"] = fmt.Sprintf("%d", r.Device.DeviceID)
		if r.Device.Display != "" {
			if r.Device.Identity.Hostname != "" {
				labels["device_hostname"] = r.Device.Identity.Hostname
			}
			if r.Device.Identity.IPAddress != "" {
				labels["device_ip"] = r.Device.Identity.IPAddress
			}
		}
	}
	return notify.Message{
		Subject:    readableTitle(incident.Rule, incident.RuleName),
		Body:       readableBody(r.Device.Labels, incident.Rule, r.Device.Host, readableDetail(r.Summary, r.Device.DeviceID, r.Device.Display)),
		Severity:   severity,
		Source:     r.Source,
		DedupeKey:  incident.DedupeKey,
		OccurredAt: r.At,
		Labels:     labels,
	}
}
