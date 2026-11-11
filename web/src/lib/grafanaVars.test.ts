import { describe, expect, it } from 'vitest';
import { rateIntervalForRange, stepForRange, stepMsForSpan, substitute } from './grafanaVars';

const DAY = 86400_000;

// Custom windows arrive as range="custom", which the old rangeToMs-based
// helpers silently treated as 1h. With 90-day retention that pinned every
// long query to a 15s step (~518k points per series). These cases pin the
// span-aware behaviour down.
describe('stepMsForSpan', () => {
  it('keeps short windows at the 15s floor', () => {
    expect(stepMsForSpan(60_000)).toBe(15_000);
    expect(stepMsForSpan(3600_000)).toBe(15_000);
  });

  it('keeps every window near 360 buckets as it grows', () => {
    expect(stepMsForSpan(6 * 3600_000)).toBe(60_000); // 1m
    expect(stepMsForSpan(7 * DAY)).toBe(28 * 60_000); // 28m
    expect(stepMsForSpan(30 * DAY)).toBe(2 * 3600_000); // 2h
    expect(stepMsForSpan(90 * DAY)).toBe(6 * 3600_000); // 6h
  });

  it('aligns buckets above one minute to whole minutes', () => {
    const odd = stepMsForSpan(10 * DAY);
    expect(odd % 60_000).toBe(0);
  });

  it('falls back to a 1h window for invalid spans', () => {
    expect(stepMsForSpan(Number.NaN)).toBe(15_000);
    expect(stepMsForSpan(0)).toBe(15_000);
  });
});

describe('stepForRange', () => {
  it('still derives step from the range token when no span is given', () => {
    expect(stepForRange('1h')).toBe('15s');
    expect(stepForRange('7d')).toBe('28m');
  });

  it('prefers the real span for custom windows', () => {
    expect(stepForRange('custom', 7 * DAY)).toBe('28m');
    expect(stepForRange('custom', 30 * DAY)).toBe('2h');
    expect(stepForRange('custom', 90 * DAY)).toBe('6h');
  });

  it('caps a 90-day query at ~360 points per series', () => {
    const span = 90 * DAY;
    expect(span / stepMsForSpan(span)).toBeLessThanOrEqual(360);
    expect(stepForRange('custom', span)).toBe('6h');
  });
});

describe('rateIntervalForRange', () => {
  it('keeps the historical tiers for windows up to 7 days', () => {
    expect(rateIntervalForRange('15m')).toBe('1m');
    expect(rateIntervalForRange('6h')).toBe('5m');
    expect(rateIntervalForRange('24h')).toBe('5m');
    expect(rateIntervalForRange('7d')).toBe('1h');
  });

  it('scales the rate window with long custom spans', () => {
    expect(rateIntervalForRange('custom', 30 * DAY)).toBe('2h');
    expect(rateIntervalForRange('custom', 90 * DAY)).toBe('6h');
  });
});

describe('substitute', () => {
  it('sizes $__rate_interval and $__interval from the real span', () => {
    const ctx = { range: 'custom', query: new URLSearchParams(), spanMs: 90 * DAY };
    expect(substitute('rate(node_cpu_seconds_total[$__rate_interval])', ctx))
      .toBe('rate(node_cpu_seconds_total[6h])');
    expect(substitute('avg_over_time(up[$__interval])', ctx))
      .toBe('avg_over_time(up[6h])');
  });

  it('keeps the old 1h fallback when spanMs is absent', () => {
    const ctx = { range: 'custom', query: new URLSearchParams() };
    expect(substitute('rate(up[$__rate_interval])', ctx)).toBe('rate(up[5m])');
  });
});
