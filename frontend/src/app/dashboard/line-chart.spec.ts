import { TestBed } from '@angular/core/testing';

import { LineChart } from './line-chart';

describe('LineChart', () => {
  function render(inputs: Record<string, unknown>) {
    const fixture = TestBed.createComponent(LineChart);
    const base = { label: 'VRAM', unit: 'MiB', ts: [0, 10_000, 20_000], avg: [100, 200, 300], stepS: 10,
                   from: 0, to: 30_000 };
    for (const [k, v] of Object.entries({ ...base, ...inputs })) fixture.componentRef.setInput(k, v);
    fixture.detectChanges();
    return { chart: fixture.componentInstance, el: fixture.nativeElement as HTMLElement, fixture };
  }

  it('draws one line, no legend when average and peak are the same', () => {
    const { chart, el } = render({ max: [100, 200, 300] });
    expect(chart.showMax()).toBe(false);
    expect(el.querySelectorAll('path.stroke-sky-400').length).toBe(1);
    expect(el.textContent).not.toContain('Spitze');
    expect(chart.latest()).toBe(300);
  });

  it('adds the dashed peak line and a legend for condensed data', () => {
    const { chart, el } = render({ max: [150, 250, 300] });
    expect(chart.showMax()).toBe(true);
    expect(el.querySelectorAll('path.stroke-sky-300\\/60').length).toBe(1);
    expect(el.textContent).toContain('Spitze');
  });

  it('uses the fixed scale and draws the threshold below it only', () => {
    expect(render({ scaleMax: 16303, warn: 14803 }).chart.yMax()).toBe(16303);
    expect(render({ scaleMax: 100, warn: 120 }).chart.warnY()).toBeNull();
    expect(render({ avg: [10, 37, 20] }).chart.yMax()).toBe(50);          // auto: 37 * 1.1 -> 50
  });

  it('hover snaps to the nearest point but not across a gap', () => {
    const { chart, fixture, el } = render({ ts: [0, 10_000, 100_000], avg: [1, 2, 3], to: 100_000 });
    const xOf = (t: number) => chart.sx()(t);
    chart.hoverAt(xOf(9_000));
    fixture.detectChanges();
    expect(chart.hover()).toBe(1);
    expect(el.textContent).toContain('Ø 2 MiB');
    chart.hoverAt(xOf(55_000));                                           // middle of the gap
    expect(chart.hover()).toBeNull();
  });

  it('says so when there is nothing to draw', () => {
    const { el } = render({ ts: [], avg: [] });
    expect(el.textContent).toContain('Noch keine Daten');
  });
});
