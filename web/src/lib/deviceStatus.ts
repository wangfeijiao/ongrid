import type { Device } from '@/api/devices';

/** "在线"判定的唯一来源，大屏计数与设备列表过滤都必须走这里，避免两处口径漂移。
 *
 *  - SNMP 纳管的网络设备没有 agent 心跳，`online` 恒为 false，只能按
 *    `reachability_status`（reachable / online / up）判定；
 *  - 其余设备以 agent 上报的 `online` 字段为准。
 */
export function isDeviceOnline(
  d: Pick<Device, 'os' | 'online' | 'reachability_status'>,
): boolean {
  if ((d.os ?? '').trim().toLowerCase() === 'network') {
    return ['reachable', 'online', 'up'].includes(
      (d.reachability_status ?? '').trim().toLowerCase(),
    );
  }
  return d.online === true;
}
