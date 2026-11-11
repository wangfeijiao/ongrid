import { describe, expect, it } from 'vitest';
import { fixedDateTime } from './format';

// The Monitor timeseries tooltip passes the raw unix-ms XAxis value to
// recharts' labelFormatter. These cases pin down the readable format and
// the fallback for the malformed values recharts can hand over.
describe('fixedDateTime', () => {
  it('renders unix milliseconds as YYYY-MM-DD HH:MM:SS in local time', () => {
    // Build with local-time components so the assertion holds in any TZ.
    const ts = new Date(2026, 10, 9, 14, 5, 36).getTime();
    expect(fixedDateTime(ts)).toBe('2026-11-09 14:05:36');
  });

  it('zero-pads month, day, hour, minute and second', () => {
    const ts = new Date(2026, 0, 2, 3, 4, 5).getTime();
    expect(fixedDateTime(ts)).toBe('2026-01-02 03:04:05');
  });

  it('accepts Date and ISO string inputs', () => {
    const d = new Date(2026, 10, 9, 14, 5, 36);
    expect(fixedDateTime(d)).toBe('2026-11-09 14:05:36');
    expect(fixedDateTime(d.toISOString())).toBe('2026-11-09 14:05:36');
  });

  it('never leaks a bare timestamp for missing or invalid inputs', () => {
    expect(fixedDateTime(null)).toBe('—');
    expect(fixedDateTime(undefined)).toBe('—');
    expect(fixedDateTime(NaN)).toBe('—');
    expect(fixedDateTime(Number.POSITIVE_INFINITY)).toBe('—');
    expect(fixedDateTime('')).toBe('—');
    expect(fixedDateTime('not-a-date')).toBe('—');
  });
});
