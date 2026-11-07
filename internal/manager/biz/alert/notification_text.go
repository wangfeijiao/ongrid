package alert

import (
	"fmt"
	"strings"
)

// ruleReadable is the operator-facing Chinese text for one built-in rule.
// Title is the short notification headline; Hint is a single sentence of
// "what happened + what to do". Both are display-only: dedupe keys,
// incident ownership, inhibition and recovery never read them.
type ruleReadable struct {
	Title string
	Hint  string
}

// builtinRuleReadable maps a stable rule_key to its Chinese notification
// text. Keying on rule_key (rather than the localized rule name) matters
// because UpsertBuiltinRule never overwrites an existing row — production
// alert_rules rows can still carry the legacy English "Scrape Down" name —
// and because alert_rules.annotations_json / alert_incidents.description
// are empty, so there is no persisted operator hint to fall back to.
//
// Rules absent from this map (user-created or newly-added built-ins) fall
// back to rule.Name, then rule_key — see readableTitle and readableHint.
var builtinRuleReadable = map[string]ruleReadable{
	"cpu_high": {
		Title: "CPU 高负载",
		Hint:  "主机 CPU 使用率持续超过阈值；请检查占用最高的进程，必要时扩容或调整负载。",
	},
	"mem_high": {
		Title: "内存高占用",
		Hint:  "主机可用内存持续偏低；请排查内存占用进程，避免触发 OOM。",
	},
	"disk_high": {
		Title: "磁盘高占用",
		Hint:  "磁盘使用率超过阈值；请清理日志与临时文件，或扩容磁盘。",
	},
	"load1_high": {
		Title: "系统负载过高",
		Hint:  "主机 1 分钟平均负载超过阈值；请检查 CPU、IO 与排队进程。",
	},
	"device_offline": {
		Title: "设备离线",
		Hint:  "该设备已超过心跳超时时间未上报；请检查主机电源、网络与 edge 采集进程。",
	},
	// Legacy key retained by pre-split installs; migrate.go re-keys rows to
	// device_offline but old incidents can still carry it.
	"edge_offline": {
		Title: "设备离线",
		Hint:  "该设备已超过心跳超时时间未上报；请检查主机电源、网络与 edge 采集进程。",
	},
	"scrape_down": {
		Title: "监控采集目标不可达",
		Hint:  "该采集目标已无法拉取，指标停止上报；请检查采集插件与目标进程。",
	},
	"prom_ingest_fail": {
		Title: "Prom 写入失败",
		Hint:  "指标远程写入持续失败，监控数据可能缺失；请检查远端存储与网络连通性。",
	},
	"disk_full_warning": {
		Title: "磁盘空间不足",
		Hint:  "磁盘使用率已超过 85%；请及时清理或扩容，避免写入失败。",
	},
	"cpu_high_default": {
		Title: "CPU 高负载",
		Hint:  "主机 CPU 使用率持续超过阈值；请检查占用最高的进程，必要时扩容或调整负载。",
	},
	"swap_high": {
		Title: "Swap 使用率过高",
		Hint:  "主机开始大量使用交换分区，通常意味着内存压力；请排查内存占用进程。",
	},
	"fd_exhaustion": {
		Title: "文件描述符接近耗尽",
		Hint:  "已分配文件描述符接近系统上限；请检查是否存在句柄泄漏并调整上限。",
	},
}

// defaultReadableHint is used for rules with no dictionary entry (typically
// user-created rules), so the notification still tells the operator what to
// do next instead of silently dropping the "说明" line.
const defaultReadableHint = "告警条件已触发；请结合下方详情与告警事件定位对象并处理。"

// collectorKindLabels renders common monitoring_pipeline collector labels
// as a short Chinese descriptor. Unknown values are kept verbatim by
// readableObjectKind so no diagnostic information is lost.
var collectorKindLabels = map[string]string{
	"custom":        "自定义指标采集",
	"custommetrics": "自定义指标采集",
	"host":          "主机指标采集",
	"node":          "主机指标采集",
	"prometheus":    "Prometheus 采集",
}

// readableTitle resolves the notification headline with a graceful
// fallback chain. A Chinese rule name (including admin edits) wins so the
// operator's own naming is respected; built-in Chinese text covers legacy
// English names such as "Scrape Down"; an English name is still better than
// a bare rule_key; the final literal keeps the subject non-empty so the
// router never drops the message.
func readableTitle(ruleKey, ruleName string) string {
	name := strings.TrimSpace(ruleName)
	if containsHan(name) {
		return name
	}
	if builtin, ok := builtinRuleReadable[strings.TrimSpace(ruleKey)]; ok && builtin.Title != "" {
		return builtin.Title
	}
	if name != "" {
		return name
	}
	if key := strings.TrimSpace(ruleKey); key != "" {
		return key
	}
	return "告警通知"
}

