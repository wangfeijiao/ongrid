package apm

import (
	"context"
	"testing"
)

type fakeHosts struct {
	seen []uint64
	err  error
}

func (f *fakeHosts) ResolveHosts(_ context.Context, ids []uint64) (map[uint64]HostRef, error) {
	f.seen = ids
	if f.err != nil {
		return nil, f.err
	}
	out := make(map[uint64]HostRef, len(ids))
	for _, id := range ids {
		out[id] = HostRef{DeviceID: "dev", IP: "10.0.0.1"}
	}
	return out, nil
}

func TestAttachHostsGroupsByIdentityAndSkipsEmptyDevice(t *testing.T) {
	p := &fakeProm{result: `[
 {"metric":{"service":"orders","service_namespace":"trade","deployment_environment_name":"production","device_id":"43"},"value":[1600,"5"]},
 {"metric":{"service":"orders","service_namespace":"trade","deployment_environment_name":"production","device_id":"42"},"value":[1600,"5"]},
 {"metric":{"service":"billing","service_namespace":"trade","deployment_environment_name":"production","device_id":"42"},"value":[1600,"5"]},
 {"metric":{"service":"ghost","service_namespace":"trade","deployment_environment_name":"production"},"value":[1600,"5"]}
]`}
	hosts := &fakeHosts{}
	rows := []Summary{
		{Identity: Identity{"orders", "trade", "production"}},
		{Identity: Identity{"billing", "trade", "production"}},
		{Identity: Identity{"ghost", "trade", "production"}},
	}
	New(p, nil, nil).WithHosts(hosts).attachHosts(t.Context(), testQuery(), rows)

	if len(rows[0].Hosts) != 2 || rows[0].Hosts[0].DeviceID != "42" || rows[0].Hosts[1].DeviceID != "43" {
		t.Fatalf("hosts must be grouped per identity and ordered by device id: %+v", rows[0].Hosts)
	}
	if rows[0].Hosts[0].IP != "10.0.0.1" {
		t.Fatalf("resolved host facts lost: %+v", rows[0].Hosts[0])
	}
	if len(rows[1].Hosts) != 1 || rows[1].Hosts[0].DeviceID != "42" {
		t.Fatalf("second identity must only carry its own host: %+v", rows[1].Hosts)
	}
	if len(rows[2].Hosts) != 0 {
		t.Fatalf("series without device_id must stay unattributed: %+v", rows[2].Hosts)
	}
	if len(hosts.seen) != 2 || hosts.seen[0] != 42 || hosts.seen[1] != 43 {
		t.Fatalf("resolver must see each device once, sorted: %v", hosts.seen)
	}
}

func TestAttachHostsNeverFailsTheList(t *testing.T) {
	p := &fakeProm{err: context.Canceled}
	rows := []Summary{{Identity: Identity{"orders", "trade", "production"}}}
	New(p, nil, nil).WithHosts(&fakeHosts{}).attachHosts(t.Context(), testQuery(), rows)
	if len(rows[0].Hosts) != 0 {
		t.Fatalf("a failed attribution query must leave hosts empty: %+v", rows[0].Hosts)
	}
}

func TestAttachHostsWithoutResolverCostsNoQuery(t *testing.T) {
	p := &fakeProm{result: `[]`}
	New(p, nil, nil).attachHosts(t.Context(), testQuery(), []Summary{{Identity: Identity{"orders", "trade", "production"}}})
	if p.calls != 0 {
		t.Fatalf("unwired host resolver must not query: calls=%d", p.calls)
	}
}
