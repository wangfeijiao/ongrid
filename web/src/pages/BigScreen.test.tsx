import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import BigScreenPage, {
  buildAdvice,
  buildDeviceRows,
  buildTrend7,
  digitGroups,
  donutGradient,
  lastFinite,
  toMetrics,
} from './BigScreen';
import { server } from '@/test/msw-server';
import type { Incident } from '@/api/alerts';

// recharts ResponsiveContainer needs ResizeObserver, absent in jsdom.
vi.stubGlobal('ResizeObserver', class {
  observe() {}
  unobserve() {}
  disconnect() {}
});

const matrix = (deviceId: string, value: string) => ({
  metric: { device_id: deviceId },
  values: [[0, value], [1, value]],
});

const promExprs: string[] = [];

function LocationProbe() {
  const loc = useLocation();
  return <span data-testid="route">{`${loc.pathname}${loc.search}`}</span>;
}

const incident = (over: Partial<Incident>): Incident => ({
  id: 1, rule_key: 'swap_high', rule_name: 'Swap 使用率 > 90%',
  severity: 'warning', status: 'open', summary: 'swap',
  target_type: 'edge', target_id: '7', labels: { device_id: '7' },
  fired_at: new Date().toISOString(), last_fired_at: new Date().toISOString(),
  updated_at: new Date().toISOString(), event_count: 1, ...over,
});

describe('BigScreenPage', () => {
  beforeEach(() => {
    promExprs.length = 0;
    localStorage.setItem('ongrid-locale', 'zh-CN');
    server.use(
      http.get('/api/v1/devices', () =>
        HttpResponse.json({
          items: [
            {
              id: 7, name: 'web-01', ip_address: '10.7.30.7', online: true,
              roles: ['server', 'database'], cpu_count: 16,
              mem_total_bytes: 64_000_000_000, created_at: '2026-01-01T00:00:00Z',
            },
            {
              id: 8, name: 'offline-01', online: false,
              roles: ['network'], created_at: '2026-01-01T00:00:00Z',
            },
          ],
          total: 2,
        }),
      ),
      http.get('/api/v1/alerts/incidents', ({ request }) => {
        const status = new URL(request.url).searchParams.get('status');
        if (status === 'resolved') return HttpResponse.json({ items: [], total: 1 });
        return HttpResponse.json({
          items: [incident({})],
          total: 1,
        });
      }),
      http.get('/api/v1/alert-rules', () =>
        HttpResponse.json({ items: [{ id: 1, rule_key: 'swap_high', enabled: true }], total: 1 }),
      ),
      http.get('/api/v1/reports', () =>
        HttpResponse.json({
          reports: [{ id: 'r1', title: '日报', kind: 'daily', status: 'ready', created_at: new Date().toISOString(), period_end: '2026-11-22T00:00:00Z' }],
          total: 21,
        }),
      ),
      http.get('/api/v1/reports/:id', () =>
        HttpResponse.json({
          id: 'r1', title: '日报', kind: 'daily', status: 'ready', created_at: new Date().toISOString(), content_md: '', timezone: 'Asia/Shanghai',
          narrative: { headline: '内存峰值贴近红线' },
          advice: [{ text: '{{entity:edge:39|设备 39}} 内存超限需扩容' }, { text: '磁盘写满需清理' }, { text: '核查流量归零' }],
        }),
      ),
      http.get('/api/v1/metrics/query_range', ({ request }) => {
        const expr = new URL(request.url).searchParams.get('expr') ?? '';
        promExprs.push(expr);
        if (expr.includes('node_cpu_seconds_total') && expr.includes('count(')) {
          return HttpResponse.json({ matrix: [{ metric: {}, values: [[0, '1'], [1, '1']] }] });
        }
        const v = expr.includes('node_cpu') ? '42.5' : expr.includes('node_filesystem') ? '91' : '63.2';
        return HttpResponse.json({ matrix: [matrix('7', v)] });
      }),
    );
  });

  it('渲染大屏各区块并展示中文标题', async () => {
    render(
      <MemoryRouter>
        <BigScreenPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole('main', { name: 'OnGrid 监控大屏' })).toBeInTheDocument();
    expect(screen.getByText('OnGrid 智能运维监控大屏')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText('资源健康度')).toBeInTheDocument();
      expect(screen.getByText('告警分级统计')).toBeInTheDocument();
      expect(screen.getByText('最新告警信息')).toBeInTheDocument();
      expect(screen.getByText('资源风险设备 TOP 6')).toBeInTheDocument();
      expect(screen.getByText('主动运维战报')).toBeInTheDocument();
      expect(screen.getByText('7 日告警治理趋势')).toBeInTheDocument();
      expect(screen.getByText('AI 巡检结论与建议')).toBeInTheDocument();
      expect(screen.getByTitle('打开平台 AI 助理')).toBeInTheDocument();
    });
    // 设备 2 / 在线 1 / 告警 1 / 离线 1 / 规则 1 —— 5 组分类数字。
    await waitFor(() => {
      expect(document.querySelectorAll('.bs-digit-group')).toHaveLength(5);
      const chars = Array.from(document.querySelectorAll('.bs-digit')).map((el) => el.textContent);
      expect(chars).toContain('2');
      expect(chars).toContain('1');
    });
  });

  it('价值场景卡使用报告/闭环/在线真实数据', async () => {
    render(<MemoryRouter><BigScreenPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('份巡检报告')).toBeInTheDocument());
    expect(screen.getByText('21')).toBeInTheDocument(); // reports total
    expect(screen.getByText('告警闭环率')).toBeInTheDocument();
    expect(screen.getByText('设备在线率')).toBeInTheDocument();
    expect(screen.getByText('次风险预警')).toBeInTheDocument();
    expect(screen.getByText('天稳定运行')).toBeInTheDocument();
  });

  it('指标解析忽略 NaN 空档，设备行保留最近有效值', () => {
    const m = toMetrics({
      matrix: [{ metric: { device_id: '7' }, values: [[0, '12'], [1, 'NaN'], [2, '18']] }],
      from: '', to: '', resolution: '1h',
    });
    expect(lastFinite(m.get('7'))).toBe(18);
    const rows = buildDeviceRows(
      [{ id: 7, name: 'web-01' } as never],
      m,
      new Map(),
      new Map(),
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].cpu).toBe(18);
    expect(rows[0].mem).toBeNull();
  });

  it('磁盘查询排除 snap 只读挂载，避免假 100%', async () => {
    render(<MemoryRouter><BigScreenPage /></MemoryRouter>);
    await waitFor(() => expect(promExprs.some((e) => e.includes('node_filesystem'))).toBe(true));
    const diskExpr = promExprs.find((e) => e.includes('node_filesystem')) ?? '';
    expect(diskExpr).toContain('fstype=~"ext[234]|xfs|btrfs|zfs"');
    expect(diskExpr).toContain('mountpoint!~"^/(snap|proc|sys|dev|run|boot/efi)(/|$)"');
  });

  it('告警显示设备 IP，并可下钻到告警与设备明细', async () => {
    render(
      <MemoryRouter initialEntries={['/bigscreen']}>
        <LocationProbe />
        <BigScreenPage />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getAllByText('10.7.30.7').length).toBeGreaterThan(0));

    fireEvent.click(screen.getAllByTitle(/点击查看告警详情/)[0]);
    expect(screen.getByTestId('route').textContent).toBe('/alerts/incidents/1');

    fireEvent.click(screen.getByTitle('查看服务器明细'));
    expect(screen.getByTestId('route').textContent).toBe('/devices?roles=server');
  });
});

