package notify

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// TestSeverityLabelLocalizedAndSafe covers the display-only severity tag.
// Message.Severity itself is never rewritten, so routing/filters still see
// the canonical enum.
func TestSeverityLabelLocalizedAndSafe(t *testing.T) {
	cases := []struct {
		sev  Severity
		want string
	}{
		{SeverityInfo, "提示"},
		{SeverityWarning, "警告"},
		{SeverityCritical, "严重"},
		{Severity("error"), "错误"},
		{Severity("fatal"), "致命"},
		{Severity("bogus"), "BOGUS"},
		{Severity(""), "告警"},
		{Severity("  notice  "), "NOTICE"},
	}
	for _, tc := range cases {
		if got := severityLabel(tc.sev); got != tc.want {
			t.Errorf("severityLabel(%q) = %q, want %q", tc.sev, got, tc.want)
		}
	}
}

// TestFormatTextKeepsSourceAndDedupeLines is the DingTalk safety contract:
// the bot has a "dedupe" custom keyword, so that line (and the source line)
// must survive the readability change byte-for-byte.
func TestFormatTextKeepsSourceAndDedupeLines(t *testing.T) {
	msg := Message{
		Subject:   "监控采集目标不可达",
		Body:      "对象：autoapm（自定义指标采集） · 主机 i-vaaqvt1i / 10.7.30.49\n说明：该采集目标已无法拉取，指标停止上报；请检查采集插件与目标进程。\n详情：scrape_down: up == 0 ⇒ device=i-vaaqvt1i (10.7.30.49),kind=custom (value=0)",
		Severity:  SeverityWarning,
		Source:    "monitoring_pipeline",
		DedupeKey: "pipeline:scrape_down:device_id=28,kind=custom,plugin=custommetrics,target_id=autoapm,target_name=autoapm",
	}
	got := formatText(msg)
	lines := strings.Split(got, "\n")

	if lines[0] != "[警告] 监控采集目标不可达" {
		t.Errorf("first line = %q", lines[0])
	}
	if len(lines) != 6 {
		t.Fatalf("lines = %d, want 6 (headline + 3 body + source + dedupe): %#v", len(lines), lines)
	}
	if lines[1] != "对象：autoapm（自定义指标采集） · 主机 i-vaaqvt1i / 10.7.30.49" {
		t.Errorf("对象 line = %q", lines[1])
	}
	if !strings.HasPrefix(lines[2], "说明：") || !strings.HasPrefix(lines[3], "详情：") {
		t.Errorf("body lines changed: %#v", lines)
	}
	if lines[4] != "source: monitoring_pipeline" {
		t.Errorf("source line = %q", lines[4])
	}
	if lines[5] != "dedupe: "+msg.DedupeKey {
		t.Errorf("dedupe line = %q", lines[5])
	}
	if !strings.Contains(got, "dedupe") {
		t.Fatal("DingTalk keyword 'dedupe' missing")
	}
	if !strings.Contains(got, "scrape_down: up == 0") || !strings.Contains(got, "(value=0)") {
		t.Error("original technical detail lost")
	}
}

func TestFormatTextOmitsEmptyOptionalLines(t *testing.T) {
	got := formatText(Message{Subject: "主机离线", Severity: SeverityCritical})
	if got != "[严重] 主机离线" {
		t.Errorf("formatText = %q", got)
	}
}

