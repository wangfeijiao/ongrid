package device

import "testing"

func TestParseVendorModel(t *testing.T) {
	tests := []struct {
		name        string
		description string
		objectID    string
		wantVendor  string
		wantModel   string
	}{
		{
			name: "H3C S7503E-M (production sample)",
			description: "H3C Comware Platform Software, Software Version 7.1.070, Release 7585P05\r\n" +
				"H3C S7503E-M\r\n" +
				"Copyright (c) 2004-2019 New H3C Technologies Co., Ltd. All rights reserved.",
			objectID:   ".1.3.6.1.4.1.25506.1.1504",
			wantVendor: "H3C",
			wantModel:  "S7503E-M",
		},
		{
			name:        "Huawei S5720 from OID",
			description: "Huawei Versatile Routing Platform Software\nHuawei S5720-36C-EI",
			objectID:    ".1.3.6.1.4.1.2011.2.23.100",
			wantVendor:  "Huawei",
			wantModel:   "S5720-36C-EI",
		},
		{
			name:        "Cisco WS-C2960 from description fallback",
			description: "Cisco IOS Software, C2960 Software (WS-C2960-24TC-L), Version 15.0",
			objectID:    "",
			wantVendor:  "Cisco",
			wantModel:   "C2960",
		},
		{
			name:        "Ruijie from OID",
			description: "Ruijie Network OS",
			objectID:    ".1.3.6.1.4.1.8882.1.1",
			wantVendor:  "Ruijie",
			wantModel:   "",
		},
		{
			name:        "unknown OID, no keywords",
			description: "SomeDevice Firmware 3.2.1",
			objectID:    ".1.3.6.1.4.1.99999.1.1",
			wantVendor:  "",
			wantModel:   "",
		},
		{
			name:        "empty input",
			description: "",
			objectID:    "",
			wantVendor:  "",
			wantModel:   "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			vendor, model := ParseVendorModel(tt.description, tt.objectID)
			if vendor != tt.wantVendor {
				t.Errorf("vendor = %q, want %q", vendor, tt.wantVendor)
			}
			if model != tt.wantModel {
				t.Errorf("model = %q, want %q", model, tt.wantModel)
			}
		})
	}
}

func TestVendorFromOID(t *testing.T) {
	cases := map[string]string{
		".1.3.6.1.4.1.25506.1.1504": "H3C",
		".1.3.6.1.4.1.2011.2.23":    "Huawei",
		".1.3.6.1.4.1.9.1.1234":     "Cisco",
		".1.3.6.1.4.1.8882":         "Ruijie",
		"1.3.6.1.4.1.25506.1.1504":  "H3C",
		".1.3.6.1.4.1.99999.1":      "",
		"":                          "",
	}
	for oid, want := range cases {
		if got := vendorFromOID(oid); got != want {
			t.Errorf("vendorFromOID(%q) = %q, want %q", oid, got, want)
		}
	}
}
