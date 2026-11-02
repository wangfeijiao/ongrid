package autoapm

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

// targetsOfCount builds count distinct host targets. Every target needs a
// unique executable+port pair, so the loop varies both.
func targetsOfCount(count int) []Target {
	out := make([]Target, count)
	for i := range out {
		out[i] = Target{Executable: fmt.Sprintf("/opt/app-%d", i+1), Port: uint16(i + 1), ServiceName: "app"}
	}
	return out
}

// rulesOfCount builds count non-overlapping workload selectors in one namespace.
func rulesOfCount(count int) []KubernetesRule {
	out := make([]KubernetesRule, count)
	for i := range out {
		out[i] = KubernetesRule{Namespace: "shop", WorkloadKind: "Deployment", WorkloadName: fmt.Sprintf("api-%d", i+1)}
	}
	return out
}

func TestHostTargetCap(t *testing.T) {
	if parsed, err := Parse(Spec{Targets: targetsOfCount(MaxTargets)}.Map()); err != nil || len(parsed.Targets) != MaxTargets {
		t.Fatalf("cap-many targets rejected: %d %v", len(parsed.Targets), err)
	}
	_, err := Parse(Spec{Targets: targetsOfCount(MaxTargets + 1)}.Map())
	if err == nil || !strings.Contains(err.Error(), "at most 350 targets") {
		t.Fatalf("overflowing targets accepted: %v", err)
	}
}

func TestKubernetesRuleCap(t *testing.T) {
	if parsed, err := Parse(Spec{Kubernetes: &Kubernetes{Rules: rulesOfCount(MaxTargets)}}.Map()); err != nil || len(parsed.Kubernetes.Rules) != MaxTargets {
		t.Fatalf("cap-many Kubernetes rules rejected: %d %v", len(parsed.Kubernetes.Rules), err)
	}
	_, err := Parse(Spec{Kubernetes: &Kubernetes{Rules: rulesOfCount(MaxTargets + 1)}}.Map())
	if err == nil || !strings.Contains(err.Error(), "at most 350 Kubernetes rules") {
		t.Fatalf("overflowing Kubernetes rules accepted: %v", err)
	}
}

// TestObservedRequirementFitsSpecJSONColumn pins the budget that motivated the
// cap rather than the cap itself: MaxTargets is a count, while MySQL TEXT
// (65535 bytes) is a byte budget, so the meaningful invariant is that the
// largest selection actually observed in production still serializes well
// inside the column. Adding fields or longer defaults per target fails here
// before it fails a save with Error 1406.
func TestObservedRequirementFitsSpecJSONColumn(t *testing.T) {
	const observedTargets = 105
	targets := make([]Target, observedTargets)
	for i := range targets {
		target := Target{
			Executable:       fmt.Sprintf("/opt/service-%d", i+1),
			Port:             uint16(i + 1),
			ServiceName:      fmt.Sprintf("service-%d", i+1),
			ServiceNamespace: "production",
			LogPath:          fmt.Sprintf("/var/log/service-%d/app.log", i+1),
			Environment:      "production",
		}
		target.TargetID = target.ID()
		targets[i] = target
	}
	body, err := json.Marshal(Spec{Environment: "production", Targets: targets}.Map())
	if err != nil {
		t.Fatal(err)
	}
	if len(body) > 65535 {
		t.Fatalf("%d targets with log paths serialize to %d bytes and no longer fit a MySQL TEXT column (65535); reduce per-target payload or migrate the spec columns to MEDIUMTEXT", observedTargets, len(body))
	}
}

