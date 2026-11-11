package promwrite

import "strings"

// High-cardinality sample filtering lives at this single boundary because
// buildPromSamples sees every edge-pushed sample (both host Push and
// PushKubernetes) before it is handed to remote_write. Filtering here means
// one change covers all 56 edges — no per-edge plugin spec rollout, and no
// dependency on Prometheus scrape relabel rules (which do not apply to the
// remote_write receiver).
//
// The list is deliberately conservative. Everything kept below is either
// referenced by the shipped alert rules, by the Monitor dashboard, or has a
// low enough cardinality that dropping it buys nothing:
//
//   - node_* (all host metrics — Monitor panels + alert rules)
//   - namedprocess_namegroup_cpu_seconds_total / memory_bytes /
//     num_procs / num_threads / open_filedesc / read|write_bytes_total
//     (process-level, used by Monitor's process panels)
//   - http_server_request_duration_seconds_bucket / _count / _sum
//     (APM latency + http_success_ratio alerts)
//   - traces_spanmetrics_latency_bucket / _calls_total (alert rules)
//   - redis_commands_latencies_usec_bucket,
//     ongrid_http_request_duration_seconds_bucket (own metrics, low cardinality)
//   - all _sum / _count companions (a few hundred series each)
//
// Only the ingress-side families below are dropped. They accounted for
// ~120k of ~394k head series (≈31%) on the 2026-11-10 capacity audit while
// carrying almost no operational value.

// droppedExact matches whole metric names.
var droppedExact = map[string]struct{}{
	// process-exporter thread-level metrics: one series per OS thread per
	// process. The `namedprocess_namegroup_thread_` prefix below catches
	// the singular forms; threads_wchan is plural and needs its own entry.
	"namedprocess_namegroup_threads_wchan": {},

	// autoapm/OBI request and response SIZE histograms. Four families,
	// ~28.6k series. Request/response sizes are not used by dashboards or
	// alerts, and their bucket × route × status labels are the main
	// cardinality amplifier on the busiest devices.
	"http_client_request_body_size_bytes_bucket":  {},
	"http_client_response_body_size_bytes_bucket": {},
	"http_server_request_body_size_bytes_bucket":  {},
	"http_server_response_body_size_bytes_bucket": {},

	// Client-side duration histogram (~10.8k series). Server-side duration
	// is retained, so the same request paths remain observable.
	"http_client_request_duration_seconds_bucket": {},
	"rpc_client_call_duration_seconds_bucket":     {},
	"v8js_gc_duration_seconds_bucket":             {},
}

// droppedPrefixes matches metric-name prefixes (thread-level families).
var droppedPrefixes = []string{
	"namedprocess_namegroup_thread_",
}

// shouldDropHighCardinalitySample reports whether a sample should be
// discarded before remote_write. It is a pure, cheap predicate: one map
// lookup plus at most one prefix check per sample.
func shouldDropHighCardinalitySample(name string) bool {
	if _, ok := droppedExact[name]; ok {
		return true
	}
	for _, prefix := range droppedPrefixes {
		if strings.HasPrefix(name, prefix) {
			return true
		}
	}
	return false
}
