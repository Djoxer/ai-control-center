import { Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';

import { Api } from '../api/api';
import { dashboardHistory } from '../api/fn/dashboard/dashboard-history';
import { DashboardHistory } from '../api/models/dashboard-history';
import { HistorySeries } from '../api/models/history-series';
import { LineChart } from './line-chart';

export type HistoryRange = DashboardHistory['range'];

export const RANGES: { key: HistoryRange; label: string; refreshMs: number }[] = [
  { key: '1h', label: '1 h', refreshMs: 15_000 },          // raw rows arrive every 10 s
  { key: '6h', label: '6 h', refreshMs: 60_000 },
  { key: '24h', label: '24 h', refreshMs: 60_000 },
  { key: '7d', label: '7 T', refreshMs: 300_000 },
  { key: '30d', label: '30 T', refreshMs: 300_000 },
];

const META: Record<HistorySeries['metric'], { label: string; digits: number }> = {
  gpu_util: { label: 'GPU-Last', digits: 0 },
  vram_used_mib: { label: 'VRAM', digits: 0 },
  temp_c: { label: 'Temperatur', digits: 0 },
  power_w: { label: 'Leistung', digits: 0 },
  cpu_percent: { label: 'CPU', digits: 0 },
  ram_percent: { label: 'Arbeitsspeicher', digits: 0 },
};

@Component({
  selector: 'app-history-panel',
  host: { class: 'block' },
  imports: [LineChart],
  templateUrl: './history-panel.html',
})
export class HistoryPanel implements OnInit {
  private readonly api = inject(Api);
  private readonly destroyRef = inject(DestroyRef);

  readonly ranges = RANGES;
  readonly range = signal<HistoryRange>('1h');
  readonly data = signal<DashboardHistory | null>(null);
  readonly error = signal<string | null>(null);
  readonly loading = signal(false);

  /** Shared x data for all six charts; the range domain comes from the server clock. */
  readonly charts = computed(() => {
    const d = this.data();
    if (!d) return [];
    const ts = d.ts.map((t) => Date.parse(t));
    const from = Date.parse(d.since), to = Date.parse(d.until);
    const condensed = d.resolution !== 'raw';
    return d.series.map((s) => ({
      metric: s.metric, label: META[s.metric]?.label ?? s.metric, digits: META[s.metric]?.digits ?? 0,
      unit: s.unit, ts, avg: s.avg, max: condensed ? s.max : null, stepS: d.stepS, from, to,
      scaleMax: s.scaleMax, warn: s.warn,
    }));
  });

  private seq = 0;                                   // answers of outdated requests are dropped
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor() {
    this.destroyRef.onDestroy(() => this.stopTimer());
  }

  ngOnInit(): void {
    this.setRange('1h');
  }

  setRange(range: HistoryRange): void {
    this.range.set(range);
    void this.load();
    this.stopTimer();
    const every = RANGES.find((r) => r.key === range)?.refreshMs ?? 60_000;
    this.timer = setInterval(() => void this.load(), every);
  }

  async load(): Promise<void> {
    const seq = ++this.seq;
    this.loading.set(true);
    try {
      const d = await this.api.invoke(dashboardHistory, { range: this.range() });
      if (seq !== this.seq) return;
      this.data.set(d);
      this.error.set(null);
    } catch (e) {
      if (seq === this.seq) this.error.set(e instanceof Error ? e.message : 'Verlauf nicht verfügbar');
    } finally {
      if (seq === this.seq) this.loading.set(false);
    }
  }

  private stopTimer(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }
}
