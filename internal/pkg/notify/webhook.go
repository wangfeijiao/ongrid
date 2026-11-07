package notify

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// responseCheck inspects a provider's HTTP response body after a 2xx
// status. DingTalk and WeCom answer HTTP 200 even when a message is
// rejected (errcode 310000 for keyword / sign / IP-policy failures), so
// the status line alone is not a delivery outcome. A nil check keeps
// providers that reply with plain text on the status-only path.
type responseCheck func(statusCode int, body []byte) error

const maxResponseBytes = 8 << 10

// DeliveryError carries the provider response behind a failed send so the
// alert pipeline can persist it in notification_deliveries.response_json.
type DeliveryError struct {
	StatusCode int
	Body       string
	Reason     string
}

func (e *DeliveryError) Error() string {
	return fmt.Sprintf("notify: provider rejected delivery (http %d): %s", e.StatusCode, e.Reason)
}

type webhookSender struct {
	name          string
	endpoint      string
	secret        string
	client        *http.Client
	buildBody     func(Message) (any, error)
	signTarget    func(endpoint, secret string, body []byte) (string, map[string]string, error)
	checkResponse responseCheck
}

// NewGenericWebhookSender posts the normalized Message JSON. When secret is
// configured it adds an HMAC signature header over the request body.
func NewGenericWebhookSender(name, endpoint, secret string, client *http.Client) Sender {
	return newWebhookSender(name, endpoint, secret, client, func(msg Message) (any, error) {
		return msg, nil
	}, signGenericWebhook)
}

// NewSlackSender posts to a Slack incoming webhook in the attachments
// format so the alert renders with severity-tinted color bar + structured
// fields (Severity / Source / Rule / Incident / Device / Dedupe) instead
// of an unstyled paragraph. Slack incoming webhooks ignore any secret —
// the credential is the URL itself — so the secret field is silently
// dropped at the channel-builder layer.
func NewSlackSender(name, endpoint string, client *http.Client) Sender {
	return newWebhookSender(name, endpoint, "", client, func(msg Message) (any, error) {
		return formatSlack(msg), nil
	}, nil)
}

// formatSlack renders one Message as a Slack incoming-webhook payload using
// the attachments format. We pick attachments over Block Kit because:
//   - it carries the colored side-bar that operators read as "how bad",
//   - it's the universally-supported format (Block Kit needs newer apps),
//   - the schema is JSON-flat and easy to test.
//
// "text" at the top stays populated with a one-line summary so Slack's own
// notification preview (push, sidebar, email digest) shows something useful
// even when the recipient client strips attachments.
func formatSlack(msg Message) map[string]any {
	sevUpper := strings.ToUpper(string(msg.Severity))
	if sevUpper == "" {
		sevUpper = "ALERT"
	}
	summary := fmt.Sprintf("[%s] %s", sevUpper, msg.Subject)

	att := map[string]any{
		"color":    slackColor(msg.Severity),
		"fallback": summary,
		"title":    nonEmpty(msg.Subject, sevUpper),
	}
	if msg.Body != "" {
		att["text"] = msg.Body
		att["mrkdwn_in"] = []string{"text"}
	}

	fields := make([]map[string]any, 0, 6)
	addField := func(title, value string, short bool) {
		if value == "" {
			return
		}
		fields = append(fields, map[string]any{
			"title": title,
			"value": value,
			"short": short,
		})
	}
	addField("Severity", sevUpper, true)
	addField("Source", msg.Source, true)
	if msg.Labels != nil {
		// Surface the alert-pipeline labels operators care about as
		// short fields; the remaining labels stay out of the message
		// to keep the card readable. Rule/incident/device are the same
		// breakdown the incident detail page leads with.
		addField("Rule", msg.Labels["rule"], true)
		if id := msg.Labels["incident_id"]; id != "" {
			addField("Incident", "#"+id, true)
		}
		if did := msg.Labels["device_id"]; did != "" {
			addField("Device", "#"+did, true)
		}
	}
	// Dedupe key is the join key for ops chatter — keep full width so
	// long pipeline:rule:label-set strings stay readable.
	addField("Dedupe key", msg.DedupeKey, false)
	if len(fields) > 0 {
		att["fields"] = fields
	}

	att["footer"] = "ongrid"
	if !msg.OccurredAt.IsZero() {
		att["ts"] = msg.OccurredAt.Unix()
	}

	return map[string]any{
		"text":        summary,
		"attachments": []any{att},
	}
}

