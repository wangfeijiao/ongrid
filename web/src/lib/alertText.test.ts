import { describe, expect, it } from 'vitest';
import { describeAlertText, humanizeNotifyMessage, humanizeReason, parseAlertText } from './alertText';

// The translator is injected by the caller, so tests don't depend on
// navigator.timezone auto-detection (jsdom would resolve to en-US).
const zh = (z: string) => z;

describe('parseAlertText', () => {
  it('拆出指标规则的标签、当前值与表达式', () => {
    const raw =
      'apm_traffic_zero: sum by (service_name) (increase(http_server_request_duration_seconds_count[5m])) == 0 ' +
      'and on(service_name) (sum by (service_name) (increase(http_server_request_duration_seconds_count[5m] offset 1d)) > 15) ' +
      '⇒ service_name=java-8081 (value=0)';
    const p = parseAlertText(raw);
    expect(p.structured).toBe(true);
    expect(p.ruleKey).toBe('apm_traffic_zero');
    expect(p.labels).toEqual([{ key: 'service_name', value: 'java-8081' }]);
    expect(p.value).toBe('0');
    expect(p.expr).toContain('increase(http_server_request_duration_seconds_count[5m])');
    expect(p.expr).not.toContain('⇒');
  });

  it('多标签 + 多行 PromQL 也能解析', () => {
    const raw = `disk_high: max by (device_id, mountpoint, fstype) (
  100 * (1
    - node_filesystem_avail_bytes / node_filesystem_size_bytes
  )
) > 85 ⇒ device_id=1117,fstype=ext4,mountpoint=/ (value=85.26934078494)`;
    const p = parseAlertText(raw);
    expect(p.structured).toBe(true);
    expect(p.labels.map((l) => l.key)).toEqual(['device_id', 'fstype', 'mountpoint']);
    expect(p.labels[2].value).toBe('/');
    expect(p.value).toBe('85.26934078494');
  });

  it('解析日志条数规则', () => {
    const raw =
      'log_container_crashloop: log count 194 >= 60 in 15m ' +
      '(labels=device_id=2:40,cluster_id=0:,source_id=8:journald,namespace=0:,service_name=0:)';
    const p = parseAlertText(raw);
    expect(p.structured).toBe(true);
    expect(p.log).toEqual({ count: 194, threshold: 60, window: '15m' });
    expect(p.labels[0]).toEqual({ key: 'device_id', value: '2:40' });
    expect(p.labels[2]).toEqual({ key: 'source_id', value: '8:journald' });
  });

  it('自由文本原样透传，不硬凑结构化', () => {
    const raw = '运维备注：机器已重启，观察 10 分钟';
    const p = parseAlertText(raw);
    expect(p.structured).toBe(false);
    expect(p.raw).toBe(raw);
    expect(describeAlertText(raw, zh).headline).toBe(raw);
  });

  it('空串不崩', () => {
    expect(parseAlertText('').structured).toBe(false);
    expect(parseAlertText(null).structured).toBe(false);
    expect(describeAlertText(null, zh).headline).toBe('');
  });

  it('事件标题被截断时仍能拿到标签，value 不输出残渣', () => {
    // alert_events.title is capped by the writer, so the tail arrives as
    // `(value=…` with no closing paren.
    const raw =
      'apm_traffic_zero: sum by (service_name) (increase(http_server_request_duration_seconds_count[5m])) == 0 ⇒ service_name=java-8081 (value=…';
    const p = parseAlertText(raw);
    expect(p.structured).toBe(true);
    expect(p.labels).toEqual([{ key: 'service_name', value: 'java-8081' }]);
    expect(p.value).toBeUndefined();
    expect(describeAlertText(raw, zh).headline).toBe('服务 java-8081');
  });
});

describe('describeAlertText', () => {
  it('指标规则渲染成中文一行话', () => {
    const raw = 'mem_high: max by (device_id) (100 * (1 - x / y)) > 90 ⇒ device_id=1118 (value=98.2767556509266)';
    const d = describeAlertText(raw, zh);
    expect(d.headline).toBe('设备 #1118 · 当前值 98.28');
    expect(d.expr).toContain('max by (device_id)');
    expect(d.expr ?? '').not.toContain('device_id=1118');
  });

  it('服务类标签用中文名', () => {
    const raw = 'apm_p95_high: histogram_quantile(0.95, x) > 1 ⇒ service_name=apollo-configservice (value=10)';
    expect(describeAlertText(raw, zh).headline).toBe('服务 apollo-configservice · 当前值 10');
  });

  it('日志规则渲染成条数与阈值', () => {
    const raw = 'log_volume_spike_journald: log count 33740 >= 20000 in 5m (labels=device_id=2:12,source_id=8:journald)';
    const d = describeAlertText(raw, zh);
    expect(d.headline).toBe('5m 内日志 33740 条（阈值 20000 条）');
    expect(d.detail).toBe('设备 #2:12 · 日志源 8:journald');
  });

  it('未知标签保留原始键，不臆造翻译', () => {
    const raw = 'custom_rule: up == 0 ⇒ weird_label=abc (value=0)';
    expect(describeAlertText(raw, zh).headline).toBe('weird_label=abc · 当前值 0');
  });
});

describe('humanizeReason', () => {
  it('把 cooldown 翻成人话', () => {
    expect(humanizeReason('cooldown', zh)).toBe('通知冷却期内，本次触发未重复推送');
  });
  it('渠道名等用户数据保持原样', () => {
    expect(humanizeReason('Ongrid通知', zh)).toBe('Ongrid通知');
  });
  it('空值返回空串', () => {
    expect(humanizeReason('', zh)).toBe('');
  });
  it('dampening 的计数原因讲清门槛与当前次数', () => {
    expect(humanizeReason('dampened: 2/3 fires in 1800s window', zh)).toBe(
      '发送策略要求 1800 秒内触发 3 次（当前 2 次），暂不推送',
    );
  });
});

describe('humanizeNotifyMessage', () => {
  it('把渠道未配置的英文错误翻成中文', () => {
    expect(
      humanizeNotifyMessage('retry attempt 5: notify: channel "Ongrid通知" not configured', zh),
    ).toBe('第 5 次重试仍失败：通知渠道「Ongrid通知」尚未配置');
  });
  it('未知错误原样保留，不吞真实信息', () => {
    const raw = 'dial tcp 10.0.0.1:25: connection refused';
    expect(humanizeNotifyMessage(raw, zh)).toBe(raw);
  });
});
