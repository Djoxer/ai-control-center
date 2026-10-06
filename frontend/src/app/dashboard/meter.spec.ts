import { TestBed } from '@angular/core/testing';

import { Meter } from './meter';

describe('Meter', () => {
  function render(value: number | null, tone: 'normal' | 'warning' | 'critical' = 'normal') {
    const fixture = TestBed.createComponent(Meter);
    fixture.componentRef.setInput('value', value);
    fixture.componentRef.setInput('tone', tone);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    return { track: el.querySelector('[role="meter"]')!, fill: el.querySelector<HTMLElement>('[role="meter"] > div') };
  }

  it('clamps to 0..100 and exposes the value to screen readers', () => {
    const { track, fill } = render(120, 'warning');
    expect(track.getAttribute('aria-valuenow')).toBe('100');
    expect(fill?.style.width).toBe('100%');
    expect(fill?.className).toContain('bg-amber-400');
  });

  it('draws an empty track for unknown values', () => {
    const { track, fill } = render(null);
    expect(track.getAttribute('aria-valuenow')).toBeNull();
    expect(fill).toBeNull();
  });
});
