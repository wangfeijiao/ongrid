// alertText.ts — turn the raw alert strings the backend stores into
// something an on-call engineer can read without knowing PromQL.
//
// The alert engine persists one of two shapes in `incident.summary` and in
// `alert_events.title`:
//
//   1. metric rules
//      `apm_traffic_zero: sum by (service_name) (...) ⇒ service_name=java-8081 (value=0)`
//   2. log rules
//      `log_container_crashloop: log count 194 >= 60 in 15m (labels=device_id=2:40,...)`
//
// Rendering those verbatim (what the SPA used to do) gives a wall of English
// PromQL on the incident page, so we parse out `rule key / labels / value`
// and render a Chinese one-liner, keeping the original expression available
// behind a "查看查询表达式" disclosure.
//
// Anything we don't recognise (free-form messages, user-typed notes) is
// returned untouched — never invent a translation for content we can't parse.

export type AlertLabel = { key: string; value: string };

export type ParsedAlertText = {
  /** rule_key prefix, empty when the string isn't in a known shape. */
  ruleKey: string;
  /** Original PromQL / LogQL expression, metric shape only. */
  expr?: string;
  labels: AlertLabel[];
  /** `(value=…)` tail of a metric alert. */
  value?: string;
  /** log-count shape. */
  log?: { count: number; threshold: number; window: string };
  raw: string;
  /** true when we recognised one of the two engine shapes. */
  structured: boolean;
};

const HEAD_RE = /^([A-Za-z][\w-]*):\s*([\s\S]+)$/;
const LOG_RE = /log count\s+(\d+)\s*>=\s*(\d+)\s*in\s*([0-9]+\s*[smhdwy])/i;

function splitLabels(tail: string): AlertLabel[] {
  const out: AlertLabel[] = [];
  for (const part of tail.split(',')) {
    const seg = part.trim();
    if (!seg) continue;
    const eq = seg.indexOf('=');
    if (eq <= 0) continue;
    const key = seg.slice(0, eq).trim();
    const value = seg.slice(eq + 1).trim();
    if (!key) continue;
    out.push({ key, value });
  }
  return out;
}

export function parseAlertText(raw: string | null | undefined): ParsedAlertText {
  const text = (raw ?? '').trim();
  const empty: ParsedAlertText = { ruleKey: '', labels: [], raw: text, structured: false };
  if (!text) return empty;

  const head = HEAD_RE.exec(text);
  if (!head) return empty;
  const ruleKey = head[1];
  const body = head[2];

  // ── metric shape: <expr> ⇒ k=v,… (value=X) ──────────────────────────
  const arrow = body.lastIndexOf('⇒');
  if (arrow >= 0) {
    const expr = body.slice(0, arrow).trim();
    let tail = body.slice(arrow + 1).trim();
    let value: string | undefined;
    // alert_events.title is truncated by the writer, so the closing paren
    // and the value itself can be cut off (`(value=…`). Keep the label set
    // usable in that case instead of folding the junk into the last label.
    const vm = /^(.*?)\s*\(value=([^)]*)\)?\s*$/.exec(tail);
    if (vm) {
      tail = vm[1].trim();
      const raw = vm[2].trim();
      value = raw && !/^[…\u2026]+$|^\.\.\.$/.test(raw) ? raw : undefined;
    }
    return {
      ruleKey,
      expr: expr || undefined,
      labels: splitLabels(tail),
      value,
      raw: text,
      structured: true,
    };
  }

  // ── log-count shape ────────────────────────────────────────────────
  const lm = LOG_RE.exec(body);
  if (lm) {
    const labelTail = /\(labels=([\s\S]*)\)\s*$/.exec(body);
    return {
      ruleKey,
      labels: labelTail ? splitLabels(labelTail[1]) : [],
      log: { count: Number(lm[1]), threshold: Number(lm[2]), window: lm[3].replace(/\s+/g, '') },
      raw: text,
      structured: true,
    };
  }

  return { ...empty, ruleKey };
}

// LABEL_NAMES keeps the operator-facing vocabulary Chinese. Unknown labels
// fall through to the raw key — that's deliberate: a made-up translation of
// an unknown label is worse than leaving it as-is.
const LABEL_NAMES: Record<string, { zh: string; en: string; idLike?: boolean }> = {
  device_id: { zh: '设备', en: 'Device', idLike: true },
  cluster_id: { zh: '集群', en: 'Cluster', idLike: true },
  source_id: { zh: '日志源', en: 'Log source' },
  namespace: { zh: '命名空间', en: 'Namespace' },
  service_name: { zh: '服务', en: 'Service' },
  mountpoint: { zh: '挂载点', en: 'Mountpoint' },
  fstype: { zh: '文件系统', en: 'Filesystem' },
  instance: { zh: '实例', en: 'Instance' },
  job: { zh: '采集任务', en: 'Scrape job' },
  host: { zh: '主机', en: 'Host' },
  hostname: { zh: '主机名', en: 'Hostname' },
  port: { zh: '端口', en: 'Port' },
  pod: { zh: 'Pod', en: 'Pod' },
  container: { zh: '容器', en: 'Container' },
  node: { zh: '节点', en: 'Node' },
};

