import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, Maximize2, RotateCw } from 'lucide-react';
import type { Device } from '@/api/devices';
import { listDevices } from '@/api/devices';
import { promQueryRange, type PromRangeResp } from '@/api/edges';
import { listIncidents, listRules, localizedRuleName, type Incident } from '@/api/alerts';
import { getReport, listReports, type Advice } from '@/api/reports';
import { usePoll } from '@/lib/usePoll';
import { relativeTime } from '@/lib/format';
import './BigScreen.css';

const WINDOW_MS = 24 * 60 * 60 * 1000;
const REFRESH_MS = 60_000;

// 与系统 :root.light 语义 token 对齐；顶部数字条按监测分类着色。
const C = {
  cyan: '#0e7490',
  blue: '#1d4ed8',
  green: '#047857',
  amber: '#b45309',
  red: '#b91c1c',
  purple: '#6d57d9',
  teal: '#0f766e',
};

// squashfs(snap 只读挂载)/tmpfs/vfat 的 avail 恒为 0，直接 max 会把每台装了
// snap 的主机都算成 100%。只统计真实可写数据盘。
const REAL_FS = '{fstype=~"ext[234]|xfs|btrfs|zfs",mountpoint!~"^/(snap|proc|sys|dev|run|boot/efi)(/|$)"}';
const EXPR = {
  cpu: '100 * (1 - avg by (device_id) (rate(node_cpu_seconds_total{mode="idle"}[5m])))',
  mem: '100 * (1 - avg by (device_id) (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))',
  disk: `max by (device_id) (100 * (1 - node_filesystem_avail_bytes${REAL_FS} / node_filesystem_size_bytes${REAL_FS}))`,
  online: 'count(count by (device_id) (rate(node_cpu_seconds_total{mode="idle"}[5m])))',
};

type Metrics = Map<string, number[]>;

type Snapshot = {
  devices: Device[];
  incidents: Incident[];
  ruleCount: number;
  cpu: Metrics;
  mem: Metrics;
  disk: Metrics;
  onlineTrend: number[];
  reportsTotal: number;
  reportHeadline: string;
  reportAdvice: string[];
  reportDate: string;
  resolvedTotal: number;
  incidentTotal: number;
};

type DeviceRow = {
  device: Device;
  cpu: number | null;
  mem: number | null;
  disk: number | null;
};

export type DigitGroup = { label: string; value: number; color: string; cls: string };
export type TrendDay = { label: string; fired: number; closed: number; date: string };
export type AdviceRow = { rank: number; pri: 'p0' | 'p1' | 'p2'; label: string; text: string };

// lastFinite returns the newest non-NaN sample; Prom emits empty gaps as NaN strings.
export function lastFinite(values: number[] | undefined): number | null {
  if (!values) return null;
  for (let i = values.length - 1; i >= 0; i--) {
    const v = values[i];
    if (Number.isFinite(v)) return v;
  }
  return null;
}

// 告警事件只带 target_id / labels.device_id，没有可读名称；靠设备清单做映射。
export function incidentDeviceId(i: Incident): string | null {
  const id = i.labels?.device_id ?? i.target_id;
  return id ? String(id) : null;
}

export function toMetrics(resp: PromRangeResp | null): Metrics {
  const out: Metrics = new Map();
  if (!resp) return out;
  for (const s of resp.matrix) {
    const id = s.metric.device_id ?? s.metric.instance;
    if (!id) continue;
    out.set(id, s.values.map(([, v]) => {
      const n = Number(v);
      return Number.isFinite(n) ? n : Number.NaN;
    }));
  }
  return out;
}

export function buildDeviceRows(devices: Device[], cpu: Metrics, mem: Metrics, disk: Metrics): DeviceRow[] {
  return devices
    .map((device) => {
      const id = String(device.id);
      return {
        device,
        cpu: lastFinite(cpu.get(id)),
        mem: lastFinite(mem.get(id)),
        disk: lastFinite(disk.get(id)),
      };
    })
    .filter((r) => r.cpu !== null || r.mem !== null || r.disk !== null);
}

