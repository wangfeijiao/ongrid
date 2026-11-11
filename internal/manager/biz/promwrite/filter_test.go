package promwrite

import (
	"context"
	"log/slog"
	"sync"
	"testing"

	pkgpromwrite "github.com/ongridio/ongrid/internal/pkg/promwrite"
	"github.com/ongridio/ongrid/internal/pkg/tunnel"
)

func TestShouldDropHighCardinalitySample(t *testing.T) {
	tests := []struct {
		name string
		want bool
	}{
		// Dropped: process-exporter thread-level families.
		{"namedprocess_namegroup_thread_cpu_seconds_total", true},
		{"namedprocess_namegroup_thread_io_bytes_total", true},
		{"namedprocess_namegroup_thread_context_switches_total", true},
		{"namedprocess_namegroup_thread_count", true},
		{"namedprocess_namegroup_thread_minor_page_faults_total", true},
		{"namedprocess_namegroup_threads_wchan", true},
		// Dropped: size histograms and client-side duration histograms.
		{"http_client_request_body_size_bytes_bucket", true},
		{"http_client_response_body_size_bytes_bucket", true},
		{"http_server_request_body_size_bytes_bucket", true},
		{"http_server_response_body_size_bytes_bucket", true},
		{"http_client_request_duration_seconds_bucket", true},
		{"rpc_client_call_duration_seconds_bucket", true},
		{"v8js_gc_duration_seconds_bucket", true},

		// Kept: Monitor dashboard panels.
		{"namedprocess_namegroup_cpu_seconds_total", false},
		{"namedprocess_namegroup_memory_bytes", false},
		{"namedprocess_namegroup_num_procs", false},
		{"namedprocess_namegroup_num_threads", false},
		{"namedprocess_namegroup_states", false},
		{"node_cpu_seconds_total", false},
		{"node_memory_MemAvailable_bytes", false},
		{"node_filesystem_avail_bytes", false},
		{"node_network_receive_bytes_total", false},
		// Kept: alert rules and APM latency.
		{"http_server_request_duration_seconds_bucket", false},
		{"http_server_request_duration_seconds_count", false},
		{"http_server_request_duration_seconds_sum", false},
		{"traces_spanmetrics_latency_bucket", false},
		{"traces_spanmetrics_calls_total", false},
		// Kept: low-cardinality own / third-party metrics and companions.
		{"ongrid_http_request_duration_seconds_bucket", false},
		{"redis_commands_latencies_usec_bucket", false},
		{"http_client_request_body_size_bytes_count", false},
		{"http_client_request_body_size_bytes_sum", false},
		{"up", false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := shouldDropHighCardinalitySample(tc.name); got != tc.want {
				t.Errorf("shouldDropHighCardinalitySample(%q) = %v, want %v", tc.name, got, tc.want)
			}
		})
	}
}

// countingWriter records how many times Write was called, so a fully
// filtered batch can be proven not to issue an empty remote_write.
type countingWriter struct {
	mu    sync.Mutex
	calls int
	last  []pkgpromwrite.Sample
}

func (c *countingWriter) Write(_ context.Context, samples []pkgpromwrite.Sample) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.calls++
	c.last = append([]pkgpromwrite.Sample(nil), samples...)
	return nil
}

func (c *countingWriter) snapshot() (int, []pkgpromwrite.Sample) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.calls, c.last
}

func mixedBatch() []tunnel.PromSample {
	return []tunnel.PromSample{
		{Name: "node_cpu_seconds_total", Value: 1, TsMs: 1000},
		{Name: "namedprocess_namegroup_thread_cpu_seconds_total", Value: 2, TsMs: 1000},
		{Name: "http_server_request_duration_seconds_bucket", Value: 3, TsMs: 1000},
		{Name: "http_client_request_body_size_bytes_bucket", Value: 4, TsMs: 1000},
		{Name: "namedprocess_namegroup_memory_bytes", Value: 5, TsMs: 1000},
	}
}