// TestSpecByteLimit covers the write-path guard: a spec the column can hold is
// accepted, one byte over is rejected with the actual size in the message so the
// caller knows how much to remove, and the count cap alone is not a substitute
// for the byte check.
func TestSpecByteLimit(t *testing.T) {
	if MaxSpecBytes != 60000 {
		t.Fatalf("byte limit changed without re-checking the TEXT column: %d", MaxSpecBytes)
	}
	for _, size := range []int{MaxSpecBytes - 100, MaxSpecBytes} {
		if err := CheckSpecSize(make([]byte, size)); err != nil {
			t.Fatalf("%d bytes must be accepted: %v", size, err)
		}
	}
	for _, size := range []int{MaxSpecBytes + 1, MaxSpecBytes + 100} {
		err := CheckSpecSize(make([]byte, size))
		if err == nil || !strings.Contains(err.Error(), "60000") || !strings.Contains(err.Error(), fmt.Sprint(size)) {
			t.Fatalf("%d bytes must be rejected with both sizes in the message: %v", size, err)
		}
	}
	// MaxTargets alone is not a byte guarantee: the highest allowed count with
	// long log paths is a spec the guard has to reject.
	targets := make([]Target, MaxTargets)
	for i := range targets {
		targets[i] = Target{Executable: fmt.Sprintf("/opt/service-%d", i+1), Port: uint16(i + 1), ServiceName: fmt.Sprintf("service-%d", i+1), LogPath: "/var/log/" + strings.Repeat("a", 180) + fmt.Sprint(i)}
	}
	body, err := json.Marshal(Spec{Targets: targets}.Map())
	if err != nil {
		t.Fatal(err)
	}
	if err := CheckSpecSize(body); err == nil {
		t.Fatalf("%d targets with long log paths (%d bytes) must be rejected by the byte guard", MaxTargets, len(body))
	}
}

func TestTargetIdentitySurvivesParseAndSelectorEdits(t *testing.T) {
	target := Target{Executable: "/opt/orders", Port: 8080, ServiceName: "orders"}
	spec, err := Parse(Spec{Targets: []Target{target}}.Map())
	if err != nil || spec.Targets[0].TargetID == "" || spec.Targets[0].TargetID != target.ID() {
		t.Fatalf("legacy target identity: %+v %v", spec, err)
	}
	id := spec.Targets[0].TargetID
	spec.Targets[0].Executable, spec.Targets[0].Port = "/opt/orders-v2", 9090
	updated, err := Parse(spec.Map())
	if err != nil || updated.Targets[0].TargetID != id {
		t.Fatalf("saved target identity changed: %+v %v", updated, err)
	}
	updated.Targets[0].TargetID = "arbitrary"
	if _, err := Parse(updated.Map()); err == nil {
		t.Fatal("invalid target ID accepted")
	}
}

func TestSelectiveContract(t *testing.T) {
	if s, err := Parse(nil); err != nil || len(s.Targets) != 0 {
		t.Fatalf("empty selection must remain empty: %v %v", s, err)
	}
	valid := func() map[string]interface{} {
		return map[string]interface{}{"targets": []interface{}{map[string]interface{}{"executable": "/opt/orders", "port": 8080, "service_name": "orders"}}}
	}
	for _, tc := range []struct {
		name string
		edit func(map[string]interface{})
	}{
		{"unknown mode", func(m map[string]interface{}) { m["mode"] = "auto" }},
		{"unknown exporter", func(m map[string]interface{}) { m["endpoint"] = "https://other" }},
		{"environment substitution", func(m map[string]interface{}) { m["environment"] = "${SECRET}" }},
		{"ratio", func(m map[string]interface{}) { m["sample_ratio"] = 1.1 }},
		{"missing selector", func(m map[string]interface{}) {
			m["targets"] = []interface{}{map[string]interface{}{"service_name": "orders"}}
		}},
		{"port overflow", func(m map[string]interface{}) {
			m["targets"].([]interface{})[0].(map[string]interface{})["port"] = 65536
		}},
		{"self instrumentation", func(m map[string]interface{}) {
			m["targets"].([]interface{})[0].(map[string]interface{})["executable"] = "/usr/bin/ongrid-edge"
		}},
		{"duplicate", func(m map[string]interface{}) { ts := m["targets"].([]interface{}); m["targets"] = append(ts, ts[0]) }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			m := valid()
			tc.edit(m)
			if _, err := Parse(m); err == nil {
				t.Fatal("invalid config accepted")
			}
		})
	}
	if s, err := Parse(valid()); err != nil || s.Ratio() != 0.1 {
		t.Fatalf("valid: %v %v", s, err)
	}
}