// TestTextChannelsDeliverReadableMessage proves every formatText-based
// channel still sends successfully and keeps the "dedupe" keyword.
func TestTextChannelsDeliverReadableMessage(t *testing.T) {
	msg := Message{
		Subject:   "监控采集目标不可达",
		Body:      "对象：autoapm · 主机 i-vaaqvt1i\n详情：up == 0 (value=0)",
		Severity:  SeverityWarning,
		Source:    "monitoring_pipeline",
		DedupeKey: "pipeline:scrape_down:device_id=28",
	}

	cases := []struct {
		name    string
		sender  func(endpoint string, client *http.Client) Sender
		extract func(t *testing.T, payload map[string]any) string
	}{
		{
			name: "dingtalk",
			sender: func(endpoint string, client *http.Client) Sender {
				return NewDingTalkSender("dingtalk", endpoint, "", client)
			},
			extract: textField("text", "content"),
		},
		{
			name: "wecom",
			sender: func(endpoint string, client *http.Client) Sender {
				return NewWeComSender("wecom", endpoint, client)
			},
			extract: textField("text", "content"),
		},
		{
			name: "feishu",
			sender: func(endpoint string, client *http.Client) Sender {
				return NewFeishuSender("feishu", endpoint, "", client)
			},
			extract: textField("content", "text"),
		},
		{
			name: "telegram",
			sender: func(endpoint string, client *http.Client) Sender {
				return NewTelegramSender("telegram", endpoint, "chat-1", client)
			},
			extract: func(_ *testing.T, payload map[string]any) string {
				s, _ := payload["text"].(string)
				return s
			},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var payload map[string]any
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
					t.Errorf("decode payload: %v", err)
				}
				w.WriteHeader(http.StatusOK)
			}))
			defer srv.Close()

			if err := tc.sender(srv.URL, srv.Client()).Send(context.Background(), msg); err != nil {
				t.Fatalf("Send: %v", err)
			}
			content := tc.extract(t, payload)
			for _, want := range []string{"[警告] 监控采集目标不可达", "source: monitoring_pipeline", "dedupe: " + msg.DedupeKey, "详情：up == 0"} {
				if !strings.Contains(content, want) {
					t.Errorf("content missing %q:\n%s", want, content)
				}
			}
		})
	}
}

func textField(outer, inner string) func(*testing.T, map[string]any) string {
	return func(t *testing.T, payload map[string]any) string {
		t.Helper()
		m, _ := payload[outer].(map[string]any)
		s, _ := m[inner].(string)
		return s
	}
}

// TestSlackAndGenericSendersUnaffectedByReadabilityChange locks the two
// channels that do NOT use formatText: Slack keeps its English uppercase
// fallback tag + attachment layout, Generic keeps the raw Message JSON.
func TestSlackAndGenericSendersUnaffectedByReadabilityChange(t *testing.T) {
	msg := Message{
		Subject:   "监控采集目标不可达",
		Body:      "对象：autoapm · 主机 i-vaaqvt1i\n详情：up == 0 (value=0)",
		Severity:  SeverityWarning,
		Source:    "monitoring_pipeline",
		DedupeKey: "pipeline:scrape_down:device_id=28",
	}

	t.Run("slack", func(t *testing.T) {
		var payload map[string]any
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
				t.Errorf("decode payload: %v", err)
			}
			w.WriteHeader(http.StatusOK)
		}))
		defer srv.Close()

		if err := NewSlackSender("slack", srv.URL, srv.Client()).Send(context.Background(), msg); err != nil {
			t.Fatalf("Slack Send: %v", err)
		}
		if payload["text"] != "[WARNING] 监控采集目标不可达" {
			t.Errorf("slack fallback text = %v", payload["text"])
		}
		atts, ok := payload["attachments"].([]any)
		if !ok || len(atts) != 1 {
			t.Fatalf("attachments = %v", payload["attachments"])
		}
		att := atts[0].(map[string]any)
		if att["title"] != msg.Subject || att["text"] != msg.Body {
			t.Errorf("attachment title/text = %v / %v", att["title"], att["text"])
		}
		fields, _ := att["fields"].([]any)
		foundDedupe := false
		for _, f := range fields {
			m := f.(map[string]any)
			if m["title"] == "Dedupe key" && m["value"] == msg.DedupeKey {
				foundDedupe = true
			}
		}
		if !foundDedupe {
			t.Error("Slack dedupe field lost")
		}
	})

	t.Run("generic", func(t *testing.T) {
		var payload Message
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
				t.Errorf("decode payload: %v", err)
			}
			w.WriteHeader(http.StatusOK)
		}))
		defer srv.Close()

		if err := NewGenericWebhookSender("generic", srv.URL, "", srv.Client()).Send(context.Background(), msg); err != nil {
			t.Fatalf("Generic Send: %v", err)
		}
		if payload.Subject != msg.Subject || payload.Body != msg.Body ||
			payload.Severity != SeverityWarning || payload.DedupeKey != msg.DedupeKey ||
			payload.Source != msg.Source {
			t.Errorf("generic payload changed: %#v", payload)
		}
	})
}