export function onlineCount(devices: Device[]): number {
  return devices.filter((d) =>
    d.os?.toLowerCase() === 'network'
      ? ['reachable', 'online', 'up'].includes((d.reachability_status ?? '').toLowerCase())
      : d.online === true,
  ).length;
}

/** 顶部 5 组监测资源：设备 / 在线 / 告警 / 离线 / 规则，分类着色。 */
export function digitGroups(s: Pick<Snapshot, 'devices' | 'incidents' | 'ruleCount'>): DigitGroup[] {
  const online = onlineCount(s.devices);
  const active = s.incidents.filter((i) => i.status !== 'resolved').length;
  return [
    { label: '设备', value: s.devices.length, color: C.purple, cls: 'c-dev' },
    { label: '在线', value: online, color: C.green, cls: 'c-online' },
    { label: '告警', value: active, color: C.amber, cls: 'c-alarm' },
    { label: '离线', value: Math.max(s.devices.length - online, 0), color: C.red, cls: 'c-offline' },
    { label: '规则', value: s.ruleCount, color: C.blue, cls: 'c-rule' },
  ];
}

/** conic-gradient 环形：按数据占比生成色段，无数据时给中性灰。 */
export function donutGradient(data: Array<{ value: number; color: string }>): string {
  const total = data.reduce((n, d) => n + d.value, 0);
  if (!total) return 'conic-gradient(#e2e8f0 0 100%)';
  let acc = 0;
  const stops = data.map((d) => {
    const from = (acc / total) * 100;
    acc += d.value;
    return `${d.color} ${from.toFixed(2)}% ${((acc / total) * 100).toFixed(2)}%`;
  });
  return `conic-gradient(${stops.join(',')})`;
}

const utcDay = (iso: string) => (iso.length >= 10 ? iso.slice(0, 10) : '');

/** 7 日新增 / 闭环趋势：按 fired_at 与 resolved_at 的 UTC 日期分组。 */
export function buildTrend7(incidents: Incident[], now: Date = new Date(), days = 7): TrendDay[] {
  const out: TrendDay[] = [];
  for (let k = days - 1; k >= 0; k--) {
    const d = new Date(now);
    d.setUTCHours(0, 0, 0, 0);
    d.setUTCDate(d.getUTCDate() - k);
    const key = d.toISOString().slice(0, 10);
    out.push({
      date: key,
      label: `${Number(key.slice(5, 7))}-${key.slice(8, 10)}`,
      fired: incidents.filter((i) => utcDay(i.fired_at) === key).length,
      closed: incidents.filter((i) => i.resolved_at && utcDay(i.resolved_at) === key).length,
    });
  }
  return out;
}

/** 报告 advice 本身按重要性排序，映射为排名徽章 + P0/P1/P2；清理实体占位标记。 */
export function buildAdvice(advice: Advice[] | string[]): AdviceRow[] {
  const labels = { p0: 'P0 紧急', p1: 'P1 较高', p2: 'P2 关注' } as const;
  return advice.slice(0, 3).map((a, i) => {
    const pri: AdviceRow['pri'] = i === 0 ? 'p0' : i === 1 ? 'p1' : 'p2';
    const text = typeof a === 'string' ? a : a.text;
    return {
      rank: i + 1,
      pri,
      label: labels[pri],
      text: text.replace(/\{\{entity:[^|]+\|([^}]+)\}\}/g, '$1'),
    };
  });
}

function firstOnlineSeries(resp: PromRangeResp | null): number[] {
  const s = resp?.matrix[0];
  if (!s) return [];
  return s.values.map(([, v]) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  });
}

function clampPct(v: number | null): number {
  if (v === null || !Number.isFinite(v)) return 0;
  return Math.max(0, Math.min(100, v));
}

function fmtBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '—';
  const tb = bytes / 1e12;
  if (tb >= 1) return `${tb.toFixed(1)}T`;
  return `${(bytes / 1e9).toFixed(0)}G`;
}

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

