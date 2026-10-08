import { Component, computed, effect, inject, input, output, signal, untracked } from '@angular/core';

import { Api } from '../api/api';
import { catalogPreflight } from '../api/fn/catalog/catalog-preflight';
import { BenchStatus } from '../api/models/bench-status';
import { CatalogModel } from '../api/models/catalog-model';
import { Preflight } from '../api/models/preflight';
import * as fmt from '../dashboard/format';
import { Meter } from '../dashboard/meter';
import { Icon } from '../layout/icon';
import { ui } from '../ui/tokens';
import {
  PHASE_ORDER, benchStateView, extraText, gib, isBenchRunning, message, phaseLabel, verdictView, vramPercent,
} from './state';

export interface BenchStart {
  numCtx: number;
  confirm: boolean;
}

/**
 * Content of the test-run dialog. Two faces:
 * - setup: choose a context, see what it would cost (preflight from the backend, refreshed on every change),
 *   start - refused for "Teil-Offload", only with a tick for "knapp";
 * - run: the phases of the running test, then the measured result.
 * The page owns the run (start request, SSE catalog.bench) and passes its status in.
 */
@Component({
  selector: 'app-catalog-bench-panel',
  imports: [Icon, Meter],
  host: { class: 'block' },
  templateUrl: './bench-panel.html',
})
export class BenchPanel {
  readonly model = input.required<CatalogModel>();
  readonly bench = input<BenchStatus | null>(null);     // the run shown in this dialog (running or just finished)
  readonly locked = input<string | null>(null);         // why no run may start right now
  readonly starting = input(false);
  readonly error = input<string | null>(null);          // the start request failed
  readonly start = output<BenchStart>();
  readonly again = output<void>();                      // back to the setup face

  private readonly api = inject(Api);
  protected readonly ui = ui;
  protected readonly fmt = fmt;
  protected readonly gib = gib;
  protected readonly phases = PHASE_ORDER;
  protected readonly phaseLabel = phaseLabel;

  readonly ctx = signal<number | null>(null);           // null = the model's effective context
  readonly preflight = signal<Preflight | null>(null);
  readonly checking = signal(false);
  readonly checkError = signal<string | null>(null);
  readonly confirmed = signal(false);
  private request = 0;                                  // answers can cross: only the newest counts

  // computed, not model() itself: a new overview (same model, new object) must not re-check and reset the tick
  private readonly name = computed(() => this.model().name);
  private readonly setup = computed(() => this.bench() === null);

  constructor() {
    // check on open, on every context change and when coming back from a run (it calibrated the estimate)
    effect(() => {
      const name = this.name();
      const ctx = this.ctx();
      if (this.setup()) untracked(() => void this.check(name, ctx));
    });
  }

  async check(name: string, ctx: number | null): Promise<void> {
    const id = ++this.request;
    this.checking.set(true);
    this.confirmed.set(false);
    try {
      const p = await this.api.invoke(catalogPreflight, { name, num_ctx: ctx ?? undefined });
      if (id !== this.request) return;
      this.preflight.set(p);
      this.checkError.set(null);
    } catch (e) {
      if (id === this.request) this.checkError.set(message(e));
    } finally {
      if (id === this.request) this.checking.set(false);
    }
  }

  readonly running = computed(() => isBenchRunning(this.bench()));
  readonly view = computed(() => {
    const p = this.preflight();
    return p ? verdictView(p.verdict) : null;
  });
  readonly pill = computed(() => {
    const v = this.view();
    return v ? `${ui.pill} ${ui.pillTone[v.tone]}` : ui.pill;
  });
  readonly extra = computed(() => {
    const p = this.preflight();
    return p ? extraText(p.verdict) : '';
  });
  readonly percent = computed(() => {
    const p = this.preflight();
    return p ? vramPercent(p.verdict) : null;
  });
  /** Context options: the backend's suggestions, the effective one marked. */
  readonly options = computed(() => this.preflight()?.suggestions ?? []);
  readonly selected = computed(() => this.ctx() ?? this.model().context.effective);
  readonly blocked = computed<string | null>(() => {
    const p = this.preflight();
    if (this.locked()) return this.locked();
    if (!p || this.checking()) return 'Prüfe …';
    if (!p.allowed) return p.reason ?? 'Nicht möglich';
    if (p.needsConfirm && !this.confirmed()) return 'Bitte bestätigen';
    return null;
  });
  readonly stateView = computed(() => {
    const b = this.bench();
    return b ? benchStateView(b) : null;
  });
  readonly statePill = computed(() => {
    const v = this.stateView();
    return v ? `${ui.pill} ${ui.pillTone[v.tone]}` : ui.pill;
  });
  readonly lockCallout = `${ui.calloutFrame} ${ui.callout.info}`;
  readonly warnCallout = `${ui.calloutFrame} ${ui.callout.warning}`;
  readonly critCallout = `${ui.calloutFrame} ${ui.callout.critical}`;
  readonly measured = ui.origin.measured;
  readonly estimated = ui.origin.estimated;

  phaseState(p: string): 'done' | 'now' | 'todo' {
    const b = this.bench();
    if (!b) return 'todo';
    if (!this.running()) return b.state === 'done' ? 'done' : 'todo';
    const now = b.phase ? PHASE_ORDER.indexOf(b.phase) : -1;
    const i = PHASE_ORDER.indexOf(p as (typeof PHASE_ORDER)[number]);
    return i < now ? 'done' : i === now ? 'now' : 'todo';
  }

  choose(value: string): void {
    const n = Number(value);
    this.ctx.set(Number.isFinite(n) && n !== this.model().context.effective ? n : null);
  }

  go(): void {
    if (this.blocked() !== null) return;
    this.start.emit({ numCtx: this.selected(), confirm: this.confirmed() });
  }
}