function formatValue(v: string): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return v;
  if (Number.isInteger(n)) return String(n);
  return String(Math.round(n * 100) / 100);
}

export type AlertTextParts = {
  /** One-line human summary. Empty when there is nothing readable. */
  headline: string;
  /** Secondary line (labels / thresholds). */
  detail: string;
  /** Raw expression, for the disclosure block. */
  expr?: string;
  /** Whether the source string matched a known engine shape. */
  structured: boolean;
};

type T = (zh: string, en: string) => string;

/**
 * Renders the parsed text into readable parts. `t` is the `tr` from
 * useI18n() so the caller re-renders on locale change.
 */
export function describeAlertText(raw: string | null | undefined, t: T): AlertTextParts {
  const p = parseAlertText(raw);
  if (!p.structured) {
    return { headline: p.raw, detail: '', structured: false };
  }

  const labelParts = p.labels.map((l) => {
    const meta = LABEL_NAMES[l.key];
    if (!meta) return `${l.key}=${l.value}`;
    const name = t(meta.zh, meta.en);
    return meta.idLike ? `${name} #${l.value}` : `${name} ${l.value}`;
  });

  if (p.log) {
    const headline = t(
      `${p.log.window} 内日志 ${p.log.count} 条（阈值 ${p.log.threshold} 条）`,
      `${p.log.count} log lines in ${p.log.window} (threshold ${p.log.threshold})`,
    );
    return { headline, detail: labelParts.join(' · '), structured: true };
  }

  const valueText =
    p.value !== undefined && p.value !== ''
      ? t(`当前值 ${formatValue(p.value)}`, `value ${formatValue(p.value)}`)
      : '';
  const headline = [...labelParts, valueText].filter(Boolean).join(' · ');
  return {
    headline: headline || t('指标越过阈值触发', 'Metric crossed its threshold'),
    detail: '',
    expr: p.expr,
    structured: true,
  };
}

// REASON_TEXT translates the engine's machine reasons (`cooldown`,
// `silenced`, …). Notification channel names (e.g. "Ongrid通知") are user
// data and pass through unchanged.
const REASON_TEXT: Record<string, { zh: string; en: string }> = {
  cooldown: {
    zh: '通知冷却期内，本次触发未重复推送',
    en: 'Within notify cooldown — this firing was not re-sent',
  },
  dampening: {
    zh: '未达到发送策略的触发次数门槛，暂不推送',
    en: 'Below the send-policy fire threshold — held back',
  },
  silenced: { zh: '命中静默规则，未推送', en: 'Matched a silence — not sent' },
  inhibited: { zh: '被上游告警抑制，未推送', en: 'Inhibited by an upstream alert — not sent' },
  dedupe: { zh: '重复告警合并，未重复推送', en: 'Deduplicated — not re-sent' },
  reopen: { zh: '条件恢复后再次触发', en: 'Condition recurred after recovery' },
};

export function humanizeReason(reason: string | null | undefined, t: T): string {
  const r = (reason ?? '').trim();
  if (!r) return '';
  // Dampening reasons carry live counts, e.g.
  // `dampened: 2/3 fires in 1800s window` — spell out what it means.
  const damp = /^dampened:\s*(\d+)\/(\d+)\s*fires\s*in\s*(\d+)s\s*window$/i.exec(r);
  if (damp) {
    return t(
      `发送策略要求 ${damp[3]} 秒内触发 ${damp[2]} 次（当前 ${damp[1]} 次），暂不推送`,
      `Send policy needs ${damp[2]} firings in ${damp[3]}s (currently ${damp[1]}) — held back`,
    );
  }
  const m = REASON_TEXT[r.toLowerCase()];
  return m ? t(m.zh, m.en) : r;
}

// Notification failures come from the delivery layer as Go error strings,
// e.g. `retry attempt 5: notify: channel "Ongrid通知" not configured`.
// Translate the shapes we've seen; anything else passes through so we never
// hide the real error text from an operator.
const NOTIFY_FAIL_RE = /retry attempt (\d+): notify: channel "(.+?)" not configured/i;
export function humanizeNotifyMessage(message: string | null | undefined, t: T): string {
  const m = NOTIFY_FAIL_RE.exec((message ?? '').trim());
  if (!m) return (message ?? '').trim();
  return t(
    `第 ${m[1]} 次重试仍失败：通知渠道「${m[2]}」尚未配置`,
    `Retry ${m[1]} failed: channel "${m[2]}" is not configured`,
  );
}

// Badges on both the list and detail pages shared the raw enum values
// (`critical`, `open`), which read as English jargon to Chinese operators.
export function severityLabel(severity: string, t: T): string {
  if (severity === 'critical') return t('严重', 'Critical');
  if (severity === 'warning') return t('警告', 'Warning');
  if (severity === 'info') return t('提示', 'Info');
  return severity;
}

export function incidentStatusLabel(status: string, t: T): string {
  switch (status) {
    case 'open':
      return t('未处理', 'Open');
    case 'acknowledged':
      return t('已认领', 'Acknowledged');
    case 'silenced':
      return t('已静默', 'Silenced');
    case 'resolved':
      return t('已恢复', 'Resolved');
    default:
      return status;
  }
}
