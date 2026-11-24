package device

import (
	"regexp"
	"strings"
)

// vendorByEnterpriseOID maps the SNMP sysObjectID enterprise number (the
// 1.3.6.1.4.1.<enterprise> prefix) to a canonical vendor name. This is the
// most reliable source: unlike free-form sysDescr text, the enterprise number
// is assigned by IANA and is stable across models.
var vendorByEnterpriseOID = map[string]string{
	"25506":   "H3C",    // New H3C Technologies / 新华三
	"2011":    "Huawei", // Huawei Technologies
	"9":       "Cisco",  // Cisco Systems
	"8882":    "Ruijie", // Ruijie Networks / 锐捷
	"58453":   "Ruijie", // Ruijie (alternate enterprise ID)
	"25461":   "ZTE",    // ZTE / 中兴
	"3902":    "Dell",   // Dell
	"11":      "Nokia",  // Nokia (Alcatel-Lucent)
	"6373":    "Arista", // Arista Networks
	"14179":   "Aruba",  // Aruba Networks
	"25506.1": "H3C",    // keep explicit form readable in docs/tests
}

// vendorKeywords maps case-insensitive substrings commonly found in sysDescr
// to a canonical vendor name. Used as a fallback when sysObjectID is absent
// or unknown.
var vendorKeywords = []struct {
	needle string
	vendor string
}{
	{"h3c", "H3C"},
	{"comware", "H3C"},
	{"huawei", "Huawei"},
	{"vrp platform", "Huawei"},
	{"cisco", "Cisco"},
	{"ios software", "Cisco"},
	{"ruijie", "Ruijie"},
	{"zte", "ZTE"},
	{"dell", "Dell"},
	{"force10", "Dell"},
	{"arista", "Arista"},
	{"eos software", "Arista"},
	{"aruba", "Aruba"},
	{"juniper", "Juniper"},
	{"junos", "Juniper"},
	{"nokia", "Nokia"},
	{"alcatel", "Nokia"},
	{"tp-link", "TP-LINK"},
	{"d-link", "D-Link"},
	{"netgear", "NETGEAR"},
	{"mikrotik", "MikroTik"},
	{"routeros", "MikroTik"},
}

// modelTokenRe matches a plausible hardware model token: it must start with a
// letter and contain at least one digit, e.g. S7503E-M, WS-C2960-24TC-L,
// S5720-36C-EI, MSR3620, USG6525E, CE6857, EX4300-48P.
var modelTokenRe = regexp.MustCompile(`[A-Za-z][A-Za-z0-9]*-[A-Za-z0-9-]*[0-9][A-Za-z0-9-]*|[A-Za-z]{1,6}[0-9][A-Za-z0-9-]*`)

// modelStopWords are token prefixes that are software/copyright noise rather
// than a hardware model. A candidate token beginning with one of these is
// rejected.
var modelStopWords = []string{
	"software", "version", "release", "copyright", "allrights", "rights",
	"reserved", "technologies", "technology", "platform", "system", "comware",
	"vrp", "ios", "eos", "junos", "routeros", "softwareversion", "boot",
	"image", "flash", "license", "serial", "mac", "address", "uptime",
	"2026", "2019", "2018", "2017", "2016", "2015",
}

// ParseVendorModel derives a canonical vendor and hardware model from the SNMP
// sysDescription and sysObjectID values. It is intentionally conservative: it
// never guesses a model from a bare number and never overwrites a value the
// caller already obtained from a stronger identity source.
//
// Parameters:
//   - sysDescription: raw sysDescr.0 text (may be multi-line).
//   - sysObjectID: raw sysObjectID.0 text, e.g. ".1.3.6.1.4.1.25506.1.1504".
//
// Returns (vendor, model); either may be empty if it cannot be determined.
func ParseVendorModel(sysDescription, sysObjectID string) (string, string) {
	vendor := vendorFromOID(sysObjectID)
	if vendor == "" {
		vendor = vendorFromDescription(sysDescription)
	}
	model := modelFromDescription(sysDescription, vendor)
	return vendor, model
}

func vendorFromOID(oid string) string {
	oid = strings.TrimSpace(oid)
	const prefix = "1.3.6.1.4.1."
	// sysObjectID may arrive with or without a leading dot.
	oid = strings.TrimPrefix(oid, ".")
	if !strings.HasPrefix(oid, prefix) {
		return ""
	}
	rest := strings.TrimPrefix(oid, prefix)
	// enterprise number is the first arc after the private enterprise root.
	arc := rest
	if idx := strings.IndexAny(rest, ". "); idx >= 0 {
		arc = rest[:idx]
	}
	if v, ok := vendorByEnterpriseOID[arc]; ok {
		return v
	}
	return ""
}

func vendorFromDescription(description string) string {
	lower := strings.ToLower(description)
	for _, kv := range vendorKeywords {
		if strings.Contains(lower, kv.needle) {
			return kv.vendor
		}
	}
	return ""
}

func modelFromDescription(description, vendor string) string {
	lowerPkg := strings.ToLower(vendor)
	for _, line := range strings.Split(description, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		lower := strings.ToLower(line)
		// Skip lines that are clearly software/version/copyright boilerplate.
		if strings.Contains(lower, "copyright") || strings.Contains(lower, "software version") ||
			strings.Contains(lower, "all rights reserved") || strings.HasPrefix(lower, "release ") {
			continue
		}
		for _, token := range modelTokenRe.FindAllString(line, -1) {
			tokenLower := strings.ToLower(token)
			stop := false
			for _, w := range modelStopWords {
				if strings.HasPrefix(tokenLower, w) {
					stop = true
					break
				}
			}
			if stop {
				continue
			}
			// A bare vendor name is not a model.
			if tokenLower == lowerPkg {
				continue
			}
			// Ignore very short or purely numeric tokens.
			if len(token) < 3 {
				continue
			}
			// Ignore tokens with no digit (e.g. "Software", "Platform").
			hasDigit := false
			for _, r := range token {
				if r >= '0' && r <= '9' {
					hasDigit = true
					break
				}
			}
			if !hasDigit {
				continue
			}
			return strings.ToUpper(token)
		}
	}
	return ""
}
