import {
  areaPath, linePath, linear, maxOf, nearestIndex, niceMax, pointLabel, segments, tickLabel, timeTicks,
} from './chart';

describe('chart geometry', () => {
  it('breaks lines at gaps and missing values', () => {
    const ts = [0, 10_000, 20_000, 80_000, 90_000, 100_000];
    const runs = segments(ts, [1, 2, 3, 4, null, 6], 10_000);
    // 20 s -> 80 s is more than 2.5 steps: server was off; index 4 is a failed source
    expect(runs.map((r) => r.map((p) => p.y))).toEqual([[1, 2, 3], [4], [6]]);
    expect(segments([], [], 10_000)).toEqual([]);
  });

  it('rounds the y axis up to 1-2-5 steps', () => {
    expect([0, 0.7, 37, 83, 830, 16303].map(niceMax)).toEqual([1, 1, 50, 100, 1000, 20000]);
  });

  it('draws paths in pixel space, a lone point as a short dash', () => {
    const sx = linear(0, 10, 0, 100), sy = linear(0, 100, 50, 0);
    expect(linePath([{ x: 0, y: 0 }, { x: 10, y: 100 }], sx, sy)).toBe('M0.0,50.0L100.0,0.0');
    expect(linePath([{ x: 5, y: 50 }], sx, sy)).toBe('M48.0,25.0L52.0,25.0');
    expect(areaPath([{ x: 0, y: 0 }, { x: 10, y: 100 }], sx, sy, 50)).toBe('M0.0,50.0L100.0,0.0L100.0,50.0L0.0,50.0Z');
    expect(areaPath([{ x: 5, y: 50 }], sx, sy, 50)).toBe('');
  });

  it('finds the nearest point', () => {
    const ts = [0, 10, 20, 30];
    expect([-5, 4, 6, 29, 99].map((x) => nearestIndex(ts, x))).toEqual([0, 0, 1, 3, 3]);
    expect(nearestIndex([], 5)).toBe(-1);
  });

  it('ticks and labels', () => {
    expect(timeTicks(0, 300, 4)).toEqual([0, 100, 200, 300]);
    const t = new Date(2026, 9, 6, 14, 5, 30).getTime();
    expect(tickLabel(t, 3_600_000)).toBe('14:05');
    expect(tickLabel(t, 7 * 86_400_000)).toBe('06.10.');
    expect(pointLabel(t, true)).toBe('06.10. 14:05:30');
    expect(pointLabel(t, false)).toBe('06.10. 14:05');
  });

  it('max ignores gaps', () => {
    expect(maxOf([null, 3, 7, null])).toBe(7);
    expect(maxOf([])).toBe(0);
  });
});
