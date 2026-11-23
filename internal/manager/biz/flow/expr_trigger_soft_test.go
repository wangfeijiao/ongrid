package flow

import (
	"strings"
	"testing"
)

// TestTriggerMissingFieldRendersEmpty locks in the fix for manual flow runs:
// the UI "Run" button POSTs an empty input, so {{trigger.*}} has no payload.
// trigger is external input and must degrade to "" rather than aborting the
// run with `field "rule" missing`.
func TestTriggerMissingFieldRendersEmpty(t *testing.T) {
	rc := &RunContext{Trigger: map[string]any{}, Nodes: map[string]any{}, Vars: map[string]any{}}

	// A single whole-string template resolves to the native value (empty string).
	got, err := rc.ResolveString("{{trigger.rule}}")
	if err != nil {
		t.Fatalf("whole-string trigger ref must not error: %v", err)
	}
	if got != "" {
		t.Fatalf("want empty string, got %#v", got)
	}

	// Mixed text renders the missing field as "".
	got, err = rc.ResolveString("规则={{trigger.rule}}，设备={{trigger.device_id}}")
	if err != nil {
		t.Fatalf("mixed trigger ref must not error: %v", err)
	}
	if got != "规则=，设备=" {
		t.Fatalf("want empty interpolation, got %q", got)
	}

	// A nil Trigger map is equally soft.
	rcNil := &RunContext{Nodes: map[string]any{}, Vars: map[string]any{}}
	if got, err := rcNil.ResolveString("{{trigger.labels.service_name}}"); err != nil || got != "" {
		t.Fatalf("nil trigger nested ref = %#v, err %v", got, err)
	}
}

// TestTriggerWrongTypeRendersEmpty: index/type mismatches on the trigger root
// degrade to "" as well (manual input shape is not guaranteed).
func TestTriggerWrongTypeRendersEmpty(t *testing.T) {
	rc := &RunContext{
		Trigger: map[string]any{"rule": "disk"},
		Nodes:   map[string]any{},
		Vars:    map[string]any{},
	}
	for _, path := range []string{"{{trigger.rule.foo}}", "{{trigger.missing[0]}}"} {
		got, err := rc.ResolveString(path)
		if err != nil || got != "" {
			t.Fatalf("%s = %#v, err %v; want empty with no error", path, got, err)
		}
	}
}

// TestNodesAndVarsStayStrict: the soft degradation must apply ONLY to trigger.
// A missing node/vars ref means a broken wire or typo and still surfaces.
func TestNodesAndVarsStayStrict(t *testing.T) {
	rc := &RunContext{Trigger: map[string]any{}, Nodes: map[string]any{}, Vars: map[string]any{}}
	cases := []string{
		"{{nodes.a1.output.answer}}",
		"{{vars.nope}}",
	}
	for _, path := range cases {
		if _, err := rc.ResolveString(path); err == nil {
			t.Fatalf("%s must still error, got nil", path)
		}
	}

	// An ill-typed nodes field is still strict too.
	rc.Nodes["a1"] = "not-a-map"
	if _, err := rc.ResolveString("{{nodes.a1.output.answer}}"); err == nil {
		t.Fatal("ill-typed nodes field must still error")
	}

	// Guard the exact error text we just softened on trigger, so a regression
	// that re-hardens trigger cannot pass unnoticed.
	if _, err := rc.lookup("trigger.rule"); err != nil {
		t.Fatalf("trigger.rule lookups must not error: %v", err)
	}
	if _, err := rc.lookup("vars.nope"); err == nil || !strings.Contains(err.Error(), "field") {
		t.Fatalf("vars.nope must keep a strict field error, got %v", err)
	}
}
