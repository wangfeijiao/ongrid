import { describe, expect, it } from 'vitest';
import { localizedRuleName } from './alerts';

describe('localizedRuleName', () => {
  it('种子名与后端名一致时返回固定文案', () => {
    expect(localizedRuleName('swap_high', 'Swap 使用率 > 90%')).toContain('90%');
  });

  it('后端阈值变化时跟随后端，不再显示写死的 50%', () => {
    expect(localizedRuleName('swap_high', 'Swap 使用率 > 70%')).toContain('70%');
    expect(localizedRuleName('swap_high', 'Swap 使用率 > 70%')).not.toContain('50%');
    expect(localizedRuleName('disk_full_warning', '磁盘使用率 > 92%')).toContain('92%');
  });

  it('后端名没有阈值时退回种子文案', () => {
    expect(localizedRuleName('swap_high', 'Swap 高占用')).toContain('90%');
  });

  it('未知规则原样返回后端名', () => {
    expect(localizedRuleName('custom_rule', '我的规则')).toBe('我的规则');
  });
});