// slackColor maps a Severity onto the Slack attachment color rail. Critical
// uses the red the Slack sentinel "danger" resolves to but as a hex so we
// pin the shade across Slack client versions; same idea for warning.
// Unknown severities get a neutral slate so the rail still renders.
func slackColor(sev Severity) string {
	switch sev {
	case SeverityCritical:
		return "#d92f2f"
	case SeverityWarning:
		return "#f2c037"
	case SeverityInfo:
		return "#36a64f"
	default:
		return "#6f7a87"
	}
}

func nonEmpty(v, fallback string) string {
	if v != "" {
		return v
	}
	return fallback
}

// NewFeishuSender posts a text payload compatible with Feishu/Lark custom bots.
func NewFeishuSender(name, endpoint, secret string, client *http.Client) Sender {
	return newWebhookSender(name, endpoint, secret, client, func(msg Message) (any, error) {
		payload := map[string]any{
			"msg_type": "text",
			"content":  map[string]string{"text": formatText(msg)},
		}
		if secret != "" {
			ts := fmt.Sprintf("%d", time.Now().Unix())
			payload["timestamp"] = ts
			payload["sign"] = signFeishu(ts, secret)
		}
		return payload, nil
	}, nil)
}

// NewDingTalkSender posts a text payload compatible with DingTalk custom bots.
func NewDingTalkSender(name, endpoint, secret string, client *http.Client) Sender {
	s := newWebhookSender(name, endpoint, secret, client, func(msg Message) (any, error) {
		return map[string]any{
			"msgtype": "text",
			"text":    map[string]string{"content": formatText(msg)},
		}, nil
	}, signDingTalkURL)
	s.checkResponse = checkDingTalkResponse
	return s
}

// NewWeComSender posts a text payload compatible with 企业微信 (WeCom) group
// bots. Endpoint URL carries the bot key as a query param; the v1 wiring
// has no extra signing — the secret query string IS the credential. Same
// JSON shape as DingTalk: {"msgtype":"text","text":{"content":"..."}}.
func NewWeComSender(name, endpoint string, client *http.Client) Sender {
	s := newWebhookSender(name, endpoint, "", client, func(msg Message) (any, error) {
		return map[string]any{
			"msgtype": "text",
			"text":    map[string]string{"content": formatText(msg)},
		}, nil
	}, nil)
	s.checkResponse = checkDingTalkResponse
	return s
}

// NewTelegramSender posts to the Telegram Bot API sendMessage endpoint.
// endpoint is the full https://api.telegram.org/bot<TOKEN>/sendMessage URL
// (bot token in the path); chatID is the target chat, sent in the JSON
// body. Telegram's auth model differs from the webhook channels — token in
// the URL, chat_id in the body — so it doesn't use the secret/signing path.
func NewTelegramSender(name, endpoint, chatID string, client *http.Client) Sender {
	return newWebhookSender(name, endpoint, "", client, func(msg Message) (any, error) {
		return map[string]any{
			"chat_id": chatID,
			"text":    formatText(msg),
		}, nil
	}, nil)
}

// checkDingTalkResponse parses the {"errcode":N,"errmsg":"..."} envelope
// shared by DingTalk and WeCom custom bots. Empty / non-JSON bodies are
// tolerated so test doubles and proxies answering with plain text keep
// working; only an explicit non-zero errcode counts as a rejection.
func checkDingTalkResponse(_ int, body []byte) error {
	var out struct {
		ErrCode int    `json:"errcode"`
		ErrMsg  string `json:"errmsg"`
	}
	if len(body) == 0 || json.Unmarshal(body, &out) != nil {
		return nil
	}
	if out.ErrCode != 0 {
		return fmt.Errorf("errcode %d: %s", out.ErrCode, out.ErrMsg)
	}
	return nil
}

func newWebhookSender(
	name string,
	endpoint string,
	secret string,
	client *http.Client,
	buildBody func(Message) (any, error),
	signTarget func(endpoint, secret string, body []byte) (string, map[string]string, error),
) *webhookSender {
	if name == "" {
		name = "webhook"
	}
	if client == nil {
		client = http.DefaultClient
	}
	return &webhookSender{
		name:       name,
		endpoint:   endpoint,
		secret:     secret,
		client:     client,
		buildBody:  buildBody,
		signTarget: signTarget,
	}
}

func (s *webhookSender) Name() string { return s.name }

