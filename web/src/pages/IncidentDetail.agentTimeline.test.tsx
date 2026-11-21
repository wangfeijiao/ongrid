import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { beforeAll, beforeEach, expect, it, vi } from 'vitest';
import { server } from '@/test/msw-server';
import IncidentDetailPage from './IncidentDetail';

const auth = vi.hoisted(() => ({ role: 'admin' }));
vi.mock('@/store/auth', () => ({
  useAuth: Object.assign(<T,>(selector: (s: typeof auth) => T) => selector(auth), {
    getState: () => ({ logout: () => {} }),
  }),
  getToken: () => null,
  getRefreshToken: () => null,
}));

const LONG_ANSWER = [
  '## 结论（先说重点）',
  '',
  '**Incident 262 是设备 350 根分区高占用（94%）**，已持续约 2 天。',
  '',
  ...Array.from({ length: 20 }, (_, i) => `- 第 ${i + 1} 条观察：磁盘占用持续上升，需要进一步下钻。`),
].join('\n');

const SHORT_ANSWER = '短时间内已确认事实，无需展开。';

// JSDOM reports 0 for layout metrics, so overflow detection would always
// be false. Emulate a 3-line clamp: long text scrolls, short text fits.
beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
    configurable: true,
    get() {
      return (this.textContent ?? '').length > 300 ? 300 : 20;
    },
  });
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    get() {
      return 60;
    },
  });
});

beforeEach(() => {
  localStorage.setItem('ongrid-locale', 'zh-CN');
});

function mockApis(answer: string) {
  server.use(
    http.get('/api/v1/alerts/incidents/262', () =>
      HttpResponse.json({
        id: 262,
        rule_key: 'disk_high',
        rule_name: '磁盘高占用',
        severity: 'warning',
        status: 'open',
        summary: 'disk_high',
        target_type: 'device',
        event_count: 573,
        fired_at: '2026-11-19T10:15:39Z',
        last_fired_at: '2026-11-21T09:51:00Z',
        updated_at: '2026-11-21T09:51:00Z',
      }),
    ),
    http.get('/api/v1/alerts/incidents/262/events', () => HttpResponse.json({ items: [], total: 0 })),
    http.get('/api/v1/alerts/incidents/262/investigation', () =>
      HttpResponse.json({
        id: 'report-262',
        incident_id: 262,
        status: 'not_started',
        tool_call_count: 0,
        created_at: '2026-11-21T09:00:00Z',
        updated_at: '2026-11-21T09:00:00Z',
      }),
    ),
    http.get('/api/v1/chat/sessions', () =>
      HttpResponse.json({
        items: [
          {
            id: 'sess-1234abcd',
            user_id: 1,
            title: '诊断 #262 · 磁盘高占用',
            related_incident_id: 262,
            created_at: '2026-11-21T09:00:00Z',
          },
        ],
        total: 1,
      }),
    ),
    http.get('/api/v1/chat/sessions/:id/messages', () =>
      HttpResponse.json({
        items: [{ id: 'msg-1', role: 'assistant', content: answer, created_at: '2026-11-21T09:05:00Z' }],
        total: 1,
      }),
    ),
  );
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={['/alerts/262']}>
      <Routes>
        <Route path="/alerts/:id" element={<IncidentDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

it('expands a clamped diagnosis answer and renders its markdown', async () => {
  mockApis(LONG_ANSWER);
  renderPage();

  const expand = await screen.findByRole('button', { name: /展开全文/ });
  await userEvent.click(expand);

  expect(await screen.findByRole('heading', { name: '结论（先说重点）' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /收起/ })).toBeInTheDocument();
});

it('keeps short answers without an expand control', async () => {
  mockApis(SHORT_ANSWER);
  renderPage();

  await screen.findByText(/短时间内已确认事实/);
  expect(screen.queryByRole('button', { name: /展开全文/ })).not.toBeInTheDocument();
});
