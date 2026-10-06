import {
  Component, DestroyRef, ElementRef, afterNextRender, computed, inject, input, signal, viewChild,
} from '@angular/core';

import {
  GAP_FACTOR, areaPath, linePath, linear, maxOf, nearestIndex, niceMax, pointLabel, segments, tickLabel, timeTicks,
} from './chart';
import { num } from './format';

const MARGIN = { top: 8, right: 8, bottom: 20, left: 44 };
const HEIGHT = 128;
const TOOLTIP_W = 150;

/**
 * One metric over time: average as a 2px line with a soft fill, peak as a thin dashed line
 * (only when the data is condensed, i.e. average and peak differ), optional threshold line,
 * hover crosshair with tooltip. Hand-built SVG: six small charts don't justify a chart library.
 */
@Component({
  selector: 'app-line-chart',
  host: { class: 'block' },
  templateUrl: './line-chart.html',
})
export class LineChart {
  readonly label = input.required<string>();
  readonly unit = input('');
  readonly digits = input(0);
  readonly ts = input.required<number[]>();                 // ms, ascending
  readonly avg = input.required<(number | null)[]>();
  readonly max = input<(number | null)[] | null>(null);
  readonly stepS = input(10);
  readonly from = input.required<number>();                 // x axis domain (server clock), ms
  readonly to = input.required<number>();
  readonly scaleMax = input<number | null>(null);           // fixed top (100 %, VRAM total); null = auto
  readonly warn = input<number | null>(null);               // threshold line

  readonly margin = MARGIN;
  readonly height = HEIGHT;
  readonly width = signal(400);                             // follows the container (ResizeObserver)
  readonly hover = signal<number | null>(null);             // index into ts
  private readonly plot = viewChild.required<ElementRef<HTMLDivElement>>('plot');

  readonly stepMs = computed(() => this.stepS() * 1000);
  readonly showMax = computed(() => {
    const max = this.max(), avg = this.avg();
    return !!max && max.some((v, i) => v !== null && v !== avg[i]);
  });
  readonly yMax = computed(() => {
    const fixed = this.scaleMax();
    if (fixed && fixed > 0) return fixed;
    const top = Math.max(maxOf(this.showMax() ? this.max()! : this.avg()), this.warn() ?? 0);
    return niceMax(top * 1.1);
  });
  readonly plotWidth = computed(() => Math.max(1, this.width() - MARGIN.left - MARGIN.right));
  readonly sx = computed(() => linear(this.from(), this.to(), MARGIN.left, this.width() - MARGIN.right));
  readonly sy = computed(() => linear(0, this.yMax(), HEIGHT - MARGIN.bottom, MARGIN.top));

  readonly avgRuns = computed(() => segments(this.ts(), this.avg(), this.stepMs()));
  readonly avgPaths = computed(() => this.avgRuns().map((r) => linePath(r, this.sx(), this.sy())));
  readonly areaPaths = computed(() =>
    this.avgRuns().map((r) => areaPath(r, this.sx(), this.sy(), HEIGHT - MARGIN.bottom)).filter(Boolean));
  readonly maxPaths = computed(() => this.showMax()
    ? segments(this.ts(), this.max()!, this.stepMs()).map((r) => linePath(r, this.sx(), this.sy()))
    : []);

  readonly yTicks = computed(() => {
    const top = this.yMax();
    const digits = top < 10 ? 1 : 0;
    return [0, top / 2, top].map((v) => ({ v, y: this.sy()(v), label: num(v, digits) }));
  });
  readonly xTicks = computed(() => {
    const span = this.to() - this.from();
    const ticks = timeTicks(this.from(), this.to(), this.width() < 320 ? 3 : 4);
    return ticks.map((t, i) => ({
      x: this.sx()(t), label: tickLabel(t, span),
      anchor: i === 0 ? 'start' : i === ticks.length - 1 ? 'end' : 'middle',
    }));
  });
  readonly warnY = computed(() => {
    const w = this.warn();
    return w !== null && w <= this.yMax() ? this.sy()(w) : null;
  });

  readonly latest = computed(() => {
    const avg = this.avg();
    for (let i = avg.length - 1; i >= 0; i--) if (avg[i] !== null) return avg[i];
    return null;
  });
  readonly ariaLabel = computed(() => {
    const now = this.latest();
    return `${this.label()}: ${now === null ? 'keine Daten' : 'zuletzt ' + num(now, this.digits()) + ' ' + this.unit()}`;
  });

  readonly hoverPoint = computed(() => {
    const i = this.hover();
    if (i === null || i < 0 || i >= this.ts().length) return null;
    const t = this.ts()[i], a = this.avg()[i], m = this.max()?.[i] ?? null;
    return {
      x: this.sx()(t), y: a === null ? null : this.sy()(a), label: pointLabel(t, this.stepS() < 60),
      avg: a, max: m,
    };
  });
  readonly tooltipLeft = computed(() => {
    const h = this.hoverPoint();
    if (!h) return 0;
    return Math.min(Math.max(0, h.x - TOOLTIP_W / 2), Math.max(0, this.width() - TOOLTIP_W));
  });

  constructor() {
    if (typeof ResizeObserver === 'undefined') return;           // jsdom (tests): keep the default width
    const ro = new ResizeObserver(([entry]) => this.width.set(Math.max(200, Math.round(entry.contentRect.width))));
    afterNextRender(() => ro.observe(this.plot().nativeElement));
    inject(DestroyRef).onDestroy(() => ro.disconnect());
  }

  value(v: number | null): string {
    return v === null ? '—' : `${num(v, this.digits())} ${this.unit()}`;
  }

  /** Pointer x (relative to the svg) -> nearest point; nothing when the pointer is over a gap. */
  hoverAt(px: number): void {
    const t = this.from() + ((px - MARGIN.left) / this.plotWidth()) * (this.to() - this.from());
    const i = nearestIndex(this.ts(), t);
    const close = i >= 0 && Math.abs(this.ts()[i] - t) <= this.stepMs() * GAP_FACTOR;
    this.hover.set(close ? i : null);
  }

  onMove(ev: PointerEvent): void {
    const rect = (ev.currentTarget as Element).getBoundingClientRect();
    this.hoverAt(ev.clientX - rect.left);
  }
}
