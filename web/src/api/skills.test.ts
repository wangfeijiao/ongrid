import { describe, expect, it } from 'vitest';
import { HttpResponse, http } from 'msw';

import { server } from '@/test/msw-server';

import { executeSkill } from './skills';

describe('executeSkill', () => {
  // host 作用域技能按设备寻址（device_id），由服务端反查该设备当前接入的
  // edge——edge 会随重装/切换而变，前端不该持有 edge id。
  it('sends the selected device id for host-scoped skills', async () => {
    let requestBody: unknown;
    server.use(
      http.post('/api/v1/skills/host_probe_tcp/execute', async ({ request }) => {
        requestBody = await request.json();
        return HttpResponse.json({ result: { ok: true } });
      }),
    );

    await executeSkill('host_probe_tcp', 52, { target: '127.0.0.1:22' });

    expect(requestBody).toEqual({
      device_id: 52,
      params: { target: '127.0.0.1:22' },
    });
  });

  it('omits the device id for manager-scoped skills', async () => {
    let requestBody: unknown;
    server.use(
      http.post('/api/v1/skills/query_devices/execute', async ({ request }) => {
        requestBody = await request.json();
        return HttpResponse.json({ result: { items: [] } });
      }),
    );

    await executeSkill('query_devices', null, { limit: 10 });

    expect(requestBody).toEqual({ params: { limit: 10 } });
  });
});
