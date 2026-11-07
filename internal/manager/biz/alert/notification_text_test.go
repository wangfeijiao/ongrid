package alert

import (
	"strings"
	"testing"
)

func TestReadableTitleFallbackChain(t *testing.T) {
	cases := []struct {
		name     string
		ruleKey  string
		ruleName string
		want     string
	}{
		{name: "chinese rule name wins", ruleKey: "cpu_high", ruleName: "自定义 CPU 告警", want: "自定义 CPU 告警"},
		{name: "legacy english name uses dictionary", ruleKey: "scrape_down", ruleName: "Scrape Down", want: "监控采集目标不可达"},
		{name: "empty name uses dictionary", ruleKey: "scrape_down", ruleName: "", want: "监控采集目标不可达"},
		{name: "unknown english name kept", ruleKey: "my_rule", ruleName: "My rule", want: "My rule"},
		{name: "unknown empty name falls back to key", ruleKey: "my_rule", ruleName: "", want: "my_rule"},
		{name: "everything empty still non-empty", ruleKey: "", ruleName: "", want: "告警通知"},
		{name: "whitespace-only name", ruleKey: "swap_high", ruleName: "   ", want: "Swap 使用率过高"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := readableTitle(tc.ruleKey, tc.ruleName); got != tc.want {
				t.Fatalf("readableTitle(%q, %q) = %q, want %q", tc.ruleKey, tc.ruleName, got, tc.want)
			}
		})
	}
}

func TestReadableTitleEveryBuiltinIsChinese(t *testing.T) {
	for key, readable := range builtinRuleReadable {
		if readable.Title == "" || !containsHan(readable.Title) {
			t.Errorf("built-in %q has no Chinese title: %#v", key, readable)
		}
		if readable.Hint == "" || !containsHan(readable.Hint) {
			t.Errorf("built-in %q has no Chinese hint: %#v", key, readable)
		}
	}
}

func TestReadableHintFallback(t *testing.T) {
	if got := readableHint("scrape_down"); !strings.Contains(got, "采集目标") {
		t.Errorf("built-in hint = %q", got)
	}
	if got := readableHint("unknown_rule"); got != defaultReadableHint {
		t.Errorf("unknown hint = %q, want %q", got, defaultReadableHint)
	}
	if got := readableHint(""); got != defaultReadableHint {
		t.Errorf("empty key hint = %q, want %q", got, defaultReadableHint)
	}
}

func TestReadableObjectPrefersHumanLabels(t *testing.T) {
	cases := []struct {
		name   string
		labels map[string]string
		host   string
		want   string
	}{
		{
			name: "scrape_down custommetrics target",
			labels: map[string]string{
				"device_id": "28", "kind": "custom", "plugin": "custommetrics",
				"target_id": "autoapm", "target_name": "autoapm",
			},
			host: "i-vaaqvt1i / 10.7.30.49",
			want: "autoapm（自定义指标采集） · 主机 i-vaaqvt1i / 10.7.30.49",
		},
		{
			name:   "service label wins over instance",
			labels: map[string]string{"service": "orders", "instance": "10.0.0.1:9100"},
			host:   "",
			want:   "orders",
		},
		{
			name:   "unknown collector keeps raw labels",
			labels: map[string]string{"target_name": "db-1", "kind": "odbc", "plugin": "mssql"},
			host:   "srv-1",
			want:   "db-1（kind=odbc, plugin=mssql） · 主机 srv-1",
		},
		{
			name:   "no labels is safe",
			labels: nil,
			host:   "",
			want:   "未标注对象",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := readableObject(tc.labels, tc.host); got != tc.want {
				t.Fatalf("readableObject() = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestReadableDeviceRendersHostAndIP(t *testing.T) {
	cases := []struct {
		name     string
		identity DeviceIdentity
		want     string
	}{
		{name: "host and ip", identity: DeviceIdentity{Hostname: "host-a", IPAddress: "10.0.0.1"}, want: "host-a / 10.0.0.1"},
		{name: "name fallback", identity: DeviceIdentity{Name: "gateway-a"}, want: "gateway-a"},
		{name: "ip only", identity: DeviceIdentity{IPAddress: "10.0.0.2"}, want: "10.0.0.2"},
		{name: "empty", identity: DeviceIdentity{}, want: ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := readableDevice(tc.identity); got != tc.want {
				t.Fatalf("readableDevice() = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestReadableDetailPreservesTechnicalLine(t *testing.T) {
	summary := "scrape_down: up == 0 ⇒ device_id=28,kind=custom (value=0)"
	got := readableDetail(summary, 28, "i-vaaqvt1i (10.7.30.49)")
	for _, want := range []string{"scrape_down:", "up == 0", "kind=custom", "(value=0)", "device=i-vaaqvt1i (10.7.30.49)"} {
		if !strings.Contains(got, want) {
			t.Errorf("detail missing %q: %q", want, got)
		}
	}
	if strings.Contains(got, "device_id=28") {
		t.Errorf("raw device id was not substituted: %q", got)
	}

	// No resolver / no identity: keep the original line verbatim.
	if got := readableDetail(summary, 28, ""); got != summary {
		t.Errorf("without identity detail = %q, want %q", got, summary)
	}
	if got := readableDetail(summary, 0, "host-a"); got != summary {
		t.Errorf("without device id detail = %q, want %q", got, summary)
	}
	if got := readableDetail("", 0, ""); got != "" {
		t.Errorf("empty summary = %q, want empty", got)
	}
}

func TestContainsHan(t *testing.T) {
	if !containsHan("设备离线") || !containsHan("CPU 高负载") {
		t.Error("Chinese text should be detected")
	}
	if containsHan("Scrape Down") || containsHan("") || containsHan("123 > 85%") {
		t.Error("ASCII text should not be detected")
	}
}
