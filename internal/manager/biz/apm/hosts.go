package apm

import (
	"context"
	"sort"
	"strconv"
)

// HostRef is the server a service was observed on. The APM list is grouped by
// service identity, so one row can span several hosts; the identity alone never
// says which machine the traffic came from.
type HostRef struct {
	DeviceID string `json:"device_id"`
	Name     string `json:"name,omitempty"`
	Hostname string `json:"hostname,omitempty"`
	IP       string `json:"ip,omitempty"`
}

// maxHostsPerService bounds the per-row host list. A mislabelled service name
// can spread across hundreds of hosts; the list view only needs enough hosts to
// show where it lives and to let the operator open the rest.
const maxHostsPerService = 16

// HostResolver maps device IDs to operator-facing host facts. Implemented by the
// data tier so this package stays independent of the devices table.
type HostResolver interface {
	ResolveHosts(ctx context.Context, deviceIDs []uint64) (map[uint64]HostRef, error)
}

func (s *Service) WithHosts(resolver HostResolver) *Service {
	s.hosts = resolver
	return s
}

// attachHosts fills Summary.Hosts for a service list. Attribution is a single
// extra query grouped by identity plus device_id, so the cost does not grow with
// the number of services. It is decorative: a failure leaves Hosts empty rather
// than failing the whole list.
func (s *Service) attachHosts(ctx context.Context, q Query, rows []Summary) {
	if s.hosts == nil || len(rows) == 0 || s.prom == nil {
		return
	}
	expr := q.aggregate("count_over_time", q.counter(), q.End.Sub(q.Start), identityLabels+",device_id")
	series, err := s.instant(ctx, expr, q.End)
	if err != nil {
		if s.log != nil {
			s.log.WarnContext(ctx, "APM host attribution unavailable", "error", err)
		}
		return
	}
	byIdentity := make(map[Identity][]string, len(rows))
	known := make(map[uint64]*HostRef)
	for _, item := range series {
		device := item.Metric["device_id"]
		if device == "" {
			continue
		}
		byIdentity[identityFromLabels(item.Metric)] = append(byIdentity[identityFromLabels(item.Metric)], device)
		if id, err := strconv.ParseUint(device, 10, 64); err == nil {
			known[id] = nil
		}
	}
	if len(known) == 0 {
		return
	}
	ids := make([]uint64, 0, len(known))
	for id := range known {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	resolved, err := s.hosts.ResolveHosts(ctx, ids)
	if err != nil {
		if s.log != nil {
			s.log.WarnContext(ctx, "APM host resolution unavailable", "error", err)
		}
		return
	}
	for i := range rows {
		devices := byIdentity[rows[i].Identity]
		if len(devices) == 0 {
			continue
		}
		sort.Slice(devices, func(a, b int) bool {
			left, _ := strconv.ParseUint(devices[a], 10, 64)
			right, _ := strconv.ParseUint(devices[b], 10, 64)
			return left < right
		})
		hosts := make([]HostRef, 0, min(len(devices), maxHostsPerService))
		for _, device := range devices[:min(len(devices), maxHostsPerService)] {
			host := HostRef{DeviceID: device}
			if id, err := strconv.ParseUint(device, 10, 64); err == nil {
				if ref, ok := resolved[id]; ok {
					// The map key is authoritative: the label is what the
					// metric actually carried.
					ref.DeviceID = device
					host = ref
				}
			}
			hosts = append(hosts, host)
		}
		rows[i].Hosts = hosts
	}
}
