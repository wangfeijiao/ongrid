import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import { DeviceHoverLayer, DeviceTooltipContent, type DeviceHoverState } from './PromQLPanel';

// Regression: recharts 2.15 drops a JSX `name` returned from Tooltip.formatter
// (only the value survives). The device hover card must therefore be rendered
// as a custom `content` component. This test locks in that both the machine
// name (line 1) and identity detail (line 2) are present in the DOM.
describe('DeviceTooltipContent (OPT-014 fix)', () => {
  it('renders machine name + detail + value, never the bare device_id', () => {
    const series = [
      { key: 's0', label: 'srv-10-7-30-74', color: '#f00', deviceId: 1, detail: '在线培训考试系统 · 10.7.30.74 · #1' },
      { key: 's1', label: 'srv-10-7-30-75', color: '#0f0', deviceId: 2 },
    ];
    const formatY = (v: number) => `${v} KiB/s`;
    render(
      <DeviceTooltipContent
        active={true}
        payload={[
          { dataKey: 's0', value: 200, color: '#f00' },
          { dataKey: 's1', value: 50, color: '#0f0' },
        ]}
        label={1794137136783}
        series={series}
        formatY={formatY}
      />
    );
    // machine names on line 1
    expect(screen.getByText('srv-10-7-30-74')).toBeTruthy();
    expect(screen.getByText('srv-10-7-30-75')).toBeTruthy();
    // detail on line 2
    expect(screen.getByText('在线培训考试系统 · 10.7.30.74 · #1')).toBeTruthy();
    // formatted values
    expect(screen.getByText('200 KiB/s')).toBeTruthy();
    expect(screen.getByText('50 KiB/s')).toBeTruthy();
    // no raw device_id label leaked to operators
    expect(screen.queryByText('device_id=1')).toBeNull();
  });

  it('caps height and shows a scroll hint when there are many series', () => {
    const many = Array.from({ length: 60 }, (_, i) => ({
      key: `s${i}`, label: `srv-${i}`, color: '#fff', deviceId: i, detail: `10.7.30.${i}`,
    }));
    const { container } = render(
      <DeviceTooltipContent
        active={true}
        payload={many.map((s) => ({ dataKey: s.key, value: 1, color: s.color }))}
        label={1794137136783}
        series={many}
        formatY={(v) => `${v} KiB/s`}
      />
    );
    const card = container.querySelector('.og-device-tooltip') as HTMLElement;
    expect(card).toBeTruthy();
    expect(card.style.overflowY).toBe('auto');
    expect(card.style.maxHeight).toContain('vh');
    expect(card.style.pointerEvents).toBe('auto');
    expect(screen.getByText(/60 条 · 滚动查看/)).toBeTruthy();
  });
});

// Regression for the "mouse can't get into the hover card / card is covered"
// bug: the card must live on document.body (not inside the chart's
// overflow:hidden wrapper), be fixed-positioned with a high z-index, and
// notify its owner on enter so hover isn't cancelled while scrolling.
describe('DeviceHoverLayer (portal)', () => {
  const series = [
    { key: 's0', label: 'srv-10-7-30-74', color: '#f00', deviceId: 1, detail: '10.7.30.74' },
  ];
  const formatY = (v: number) => `${v} KiB/s`;
  const hover: DeviceHoverState = {
    payload: [{ dataKey: 's0', value: 200, color: '#f00' }],
    label: 1794137136783,
    clientX: 240,
    clientY: 180,
    id: 1,
  };

  it('portals the card to body with fixed positioning and a high z-index', () => {
    const { container } = render(
      <DeviceHoverLayer
        hover={hover}
        series={series}
        formatY={formatY}
        onPointerEnter={() => {}}
        onPointerLeave={() => {}}
      />,
    );
    // Not rendered inside the chart container — otherwise overflow:hidden
    // would clip it and neighbours would paint over it.
    expect(container.querySelector('.og-device-tooltip')).toBeNull();
    const layer = document.body.querySelector('.og-device-hover-layer') as HTMLElement;
    expect(layer).toBeTruthy();
    expect(layer.style.position).toBe('fixed');
    expect(layer.style.zIndex).toBe('2000');
    expect(layer.style.pointerEvents).toBe('auto');
    expect(screen.getByText('srv-10-7-30-74')).toBeTruthy();
  });

  it('reports pointer enter/leave so the chart can keep hover alive while scrolling', () => {
    const onEnter = vi.fn();
    const onLeave = vi.fn();
    render(
      <DeviceHoverLayer
        hover={hover}
        series={series}
        formatY={formatY}
        onPointerEnter={onEnter}
        onPointerLeave={onLeave}
      />,
    );
    const layer = document.body.querySelector('.og-device-hover-layer') as HTMLElement;
    fireEvent.mouseEnter(layer);
    fireEvent.mouseLeave(layer);
    expect(onEnter).toHaveBeenCalledTimes(1);
    expect(onLeave).toHaveBeenCalledTimes(1);
  });
});
