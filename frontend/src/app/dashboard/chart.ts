// Pure geometry for the history charts: segments with gaps, scales, ticks. No Angular, no DOM.

export interface Pt {
  x: number;            // time, ms since epoch
  y: number;            // value
}

/** A gap bigger than this many steps breaks the line: the server was off, don't draw a bridge. */
export const GAP_FACTOR = 2.5;

/** Split a series into drawable runs. null values and time gaps both end a run. */
export function segments(ts: number[], values: (number | null)[], stepMs: number): Pt[][] {
  const out: Pt[][] = [];
  let run: Pt[] = [];
  for (let i = 0; i < ts.length; i++) {
    const v = values[i];
    const gap = i > 0 && ts[i] - ts[i - 1] > stepMs * GAP_FACTOR;
    if (v === null || v === undefined || gap) {
      if (run.length) out.push(run);
      run = [];
    }
    if (v !== null && v !== undefined) run.push({ x: ts[i], y: v });
  }
  if (run.length) out.push(run);
  return out;
}

/** 1-2-5 rounding up: 37 -> 50, 830 -> 1000, 0 -> 1. A y axis that ends on a round number. */
export function niceMax(value: number): number {
  if (!(value > 0)) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(value)));
  for (const f of [1, 2, 5, 10]) if (value <= f * exp) return f * exp;
  return 10 * exp;
}

export type Scale = (v: number) => number;

export function linear(d0: number, d1: number, r0: number, r1: number): Scale {
  const span = d1 - d0 || 1;
  return (v) => r0 + ((v - d0) / span) * (r1 - r0);
}

/** "M x,y L x,y ..." for one run. A single point becomes a short dash so it stays visible. */
export function linePath(run: Pt[], sx: Scale, sy: Scale): string {
  if (run.length === 1) {
    const x = sx(run[0].x), y = sy(run[0].y);
    return `M${(x - 2).toFixed(1)},${y.toFixed(1)}L${(x + 2).toFixed(1)},${y.toFixed(1)}`;
  }
  return run.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join('');
}

/** Closed shape between the line and the baseline (the soft fill under the average). */
export function areaPath(run: Pt[], sx: Scale, sy: Scale, baseline: number): string {
  if (run.length < 2) return '';
  const top = linePath(run, sx, sy);
  return `${top}L${sx(run[run.length - 1].x).toFixed(1)},${baseline.toFixed(1)}`
    + `L${sx(run[0].x).toFixed(1)},${baseline.toFixed(1)}Z`;
}

/** Index of the timestamp closest to x (ts sorted ascending). -1 for an empty list. */
export function nearestIndex(ts: number[], x: number): number {
  if (!ts.length) return -1;
  let lo = 0, hi = ts.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (ts[mid] <= x) lo = mid; else hi = mid;
  }
  return Math.abs(ts[lo] - x) <= Math.abs(ts[hi] - x) ? lo : hi;
}

/** Evenly spaced x ticks between t0 and t1 (inclusive). */
export function timeTicks(t0: number, t1: number, count = 4): number[] {
  if (count < 2 || t1 <= t0) return [t0];
  return Array.from({ length: count }, (_, i) => t0 + ((t1 - t0) * i) / (count - 1));
}

/** Up to a day: "14:05"; longer: "06.10." - local time of the viewer. */
export function tickLabel(ms: number, spanMs: number): string {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, '0');
  return spanMs <= 86_400_000 ? `${p(d.getHours())}:${p(d.getMinutes())}` : `${p(d.getDate())}.${p(d.getMonth() + 1)}.`;
}

/** Tooltip time: "06.10. 14:05:30" (raw) or "06.10. 14:05" (condensed buckets). */
export function pointLabel(ms: number, withSeconds: boolean): string {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, '0');
  const time = `${p(d.getHours())}:${p(d.getMinutes())}${withSeconds ? ':' + p(d.getSeconds()) : ''}`;
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}. ${time}`;
}

/** Largest value, ignoring gaps; 0 for an empty series. A loop, not Math.max(...): 1440 points are fine either way, 100k are not. */
export function maxOf(values: (number | null)[]): number {
  let top = 0;
  for (const v of values) if (v !== null && v !== undefined && v > top) top = v;
  return top;
}
