import { Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';

import { Api } from '../api/api';
import { dashboardSnapshot } from '../api/fn/dashboard/dashboard-snapshot';
import { DashboardSnapshot } from '../api/models/dashboard-snapshot';
import { DashboardWarning } from '../api/models/dashboard-warning';
import { DiskUsage } from '../api/models/disk-usage';
import { LoadedModel } from '../api/models/loaded-model';
import { StreamService } from '../core/stream.service';
import { Icon } from '../layout/icon';
import { EventsPanel } from './events-panel';
import { ExportDialog } from './export-dialog';
import * as fmt from './format';
import { HistoryPanel } from './history-panel';
import { Meter, MeterTone } from './meter';
import { ui } from '../ui/tokens';

export const SNAPSHOT_TOPIC = 'dashboard.snapshot';
/** No snapshot for this long = the numbers on screen are old. Backend sends every 2 s (default). */
export const STALE_AFTER_MS = 10_000;

@Component({
  selector: 'app-dashboard',
  imports: [Icon, Meter, EventsPanel, HistoryPanel, ExportDialog],
  templateUrl: './dashboard.html',
})
export class Dashboard implements OnInit {
  private readonly api = inject(Api);
  private readonly stream = inject(StreamService);
  private readonly destroyRef = inject(DestroyRef);

  readonly fmt = fmt;                                      // template access to the pure helpers
  readonly ui = ui;                                        // shared class strings (ui/tokens.ts)
  readonly snapshot = signal<DashboardSnapshot | null>(null);
  readonly error = signal<string | null>(null);
  readonly receivedAt = signal<number | null>(null);        // browser clock: when the last snapshot arrived
  readonly now = signal(Date.now());                        // ticks every second for the stale check
  readonly streamState = this.stream.state;
  readonly exportOpen = signal(false);

  readonly stale = computed(() => {
    const at = this.receivedAt();
    return at !== null && this.now() - at > STALE_AFTER_MS;
  });
  readonly live = computed(() => this.streamState() === 'open' && !this.stale());

  readonly warnings = computed(() => this.snapshot()?.warnings ?? []);
  /** "vram_low", "disk_low:D:\" ... -> meters turn amber exactly when the backend warns. */
  private readonly warned = computed(() => new Set(this.warnings().map((w) => this.warnKey(w.code, w.subject))));

  readonly gpu = computed(() => this.snapshot()?.gpu ?? null);
  readonly vramPercent = computed(() => {
    const g = this.gpu();
    return g ? fmt.share(g.vramUsedMib, g.vramTotalMib) : null;
  });
  readonly vramFreeMib = computed(() => {
    const g = this.gpu();
    return g ? g.vramTotalMib - g.vramUsedMib : null;
  });
  readonly ramPercent = computed(() => {
    const h = this.snapshot()?.host;
    return h ? fmt.share(h.ramUsedBytes, h.ramTotalBytes) : null;
  });
  readonly simulatedLabel = computed(() => (this.snapshot()?.simulated ?? []).map((s) => this.sourceLabel(s)).join(', '));
  readonly ollamaLatency = computed(() =>
    this.snapshot()?.services.find((x) => x.key === 'ollama')?.latencyMs ?? null);

  constructor() {
    const off = this.stream.on<DashboardSnapshot>(SNAPSHOT_TOPIC, (s) => this.accept(s));
    const timer = setInterval(() => this.now.set(Date.now()), 1000);
    this.destroyRef.onDestroy(() => {
      off();
      clearInterval(timer);
    });
  }

  async ngOnInit(): Promise<void> {
    // the stream would deliver a snapshot within 2 s anyway; the GET just avoids an empty page meanwhile
    try {
      this.accept(await this.api.invoke(dashboardSnapshot));
    } catch (e) {
      if (!this.snapshot()) this.error.set(this.message(e));   // a live snapshot may have won the race
    }
  }

  /** Newest wins: a slow GET answer must not overwrite a newer snapshot from the stream. */
  accept(s: DashboardSnapshot): void {
    const current = this.snapshot();
    if (current && Date.parse(s.ts) < Date.parse(current.ts)) return;
    this.snapshot.set(s);
    this.receivedAt.set(Date.now());
    this.now.set(Date.now());
    this.error.set(null);
  }

  // ---- view helpers -----------------------------------------------------------------------

  tone(code: DashboardWarning['code'], subject: string | null = null): MeterTone {
    return this.warned().has(this.warnKey(code, subject)) ? 'warning' : 'normal';
  }

  diskPercent(d: DiskUsage): number | null {
    return fmt.share(d.usedBytes, d.totalBytes);
  }

  sourceError(source: string): string | null {
    return this.snapshot()?.errors.find((e) => e.source === source)?.message ?? null;
  }

  levelLabel(level: DashboardWarning['level']): string {
    return { critical: 'Kritisch', warning: 'Warnung', info: 'Hinweis' }[level];
  }

  cpuSegmentClass(m: LoadedModel): string {
    return ui.fill[m.placement === 'split' ? 'critical' : 'warning'];   // split = crash risk, cpu = only slow
  }

  sourceLabel(key: string): string {
    return fmt.SOURCE_LABELS[key] ?? key;
  }

  throttleLabel(code: string): string {
    return fmt.THROTTLE_LABELS[code] ?? code;
  }

  private warnKey(code: string, subject: string | null | undefined): string {
    return code === 'disk_low' ? `${code}:${subject ?? ''}` : code;
  }

  private message(e: unknown): string {
    if (e instanceof HttpErrorResponse) {
      if (e.status === 503) return 'Das Dashboard-Modul läuft nicht (Details im Health-Status bzw. Log).';
      const detail = (e.error as { detail?: unknown } | null)?.detail;
      return typeof detail === 'string' ? detail : `${e.status} ${e.statusText}`;
    }
    return e instanceof Error ? e.message : String(e);
  }
}
