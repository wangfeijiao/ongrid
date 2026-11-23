package device

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/ongridio/ongrid/internal/pkg/tunnel"
)

func TestOPT010KeyGlobalByMAC(t *testing.T) {
	rep := tunnel.NetworkDiscoveryCandidateReport{
		IPAddress: "10.7.30.254", MAC: "30:80:9B:39:E4:01",
		InterfaceName: "eth0", Source: "arp", SysName: "JKXC_CoreSW01",
	}
	now := time.Now()
	a, ok, _ := networkCandidateFromReport(11, rep, now)
	b, ok2, _ := networkCandidateFromReport(22, rep, now)
	if !ok || !ok2 { t.Fatal("candidate rejected") }
	if a.ObservationKey != b.ObservationKey {
		t.Fatalf("different edge produced different key: %s vs %s", a.ObservationKey, b.ObservationKey)
	}
	if !strings.HasPrefix(a.ObservationKey, "mac:30:80:9b:39:e4:01") {
		t.Fatalf("key not global MAC form: %s", a.ObservationKey)
	}
	var data map[string]string
	if err := json.Unmarshal([]byte(a.SourceDataJSON), &data); err != nil { t.Fatal(err) }
	if strings.TrimSpace(data["_observers"]) == "" {
		t.Fatalf("_observers missing: %s", a.SourceDataJSON)
	}
	if !strings.Contains(data["_observers"], "\"observer_edge_id\":\"11\"") {
		t.Fatalf("_observers missing edge id: %s", data["_observers"])
	}
}