func TestIngester_FilterDisabledByDefault(t *testing.T) {
	cw := &countingWriter{}
	ing := NewIngester(cw, slog.Default())
	batch := mixedBatch()

	if err := ing.Push(context.Background(), 42, "embedded:gopsutil", batch); err != nil {
		t.Fatalf("Push: %v", err)
	}
	calls, got := cw.snapshot()
	if calls != 1 || len(got) != 5 {
		t.Fatalf("Write calls=%d samples=%d, want 1 call with all 5 samples", calls, len(got))
	}
	for i, s := range got {
		if labelMap(s.Labels)["__name__"] != batch[i].Name {
			t.Errorf("sample %d = %q, want %q", i, labelMap(s.Labels)["__name__"], batch[i].Name)
		}
	}
	if ing.DroppedSamples() != 0 {
		t.Errorf("DroppedSamples = %d, want 0 when filter disabled", ing.DroppedSamples())
	}
}

func TestIngester_FilterEnabledDropsOnlyListedFamilies(t *testing.T) {
	cw := &countingWriter{}
	ing := NewIngester(cw, slog.Default(), WithHighCardinalityFilter())

	if err := ing.Push(context.Background(), 42, "embedded:gopsutil", mixedBatch()); err != nil {
		t.Fatalf("Push: %v", err)
	}
	calls, got := cw.snapshot()
	if calls != 1 {
		t.Fatalf("Write calls = %d, want 1", calls)
	}
	want := []string{
		"node_cpu_seconds_total",
		"http_server_request_duration_seconds_bucket",
		"namedprocess_namegroup_memory_bytes",
	}
	if len(got) != len(want) {
		t.Fatalf("kept %d samples, want %d", len(got), len(want))
	}
	for i, name := range want {
		if v := labelMap(got[i].Labels)["__name__"]; v != name {
			t.Errorf("kept[%d] = %q, want %q", i, v, name)
		}
	}
	if ing.DroppedSamples() != 2 {
		t.Errorf("DroppedSamples = %d, want 2", ing.DroppedSamples())
	}
	// Cloud labels must still be attached to kept samples.
	if m := labelMap(got[0].Labels); m["device_id"] != "42" || m["ongrid_source"] != "embedded:gopsutil" {
		t.Errorf("cloud labels missing on kept sample: %v", m)
	}
}

func TestIngester_FullyFilteredBatchSkipsWrite(t *testing.T) {
	cw := &countingWriter{}
	ing := NewIngester(cw, slog.Default(), WithHighCardinalityFilter())

	all := []tunnel.PromSample{
		{Name: "namedprocess_namegroup_thread_io_bytes_total", Value: 1, TsMs: 1000},
		{Name: "http_client_response_body_size_bytes_bucket", Value: 2, TsMs: 1000},
	}
	if err := ing.Push(context.Background(), 7, "scrape:local", all); err != nil {
		t.Fatalf("Push: %v", err)
	}
	if calls, _ := cw.snapshot(); calls != 0 {
		t.Errorf("Write called %d times on a fully filtered batch, want 0", calls)
	}
	if ing.DroppedSamples() != 2 {
		t.Errorf("DroppedSamples = %d, want 2", ing.DroppedSamples())
	}
}

func TestIngester_FilterAppliesToKubernetesAndAccumulates(t *testing.T) {
	cw := &countingWriter{}
	ing := NewIngester(cw, slog.Default(), WithHighCardinalityFilter())

	batch := []tunnel.PromSample{
		{Name: "node_cpu_seconds_total", Value: 1, TsMs: 1000},
		{Name: "http_client_request_duration_seconds_bucket", Value: 2, TsMs: 1000},
	}
	if err := ing.PushKubernetes(context.Background(), 9, "k8s:controller", batch); err != nil {
		t.Fatalf("PushKubernetes: %v", err)
	}
	if err := ing.Push(context.Background(), 42, "embedded:gopsutil", mixedBatch()); err != nil {
		t.Fatalf("Push: %v", err)
	}
	if got := ing.DroppedSamples(); got != 3 {
		t.Errorf("DroppedSamples = %d, want 3 across both paths", got)
	}
	_, last := cw.snapshot()
	if len(last) != 3 {
		t.Fatalf("last batch kept %d samples, want 3", len(last))
	}
	if m := labelMap(last[0].Labels); m["device_id"] != "42" || m["cluster_id"] != "" {
		t.Errorf("host batch labels wrong: %v", m)
	}
}