describe('大屏数据映射纯函数', () => {
  it('顶部数字条 5 组按分类着色且离线数正确', () => {
    const groups = digitGroups({
      devices: [{ id: 1, online: true }, { id: 2, online: false }, { id: 3, online: false }] as never,
      incidents: [incident({ id: 1 }), incident({ id: 2, status: 'resolved' })] as never,
      ruleCount: 4,
    });
    expect(groups.map((g) => g.label)).toEqual(['设备', '在线', '告警', '离线', '规则']);
    expect(groups[0].value).toBe(3);
    expect(groups[1].value).toBe(1);
    expect(groups[2].value).toBe(1);
    expect(groups[3].value).toBe(2);
    expect(new Set(groups.map((g) => g.cls)).size).toBe(5);
  });

  it('环形渐变按占比生成连续色段，空数据给中性灰', () => {
    expect(donutGradient([{ value: 3, color: '#f00' }, { value: 1, color: '#0f0' }])).toBe(
      'conic-gradient(#f00 0.00% 75.00%,#0f0 75.00% 100.00%)',
    );
    expect(donutGradient([])).toContain('#e2e8f0');
  });

  it('7 日趋势按 fired_at/resolved_at 分组', () => {
    const now = new Date('2026-11-23T12:00:00Z');
    const days = buildTrend7([
      incident({ id: 1, fired_at: '2026-11-23T01:00:00Z', resolved_at: null, status: 'open' }),
      incident({ id: 2, fired_at: '2026-11-22T01:00:00Z', resolved_at: '2026-11-23T05:00:00Z', status: 'resolved' }),
    ], now);
    expect(days).toHaveLength(7);
    expect(days[6].date).toBe('2026-11-23');
    expect(days[6].fired).toBe(1);
    expect(days[6].closed).toBe(1);
    expect(days[5].fired).toBe(1);
  });

  it('建议映射排名徽章与 P0/P1/P2 并清理实体占位', () => {
    const rows = buildAdvice([{ text: '{{entity:edge:39|设备 39}} 内存超限' }, { text: '磁盘需清理' }, { text: '核查流量' }]);
    expect(rows.map((r) => r.pri)).toEqual(['p0', 'p1', 'p2']);
    expect(rows[0].text).toBe('设备 39 内存超限');
    expect(rows[0].rank).toBe(1);
  });
});
