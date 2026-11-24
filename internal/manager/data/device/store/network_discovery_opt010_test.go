package store

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestMergeObserverJSONDedupByEdge(t *testing.T) {
	// 同一 observer_edge_id 的两条证据应合并为一条，新的覆盖旧的。
	old := `[{"observer_edge_id":"1","ip_address":"10.7.30.254","observed_at":"2026-11-19T10:00:00Z","confidence":"20"},` +
		`{"observer_edge_id":"2","ip_address":"10.7.11.254","observed_at":"2026-11-19T10:01:00Z","confidence":"20"}]`
	incoming := `[{"observer_edge_id":"1","ip_address":"10.7.30.254","observed_at":"2026-11-19T16:00:00Z","confidence":"90"},` +
		`{"observer_edge_id":"3","ip_address":"10.7.12.254","observed_at":"2026-11-19T16:02:00Z","confidence":"20"}]`

	merged := mergeObserverJSON(old, incoming)
	var arr []map[string]string
	if err := json.Unmarshal([]byte(merged), &arr); err != nil {
		t.Fatalf("merged is not valid JSON: %v (%s)", err, merged)
	}
	if len(arr) != 3 {
		t.Fatalf("expected 3 observers after dedup, got %d: %s", len(arr), merged)
	}
	for _, item := range arr {
		if item["observer_edge_id"] == "1" && item["confidence"] != "90" {
			t.Fatalf("edge 1 should keep newest evidence (confidence 90), got %s", item["confidence"])
		}
	}
}

func TestSetAndExtractObservers(t *testing.T) {
	src := `{"sys_name":"JKXC_CoreSW01","lldp_chassis_id":"00:11:22"}`
	obs := `[{"observer_edge_id":"1","ip_address":"10.7.30.254"}]`
	out := setObservers(src, obs)
	var data map[string]string
	if err := json.Unmarshal([]byte(out), &data); err != nil {
		t.Fatal(err)
	}
	if data["sys_name"] != "JKXC_CoreSW01" || data["lldp_chassis_id"] != "00:11:22" {
		t.Fatalf("identity fields lost: %s", out)
	}
	got := extractObservers(out)
	if !strings.Contains(got, "10.7.30.254") {
		t.Fatalf("observer not extractable: %s", got)
	}
}

func TestMergeObserverJSONCaps(t *testing.T) {
	var arr []map[string]string
	for i := 0; i < 150; i++ {
		arr = append(arr, map[string]string{
			"observer_edge_id": string(rune('A' + i%26)) + string(rune('a' + i/26)),
			"observed_at":      "2026-11-19T10:00:00Z",
		})
	}
	b, _ := json.Marshal(arr)
	merged := mergeObserverJSON("[]", string(b))
	var out []map[string]string
	json.Unmarshal([]byte(merged), &out)
	if len(out) != maxObserversPerCandidate {
		t.Fatalf("expected cap %d, got %d", maxObserversPerCandidate, len(out))
	}
}
