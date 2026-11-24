import { describe, expect, it } from 'vitest';

import { isDeviceOnline } from './deviceStatus';

describe('isDeviceOnline', () => {
  it('普通设备以 agent 上报的 online 为准', () => {
    expect(isDeviceOnline({ online: true })).toBe(true);
    // 有 reachability 也不能把普通设备判成在线
    expect(isDeviceOnline({ online: false, reachability_status: 'reachable' })).toBe(false);
  });

  it('SNMP 网络设备以 reachability_status 为准，忽略恒为 false 的 online', () => {
    expect(isDeviceOnline({ os: 'network', online: false, reachability_status: 'reachable' })).toBe(true);
    expect(isDeviceOnline({ os: 'Network', online: false, reachability_status: 'up' })).toBe(true);
    expect(isDeviceOnline({ os: 'network', online: true, reachability_status: 'down' })).toBe(false);
    expect(isDeviceOnline({ os: 'network', online: false, reachability_status: undefined })).toBe(false);
  });
});