function clockText(d: Date): string {
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const SEV_COLOR: Record<string, string> = { critical: C.red, warning: C.amber, info: C.blue };
const SEV_LABEL: Record<string, string> = { critical: '严重', warning: '警告', info: '提示' };

function countRole(devices: Device[], role: string): number {
  return devices.filter((d) => (d.roles ?? []).includes(role)).length;
}

export default function BigScreenPage() {
  const navigate = useNavigate();
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [now, setNow] = useState(() => new Date());
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null);
  const [fullscreen, setFullscreen] = useState(false);

  const loadAll = useCallback(async () => {
    const to = new Date();
    const from = new Date(to.getTime() - WINDOW_MS);
    const fromIso = from.toISOString();
    const toIso = to.toISOString();
    const query = (expr: string) =>
      promQueryRange({ expr, from: fromIso, to: toIso, step: '1h' }).catch(() => null);

    const [devicesR, incidentsR, rulesR, reportsR, resolvedR, cpuR, memR, diskR, onlineR] = await Promise.all([
      listDevices().catch(() => null),
      listIncidents({ pageSize: 200 }).catch(() => null),
      listRules().catch(() => null),
      listReports({ limit: 1 }).catch(() => null),
      listIncidents({ status: 'resolved', pageSize: 1 }).catch(() => null),
      query(EXPR.cpu),
      query(EXPR.mem),
      query(EXPR.disk),
      query(EXPR.online),
    ]);

    const incidents = incidentsR?.items ?? [];
    const reportId = reportsR?.reports?.[0]?.id;
    let reportHeadline = '';
    let reportAdvice: string[] = [];
    let reportDate = '';
    if (reportId) {
      const detail = await getReport(reportId).catch(() => null);
      reportHeadline = detail?.content?.narrative?.headline ?? '';
      reportAdvice = (detail?.content?.advice ?? []).map((a) => a.text);
      reportDate = detail?.period_end?.slice(0, 10) ?? detail?.generated_at?.slice(0, 10) ?? '';
    }

    setSnapshot({
      devices: devicesR?.items ?? [],
      incidents,
      ruleCount: rulesR?.items?.length ?? 0,
      cpu: toMetrics(cpuR),
      mem: toMetrics(memR),
      disk: toMetrics(diskR),
      onlineTrend: firstOnlineSeries(onlineR),
      reportsTotal: reportsR?.total ?? reportsR?.reports?.length ?? 0,
      reportHeadline,
      reportAdvice,
      reportDate,
      resolvedTotal: resolvedR?.total ?? 0,
      incidentTotal: incidentsR?.total ?? incidents.length,
    });
    setRefreshedAt(new Date());
    setNow(new Date());
  }, []);

  useEffect(() => { void loadAll(); }, [loadAll]);
  usePoll(loadAll, REFRESH_MS);

  // 分钟级刷新：显示真实时间但不做跳秒动画。
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    const onChange = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void document.documentElement.requestFullscreen?.();
  }, []);

  const deviceMap = useMemo(() => {
    const m = new Map<string, Device>();
    for (const d of snapshot?.devices ?? []) m.set(String(d.id), d);
    return m;
  }, [snapshot]);

  const goDevice = useCallback((id: string | number) => navigate(`/devices/${encodeURIComponent(String(id))}`), [navigate]);
  const goIncident = useCallback((id: number) => navigate(`/alerts/incidents/${id}`), [navigate]);

  const rows = useMemo(
    () => (snapshot ? buildDeviceRows(snapshot.devices, snapshot.cpu, snapshot.mem, snapshot.disk) : []),
    [snapshot],
  );

  const health = useMemo(() => {
    let healthy = 0, warning = 0, critical = 0;
    for (const r of rows) {
      const peak = Math.max(clampPct(r.cpu), clampPct(r.mem), clampPct(r.disk));
      if (peak >= 90) critical++;
      else if (peak >= 80) warning++;
      else healthy++;
    }
    const offline = snapshot ? snapshot.devices.length - rows.length : 0;
    return [
      { name: '健康', value: healthy, color: C.green },
      { name: '预警', value: warning, color: C.amber },
      { name: '严重', value: critical, color: C.red },
      { name: '无数据', value: Math.max(offline, 0), color: '#94a3b8' },
    ];
  }, [rows, snapshot]);

  const severity = useMemo(() => {
    const counts = { critical: 0, warning: 0, info: 0 };
    for (const i of snapshot?.incidents ?? []) {
      if (i.status === 'resolved') continue;
      const key = i.severity === 'critical' || i.severity === 'warning' ? i.severity : 'info';
      counts[key]++;
    }
    return [
      { name: '严重', value: counts.critical, color: C.red },
      { name: '警告', value: counts.warning, color: C.amber },
      { name: '提示', value: counts.info, color: C.blue },
    ];
  }, [snapshot]);

  const riskDevices = useMemo(
    () => [...rows]
      .map((r) => ({
        device: r.device,
        cpu: clampPct(r.cpu),
        mem: clampPct(r.mem),
        disk: clampPct(r.disk),
        peak: Math.max(clampPct(r.cpu), clampPct(r.mem), clampPct(r.disk)),
      }))
      .sort((a, b) => b.peak - a.peak)
      .slice(0, 6),
    [rows],
  );

  const trend7 = useMemo(() => buildTrend7(snapshot?.incidents ?? [], now), [snapshot, now]);

  const activeIncidents = useMemo(
    () => (snapshot?.incidents ?? []).filter((i) => i.status !== 'resolved'),
    [snapshot],
  );

  const events = useMemo(() => {
    const closed = (snapshot?.incidents ?? [])
      .filter((i) => i.status === 'resolved' && i.resolved_at)
      .sort((a, b) => +new Date(b.resolved_at!).getTime() - +new Date(a.resolved_at!).getTime())
      .slice(0, 6);
    return closed.map((i) => {
      const name = localizedRuleName(i.rule_key, i.rule_name);
      const devId = incidentDeviceId(i);
      const dev = devId ? deviceMap.get(devId) : undefined;
      const target = dev?.ip_address ?? dev?.name ?? devId ?? '—';
      const step = i.acknowledged_at ? '已确认并处置' : '系统自动闭环';
      const time = new Date(i.resolved_at!).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
      return { name, target, step, time, fired: relativeTime(i.fired_at) };
    });
  }, [snapshot, deviceMap]);

  const adviceRows = useMemo(() => buildAdvice(snapshot?.reportAdvice ?? []), [snapshot]);
  const groups = snapshot ? digitGroups(snapshot) : [];

  const values = useMemo(() => {
    const s = snapshot;
    if (!s) return [];
    const online = groups[1]?.value ?? 0;
    const rate = s.incidentTotal > 0 ? Math.round((s.resolvedTotal / s.incidentTotal) * 100) : 0;
    const created = s.devices.map((d) => new Date(d.created_at ?? '').getTime()).filter((t) => Number.isFinite(t) && t > 0);
    const stableDays = created.length ? Math.max(0, Math.floor((Date.now() - Math.min(...created)) / 86400000)) : 0;
    const reportTime = s.reportDate || '—';
    const closedToday = trend7[trend7.length - 1]?.closed ?? 0;
    return [
      { icon: '巡', color: '#0e7490', num: String(s.reportsTotal), act: '份巡检报告', lab: `${reportTime} 自动生成 · 无需人工值守`, to: '/reports' },
      { icon: '闭', color: C.purple, num: `${rate}%`, act: '告警闭环率', lab: `今日闭环 ${closedToday} 条 · ${s.resolvedTotal}/${s.incidentTotal} 累计处置`, to: '/alerts/incidents' },
      { icon: '守', color: C.green, num: `${s.devices.length ? Math.round((online / s.devices.length) * 100) : 0}%`, act: '设备在线率', lab: `${s.devices.length} 台全时纳管 · 异常即时发现`, to: '/devices' },
      { icon: '警', color: C.amber, num: String(s.incidentTotal), act: '次风险预警', lab: '提前发现隐患 · 防患于未然', to: '/alerts/incidents' },
      { icon: '稳', color: C.blue, num: String(stableDays), act: '天稳定运行', lab: '平台持续在线 · 业务无中断', to: '/dashboard' },
    ];
  }, [snapshot, groups, trend7]);

  return (
    <main className="bs-root" aria-label="OnGrid 监控大屏">
      <header className="bs-header">
        <div className="bs-header-side">
          <span className="bs-live-dot" />
          <span>系统运行中</span>
          <span className="bs-clock">{clockText(now)}</span>
        </div>
        <div className="bs-title">
          <span className="bs-title-wing" />
          OnGrid 智能运维监控大屏
          <span className="bs-title-wing right" />
        </div>
        <div className="bs-header-side right">
          <span>{refreshedAt ? `更新于 ${refreshedAt.toLocaleTimeString('zh-CN', { hour12: false })}` : '加载中'}</span>
          <span className="bs-ai-launcher">
            <button
              type="button"
              className="bs-ai-btn"
              onClick={() => navigate('/')}
              title="打开平台 AI 助理"
            >
              <svg viewBox="40 50 432 412" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                <defs>
                  <linearGradient id="bsFavLeft" x1="154" y1="88" x2="225" y2="432" gradientUnits="userSpaceOnUse">
                    <stop offset="0" stopColor="#F15BC7" /><stop offset="0.52" stopColor="#8C6DF0" /><stop offset="1" stopColor="#5269F4" />
                  </linearGradient>
                  <linearGradient id="bsFavRight" x1="291" y1="69" x2="362" y2="413" gradientUnits="userSpaceOnUse">
                    <stop offset="0" stopColor="#1456B5" /><stop offset="0.56" stopColor="#30A6D0" /><stop offset="1" stopColor="#57D6D8" />
                  </linearGradient>
                </defs>
                <rect x="54" y="178" width="354" height="74" rx="37" transform="rotate(-14 54 178)" fill="#7182AD" />
                <rect x="102" y="307" width="354" height="74" rx="37" transform="rotate(-14 102 307)" fill="#7182AD" />
                <rect x="115" y="93" width="76" height="346" rx="38" transform="rotate(-14 115 93)" fill="url(#bsFavLeft)" />
                <rect x="252" y="74" width="76" height="346" rx="38" transform="rotate(-14 252 74)" fill="url(#bsFavRight)" />
              </svg>
              AI 助理
            </button>
          </span>
          <button type="button" className="bs-icon-btn" onClick={() => void loadAll()} title="刷新数据">
            <RotateCw size={12} /> 刷新
          </button>
          <button type="button" className="bs-icon-btn" onClick={toggleFullscreen} title="全屏显示">
            <Maximize2 size={12} /> {fullscreen ? '退出全屏' : '全屏'}
          </button>
          <button type="button" className="bs-icon-btn" onClick={() => navigate('/dashboard')} title="返回普通视图">
            <ArrowLeft size={12} /> 返回
          </button>
        </div>
      </header>

      <div className="bs-counter-strip">
        <span className="bs-counter-label">平台监测资源</span>
        <div className="bs-digits">
          {groups.map((g) => (
            <div className={`bs-digit-group ${g.cls}`} key={g.label}>
              <div className="bs-digit-pair">
                {String(g.value).padStart(2, '0').split('').map((ch, idx) => (
                  <span className="bs-digit" key={idx}>{ch}</span>
                ))}
              </div>
              <span className="bs-digit-group-label">{g.label}</span>
            </div>
          ))}
        </div>
      </div>

      {!snapshot ? (
        <div className="bs-loading" style={{ flex: 1 }}>正在加载平台资源与监控数据…</div>
      ) : (
        <div className="bs-main">
          {/* 第一行：资源概况 / 资源健康度 / 告警分级 */}
          <Panel title="基础资源展示" extra={`共 ${snapshot.devices.length} 台设备`} className="col-span-4 bs-panel-res">
            <div className="bs-res-grid">
              {[
                { label: '在线设备', value: groups[1]?.value ?? 0, color: C.green, to: '/devices' },
                { label: '离线设备', value: groups[3]?.value ?? 0, color: C.red, to: '/devices' },
                { label: '服务器', value: countRole(snapshot.devices, 'server'), color: C.cyan, to: '/devices?roles=server' },
                { label: '存储', value: countRole(snapshot.devices, 'storage'), color: C.purple, to: '/devices?roles=storage' },
                { label: '数据库', value: countRole(snapshot.devices, 'database'), color: C.amber, to: '/devices?roles=database' },
                { label: '网络设备', value: countRole(snapshot.devices, 'network'), color: C.teal, to: '/devices?roles=network' },
                { label: 'GPU 设备', value: countRole(snapshot.devices, 'gpu'), color: C.blue, to: '/devices?roles=gpu' },
                { label: '监控服务', value: rows.length, color: C.cyan, to: '/monitor' },
              ].map((cell) => (
                <button type="button" className="bs-res-cell" key={cell.label} onClick={() => navigate(cell.to)} title={`查看${cell.label}明细`}>
                  <span className="bs-res-num" style={{ color: cell.color }}>{cell.value}</span>
                  <span className="bs-res-label">{cell.label}</span>
                </button>
              ))}
            </div>
          </Panel>

          <Panel title="资源健康度" extra={`采集 ${rows.length} 台`} className="col-span-5 bs-panel-health">
            <Donut data={health} centerValue={String(rows.length)} />
          </Panel>

          <Panel title="告警分级统计" extra={`活跃 ${severity.reduce((n, s) => n + s.value, 0)} 条`} className="col-span-3 bs-panel-sev">
            <Donut data={severity} centerValue={String(severity.reduce((n, s) => n + s.value, 0))} />
          </Panel>

          {/* 第二行：运维价值场景卡 —— 平台做了什么 → 什么价值 */}
          <div className="bs-value-strip">
            {values.map((v) => (
              <div className="bs-value-cell" key={v.act} role="button" tabIndex={0} title={v.lab} onClick={() => navigate(v.to)} onKeyDown={(e) => { if (e.key === 'Enter') navigate(v.to); }}>
                <span className="bs-value-icon" style={{ background: `linear-gradient(135deg,${v.color},${v.color}cc)` }}>{v.icon}</span>
                <div className="bs-value-meta">
                  <div className="bs-value-top"><span className="bs-value-num" style={{ color: v.color }}>{v.num}</span><span className="bs-value-act">{v.act}</span></div>
                  <div className="bs-value-lab">{v.lab}</div>
                </div>
              </div>
            ))}
          </div>

          {/* 第三行：最新告警 / 资源风险设备 / 主动运维战报 */}
          <Panel title="最新告警信息" extra={`活跃 ${activeIncidents.length} 条 · 自动滚屏`} className="col-span-3 bs-panel-alert">
            <div className="bs-alert-wrap">
              <div className="bs-alert-track">
                {[0, 1].map((copy) => (
                  <div className="bs-alert-list" key={copy}>
                    {activeIncidents.length === 0 && copy === 0
                      ? <div className="bs-list-empty">当前无活跃告警</div>
                      : activeIncidents.map((i) => {
                        const devId = incidentDeviceId(i);
                        const dev = devId ? deviceMap.get(devId) : undefined;
                        const target = dev?.ip_address ?? dev?.name ?? devId ?? '';
                        const name = localizedRuleName(i.rule_key, i.rule_name);
                        const sev = i.severity === 'critical' || i.severity === 'warning' ? i.severity : 'info';
                        return (
                          <button
                            type="button"
                            className={`bs-alert-row ${sev}`}
                            key={`${copy}-${i.id}`}
                            onClick={() => goIncident(i.id)}
                            title={`${name}${target ? ` · ${target}` : ''} — 点击查看告警详情`}
                          >
                            <span className="bs-alert-sev" style={{ color: SEV_COLOR[sev] }}>{SEV_LABEL[sev]}</span>
                            <span className="bs-alert-title">{name}</span>
                            <span className="bs-alert-target">{target}</span>
                            <span className="bs-alert-time">{relativeTime(i.last_fired_at || i.fired_at)}</span>
                          </button>
                        );
                      })}
                  </div>
                ))}
              </div>
            </div>
          </Panel>

          <Panel title="资源风险设备 TOP 6" extra={`一机一行 · CPU/内存/磁盘 · ${riskDevices.filter((r) => r.peak >= 90).length} 台 >90%`} className="col-span-5 bs-panel-risk">
            <div className="bs-risk-table">
              <div className="bs-risk-line bs-risk-head">
                <span>#</span><span>设备 IP</span><span>CPU</span><span>内存</span><span>磁盘</span><span>峰值水位</span><span>峰值</span>
              </div>
              {riskDevices.length === 0 && <div className="bs-list-empty">暂无指标数据</div>}
              {riskDevices.map((r, idx) => (
                <button type="button" className="bs-risk-line bs-risk-row" key={r.device.id} onClick={() => goDevice(r.device.id)} title={`${r.device.name} · 查看设备详情`}>
                  <span className={`bs-risk-rank ${idx < 3 ? `r${idx + 1}` : ''}`}>{idx + 1}</span>
                  <span className="bs-risk-ip">{r.device.ip_address ?? r.device.name}</span>
                  <span className={`bs-risk-val ${r.cpu >= 90 ? 'hot' : r.cpu >= 80 ? 'warm' : ''}`}>{r.cpu.toFixed(0)}%</span>
                  <span className={`bs-risk-val ${r.mem >= 90 ? 'hot' : r.mem >= 80 ? 'warm' : ''}`}>{r.mem.toFixed(0)}%</span>
                  <span className={`bs-risk-val ${r.disk >= 90 ? 'hot' : r.disk >= 80 ? 'warm' : ''}`}>{r.disk.toFixed(0)}%</span>
                  <span className="bs-risk-track"><span className={`bs-risk-fill ${r.peak < 90 ? 'warn' : ''}`} style={{ width: `${r.peak}%` }} /></span>
                  <span className="bs-risk-peak">{r.peak.toFixed(0)}%</span>
                </button>
              ))}
            </div>
          </Panel>

          <Panel title="主动运维战报" extra="发现 → 处置 → 价值 · 自动滚屏" className="col-span-4 bs-panel-event">
            <div className="bs-event-wrap">
              <div className="bs-event-track">
                {[0, 1].map((copy) => (
                  <div key={copy}>
                    <div className="bs-event-row ok">
                      <div className="bs-event-head"><span className="bs-event-icon">📋</span><span className="bs-event-type">巡检产出</span><span className="bs-event-find">每日自动巡检报告 · {snapshot.reportDate || '—'}</span><span className="bs-event-time">01:00</span></div>
                      <div className="bs-event-flow"><span>定时触发</span><span className="bs-event-arrow">→</span><span>AI 分析 {snapshot.devices.length} 台</span><span className="bs-event-arrow">→</span><span className="bs-event-value">报告已自动生成</span></div>
                    </div>
                    {events.map((e, idx) => (
                      <div className="bs-event-row ok" key={`${copy}-${idx}`}>
                        <div className="bs-event-head"><span className="bs-event-icon">✅</span><span className="bs-event-type">告警闭环</span><span className="bs-event-find">{e.name} · {e.target}</span><span className="bs-event-time">{e.time}</span></div>
                        <div className="bs-event-flow"><span>{e.fired}触发</span><span className="bs-event-arrow">→</span><span>{e.step}</span><span className="bs-event-arrow">→</span><span className="bs-event-value">已闭环归档</span></div>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          </Panel>

          {/* 第四行：7 日治理趋势 / AI 巡检结论与建议 */}
          <Panel title="7 日告警治理趋势" extra="新增 vs 闭环 · 逐柱查看条数" className="col-span-5 bs-panel-trend">
            <TrendBars data={trend7} />
          </Panel>

          <Panel title="AI 巡检结论与建议" extra={`日报 ${snapshot.reportDate || '—'} · 按优先级排序 · 待处理`} className="col-span-7 bs-panel-ai">
            <div className="bs-ai-wrap">
              <div className="bs-ai-head">{snapshot.reportHeadline || '本周期暂无巡检结论'}</div>
              {adviceRows.length === 0 && <div className="bs-list-empty">暂无处理建议</div>}
              {adviceRows.map((a) => (
                <div className="bs-ai-row" key={a.rank}>
                  <span className={`bs-ai-rank ${a.pri}`}>{a.rank}</span>
                  <span className={`bs-ai-pri ${a.pri}`}>{a.label}</span>
                  <span className="bs-ai-text">{a.text}</span>
                </div>
              ))}
            </div>
          </Panel>
        </div>
      )}
    </main>
  );
}

function Panel({ title, extra, className = '', children }: {
  title: string; extra?: string; className?: string; children: React.ReactNode;
}) {
  return (
    <section className={`bs-panel ${className}`}>
      <span className="bs-corner tl" /><span className="bs-corner tr" />
      <span className="bs-corner bl" /><span className="bs-corner br" />
      <header className="bs-panel-head">
        {title}
        {extra && <span className="bs-panel-extra">{extra}</span>}
      </header>
      <div className="bs-panel-body">{children}</div>
    </section>
  );
}

function Donut({ data, centerValue }: {
  data: Array<{ name: string; value: number; color: string }>; centerValue: string;
}) {
  const sum = data.reduce((n, d) => n + d.value, 0);
  return (
    <div className="bs-donut-wrap">
      <div className="bs-donut-chart">
        {sum === 0 ? <div className="bs-list-empty">暂无数据</div> : (
          <div className="bs-donut-box">
            <div className="bs-donut-ring" style={{ background: donutGradient(data) }} />
            <div className="bs-donut-center"><b>{centerValue}</b></div>
          </div>
        )}
      </div>
      <div className="bs-legend">
        {data.map((d) => (
          <div className="bs-legend-row" key={d.name}>
            <span className="bs-legend-dot" style={{ background: d.color, color: d.color }} />
            <span>{d.name}</span>
            <span className="bs-legend-num">{d.value}{sum > 0 ? ` · ${Math.round((d.value / sum) * 100)}%` : ''}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function TrendBars({ data }: { data: TrendDay[] }) {
  const [tip, setTip] = useState<{ text: string; x: number; y: number } | null>(null);
  const W = 700, H = 200, left = 45, right = 10, top = 20, bottom = 30;
  const plotH = H - top - bottom;
  const max = Math.max(1, ...data.flatMap((d) => [d.fired, d.closed]));
  const groupW = (W - left - right) / Math.max(data.length, 1);
  const barW = Math.min(22, groupW * 0.3);
  const yFor = (v: number) => top + plotH - (v / max) * plotH;

  return (
    <div className="bs-trend-host">
      <div className="bs-trend-legend">
        <span><i style={{ background: C.amber }} />新增</span>
        <span><i style={{ background: C.green }} />闭环</span>
      </div>
      <svg className="bs-trend-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        {[0, 1, 2, 3].map((k) => (
          <line key={k} x1={left} x2={W - right} y1={top + (plotH / 3) * k} y2={top + (plotH / 3) * k} stroke="#e2e8f0" />
        ))}
        <g className="bs-trend-axis" fontSize="12">
          {[3, 2, 1, 0].map((k) => (
            <text key={k} x="12" y={top + (plotH / 3) * (3 - k) + 4}>{Math.round((max / 3) * k)}</text>
          ))}
        </g>
        {data.map((d, i) => {
          const base = left + groupW * i + groupW / 2;
          const bars = [
            { v: d.fired, x: base - barW - 2, color: C.amber, label: '新增' },
            { v: d.closed, x: base + 2, color: C.green, label: '闭环' },
          ];
          return (
            <g key={d.date}>
              {bars.map((b) => (
                <rect
                  key={b.label}
                  className="bs-trend-bar"
                  x={b.x}
                  y={yFor(b.v)}
                  width={barW}
                  height={Math.max(b.v > 0 ? 2 : 1, top + plotH - yFor(b.v))}
                  fill={b.color}
                  onMouseEnter={() => setTip({ text: `${d.label} · ${b.label} ${b.v} 条`, x: 0, y: 0 })}
                  onMouseMove={(e) => {
                    const r = e.currentTarget.ownerSVGElement?.getBoundingClientRect();
                    if (!r) return;
                    const scaleX = r.width / W;
                    setTip({
                      text: `${d.label} · ${b.label} ${b.v} 条`,
                      x: ((e.clientX - r.left) / scaleX / W) * 100,
                      y: (yFor(b.v) / H) * 100,
                    });
                  }}
                  onMouseLeave={() => setTip(null)}
                />
              ))}
              <text className="bs-trend-axis" x={base} y={H - 8} textAnchor="middle" fontSize="12">{d.label}</text>
            </g>
          );
        })}
      </svg>
      {tip && <div className="bs-trend-tip show" style={{ left: `${tip.x}%`, top: `${tip.y}%` }}>{tip.text}</div>}
    </div>
  );
}