// readableHint returns the per-rule Chinese explanation, or a generic
// operator hint for rules outside the built-in dictionary.
func readableHint(ruleKey string) string {
	if builtin, ok := builtinRuleReadable[strings.TrimSpace(ruleKey)]; ok && builtin.Hint != "" {
		return builtin.Hint
	}
	return defaultReadableHint
}

// readableObject builds the "对象" line. It prefers human-readable
// target/service labels over raw ids, and appends the resolved host when a
// device identity is available.
func readableObject(labels map[string]string, host string) string {
	name := firstNonEmptyLabel(labels,
		"target_name", "service", "service_instance_id", "instance",
		"job", "target_id", "device_name", "edge_name",
	)
	if name == "" {
		name = "未标注对象"
	}
	var b strings.Builder
	b.WriteString(name)
	if kind := readableObjectKind(labels); kind != "" {
		b.WriteString("（")
		b.WriteString(kind)
		b.WriteString("）")
	}
	if host != "" {
		b.WriteString(" · 主机 ")
		b.WriteString(host)
	}
	return b.String()
}

// readableObjectKind turns collector labels into a short Chinese
// descriptor. Unknown collectors keep their raw key=value pairs so the
// operator can still tell what produced the series.
func readableObjectKind(labels map[string]string) string {
	for _, key := range []string{"kind", "plugin"} {
		v := strings.ToLower(strings.TrimSpace(labels[key]))
		if v == "" {
			continue
		}
		if zh, ok := collectorKindLabels[v]; ok {
			return zh
		}
	}
	var raw []string
	for _, key := range []string{"kind", "plugin"} {
		if v := strings.TrimSpace(labels[key]); v != "" {
			raw = append(raw, key+"="+v)
		}
	}
	return strings.Join(raw, ", ")
}

// readableDevice renders a device identity as "name / ip" for the human
// "对象" line. deviceDisplay keeps its original "name (ip)" shape for the
// technical detail substitution, so existing consumers stay unchanged.
func readableDevice(identity DeviceIdentity) string {
	host := strings.TrimSpace(identity.Hostname)
	if host == "" {
		host = strings.TrimSpace(identity.Name)
	}
	ip := strings.TrimSpace(identity.IPAddress)
	switch {
	case host != "" && ip != "":
		return host + " / " + ip
	case host != "":
		return host
	default:
		return ip
	}
}

// readableDetail preserves the original pipeline summary (rule_key, expr,
// label set, value) as the technical "详情" line. When a device identity
// resolved, the numeric device_id label is replaced with the readable
// device display — the exact numeric id remains in the dedupe line.
func readableDetail(summary string, deviceID uint64, display string) string {
	detail := strings.TrimSpace(summary)
	if deviceID == 0 || display == "" || detail == "" {
		return detail
	}
	return strings.ReplaceAll(detail,
		fmt.Sprintf("device_id=%d", deviceID), "device="+display)
}

// readableBody assembles the human-readable part of the outbound message.
// The 详情 line always keeps the original technical summary; a missing
// summary still yields a non-empty body so no channel sees an empty message.
func readableBody(labels map[string]string, ruleKey, host, detail string) string {
	lines := make([]string, 0, 3)
	lines = append(lines, "对象："+readableObject(labels, host))
	if hint := readableHint(ruleKey); hint != "" {
		lines = append(lines, "说明："+hint)
	}
	if detail == "" {
		detail = "（无可用技术详情）"
	}
	lines = append(lines, "详情："+detail)
	return strings.Join(lines, "\n")
}

// firstNonEmptyLabel returns the first non-blank label value in key order.
func firstNonEmptyLabel(labels map[string]string, keys ...string) string {
	for _, key := range keys {
		if v := strings.TrimSpace(labels[key]); v != "" {
			return v
		}
	}
	return ""
}

// containsHan reports whether s contains a CJK unified ideograph. Used to
// prefer an already-localized rule name over the built-in dictionary.
func containsHan(s string) bool {
	for _, r := range s {
		if r >= '\u4e00' && r <= '\u9fff' {
			return true
		}
	}
	return false
}