func (s *webhookSender) Send(ctx context.Context, msg Message) error {
	if s.endpoint == "" {
		return fmt.Errorf("endpoint required")
	}
	payload, err := s.buildBody(msg)
	if err != nil {
		return fmt.Errorf("build payload: %w", err)
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal payload: %w", err)
	}
	endpoint := s.endpoint
	headers := map[string]string{}
	if s.signTarget != nil {
		endpoint, headers, err = s.signTarget(s.endpoint, s.secret, body)
		if err != nil {
			return fmt.Errorf("sign request: %w", err)
		}
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("new request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "ongrid-notify/1.0")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := s.client.Do(req)
	if err != nil {
		return fmt.Errorf("post: %w", err)
	}
	defer resp.Body.Close()
	respBody, readErr := io.ReadAll(io.LimitReader(resp.Body, maxResponseBytes+1))
	if readErr != nil {
		return fmt.Errorf("read response body: %w", readErr)
	}
	tooLarge := len(respBody) > maxResponseBytes
	if tooLarge {
		respBody = respBody[:maxResponseBytes]
	}
	bodyText := string(respBody)
	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		return &DeliveryError{
			StatusCode: resp.StatusCode,
			Body:       bodyText,
			Reason:     fmt.Sprintf("unexpected status: %s", resp.Status),
		}
	}
	// A truncated body can no longer be parsed as a provider envelope;
	// treat it as a failure instead of silently accepting an unverified reply.
	if tooLarge {
		return &DeliveryError{
			StatusCode: resp.StatusCode,
			Body:       bodyText,
			Reason:     fmt.Sprintf("response body exceeds %d bytes", maxResponseBytes),
		}
	}
	if s.checkResponse != nil {
		if err := s.checkResponse(resp.StatusCode, respBody); err != nil {
			return &DeliveryError{
				StatusCode: resp.StatusCode,
				Body:       bodyText,
				Reason:     err.Error(),
			}
		}
	}
	return nil
}

// severityLabel renders the severity as a short Chinese tag for the text
// channels (DingTalk / WeCom / Feishu / Telegram / Generic). It is
// display-only: Message.Severity stays the canonical enum used by channel
// routing and severity filters, so no downstream matcher sees a new value.
// Unknown or legacy values are kept in uppercase English rather than
// dropped, and an empty severity still produces a visible tag.
func severityLabel(sev Severity) string {
	switch sev {
	case SeverityCritical:
		return "严重"
	case SeverityWarning:
		return "警告"
	case SeverityInfo:
		return "提示"
	case "error":
		return "错误"
	case "fatal":
		return "致命"
	default:
		upper := strings.ToUpper(strings.TrimSpace(string(sev)))
		if upper == "" {
			return "告警"
		}
		return upper
	}
}

func formatText(msg Message) string {
	parts := []string{fmt.Sprintf("[%s] %s", severityLabel(msg.Severity), msg.Subject)}
	if msg.Body != "" {
		parts = append(parts, msg.Body)
	}
	if msg.Source != "" {
		parts = append(parts, "source: "+msg.Source)
	}
	if msg.DedupeKey != "" {
		parts = append(parts, "dedupe: "+msg.DedupeKey)
	}
	return strings.Join(parts, "\n")
}

func signGenericWebhook(endpoint string, secret string, body []byte) (string, map[string]string, error) {
	headers := map[string]string{}
	if secret == "" {
		return endpoint, headers, nil
	}
	mac := hmac.New(sha256.New, []byte(secret))
	if _, err := mac.Write(body); err != nil {
		return "", nil, err
	}
	headers["X-Ongrid-Signature"] = "sha256=" + hex.EncodeToString(mac.Sum(nil))
	return endpoint, headers, nil
}

func signFeishu(timestamp, secret string) string {
	stringToSign := timestamp + "\n" + secret
	mac := hmac.New(sha256.New, []byte(stringToSign))
	return base64.StdEncoding.EncodeToString(mac.Sum(nil))
}

func signDingTalkURL(endpoint, secret string, _ []byte) (string, map[string]string, error) {
	if secret == "" {
		return endpoint, nil, nil
	}
	ts := fmt.Sprintf("%d", time.Now().UnixMilli())
	stringToSign := ts + "\n" + secret
	mac := hmac.New(sha256.New, []byte(secret))
	if _, err := mac.Write([]byte(stringToSign)); err != nil {
		return "", nil, err
	}
	sign := base64.StdEncoding.EncodeToString(mac.Sum(nil))
	u, err := url.Parse(endpoint)
	if err != nil {
		return "", nil, err
	}
	q := u.Query()
	q.Set("timestamp", ts)
	q.Set("sign", sign)
	u.RawQuery = q.Encode()
	return u.String(), nil, nil
}