func TestEnvironmentOverridesAndSystemProcessExclusion(t *testing.T) {
	for _, exe := range []string{"/usr/lib/systemd/systemd", "/usr/lib/systemd/systemd-resolved"} {
		if !Excluded(exe) {
			t.Fatalf("system process allowed: %s", exe)
		}
		if _, err := Parse(map[string]interface{}{"targets": []Target{{Executable: exe, Port: 22, ServiceName: "system"}}}); err == nil {
			t.Fatal("manual system target accepted")
		}
	}
	for _, exe := range []string{"/usr/bin/nginx", "/opt/orders", "/usr/bin/java", "/usr/bin/python3"} {
		if Excluded(exe) {
			t.Fatalf("business process excluded: %s", exe)
		}
	}
	targets := []Target{{Executable: "/opt/orders", Port: 8080, ServiceName: "orders", Environment: "test"}, {Executable: "/opt/orders", Port: 8081, ServiceName: "orders", ServiceNamespace: "prod", Environment: "production"}}
	if _, err := Parse(map[string]interface{}{"targets": targets}); err != nil {
		t.Fatal(err)
	}
	targets[1].ServiceNamespace = ""
	if _, err := Parse(map[string]interface{}{"targets": targets}); err == nil {
		t.Fatal("ambiguous identity environments accepted")
	}
	targets[0].Environment = "${SECRET}"
	if _, err := Parse(map[string]interface{}{"targets": targets[:1]}); err == nil {
		t.Fatal("environment substitution accepted")
	}
}

func TestKubernetesSelectorsAreBoundedAndUnambiguous(t *testing.T) {
	valid := Spec{Kubernetes: &Kubernetes{Rules: []KubernetesRule{{Namespace: "shop", WorkloadKind: "Deployment", WorkloadName: "api.v1", Container: "app"}}}}
	if parsed, err := Parse(valid.Map()); err != nil || parsed.Kubernetes.Rules[0].Container != "" {
		t.Fatalf("legacy container restriction retained: %+v %v", parsed, err)
	}
	for _, rules := range [][]KubernetesRule{
		{{Namespace: ".*"}},
		{{Namespace: "shop", WorkloadKind: "Pod", WorkloadName: "api"}},
		{{Namespace: "shop", WorkloadName: "api"}},
		{{Namespace: "shop"}, {Namespace: "shop", WorkloadKind: "Deployment", WorkloadName: "api"}},
		{{Namespace: "shop", WorkloadKind: "Deployment", WorkloadName: "api"}, {Namespace: "shop", WorkloadKind: "Deployment", WorkloadName: "api", Container: "app"}},
	} {
		spec := Spec{Kubernetes: &Kubernetes{Rules: rules}}
		if _, err := Parse(spec.Map()); err == nil {
			t.Fatalf("accepted unsafe selectors: %+v", rules)
		}
	}
	valid.Targets = []Target{{Executable: "/opt/app", Port: 8080, ServiceName: "app"}}
	if _, err := Parse(valid.Map()); err == nil {
		t.Fatal("mixed host and Kubernetes targets")
	}
	if _, err := Parse(map[string]interface{}{"kubernetes": map[string]interface{}{"rules": []interface{}{map[string]interface{}{"namespace": "shop", "service_namespace": "fake"}}}}); err == nil {
		t.Fatal("namespace override accepted")
	}
}

func TestServiceLogPaths(t *testing.T) {
	target := Target{Executable: "/opt/orders", Port: 8080, ServiceName: "orders"}
	for _, pattern := range []string{"/var/log/orders/*.log", "/opt/orders/logs/app.log", ""} {
		target.LogPath = pattern
		if _, err := Parse(Spec{Targets: []Target{target}}.Map()); err != nil {
			t.Fatal(err)
		}
	}
	for _, pattern := range []string{"relative.log", "/var/log/../secret", "/var/log/${SECRET}", "/var/log/a\n", " /var/log/a"} {
		target.LogPath = pattern
		if _, err := Parse(Spec{Targets: []Target{target}}.Map()); err == nil {
			t.Fatalf("accepted %q", pattern)
		}
	}
	target.LogPath = "/var/log/app.log"
	other := target
	other.Port, other.ServiceName = 8081, "inventory"
	if _, err := Parse(Spec{Targets: []Target{target, other}}.Map()); err == nil {
		t.Fatal("ambiguous log identity accepted")
	}
}
