import { Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';

import { Api } from '../api/api';
import { catalogCheckCandidate } from '../api/fn/catalog/catalog-check-candidate';
import { catalogForgetCandidate } from '../api/fn/catalog/catalog-forget-candidate';
import { catalogOverview } from '../api/fn/catalog/catalog-overview';
import { catalogRefresh } from '../api/fn/catalog/catalog-refresh';
import { catalogSetUsage } from '../api/fn/catalog/catalog-set-usage';
import { catalogStartBench } from '../api/fn/catalog/catalog-start-bench';
import { BenchStatus } from '../api/models/bench-status';
import { CatalogOverview } from '../api/models/catalog-overview';
import { StreamService } from '../core/stream.service';
import * as fmt from '../dashboard/format';
import { Icon } from '../layout/icon';
import { Dialog } from '../ui/dialog';
import { ui } from '../ui/tokens';
import { BenchPanel, BenchStart } from './bench-panel';
import { Candidates } from './candidates';
import { ModelDetails } from './model-details';
import { ModelRow } from './model-row';
import {
  BENCH_TOPIC, OVERVIEW_TOPIC, UsageFilter, budgetFacts, gib, groupViews, inUse, isBenchRunning, isNewerBench,
  isNewerOverview, matchesUsage, message, serverFacts, serverSourceText, when,
} from './state';
import { UsageChange, UsagePanel } from './usage-panel';

const ALL = '*';                      // busy marker of "refresh everything"

export type CatalogView = 'installed' | 'candidates';

/**
 * Catalog page: installed models grouped by origin, effective context, VRAM need against the card's budget,
 * verdict, test runs. The overview comes via GET and, after every change on the server, complete via SSE
 * (catalog.overview); a running test run reports each phase via SSE (catalog.bench). Second view: candidates,
 * models of Ollama's library checked before a pull (part of the same overview).
 */
@Component({
  selector: 'app-catalog',
  imports: [BenchPanel, Candidates, Dialog, Icon, ModelDetails, ModelRow, UsagePanel],
  templateUrl: './catalog.html',
})
export class Catalog implements OnInit {
  readonly ui = ui;
  readonly fmt = fmt;
  readonly gib = gib;
  readonly when = when;
  private readonly api = inject(Api);
  private readonly stream = inject(StreamService);

  readonly overview = signal<CatalogOverview | null>(null);
  readonly loaded = signal(false);
  readonly error = signal<string | null>(null);
  readonly actionError = signal<string | null>(null);
  readonly busy = signal<string | null>(null);          // model name, ALL, or null
  readonly detailName = signal<string | null>(null);
  readonly streamState = this.stream.state;
  // test runs
  readonly bench = signal<BenchStatus | null>(null);   // newest status seen (any model)
  readonly benchName = signal<string | null>(null);    // test dialog open for this model
  readonly shownRun = signal<string | null>(null);     // id of the run the dialog shows
  readonly benchStarting = signal(false);
  readonly benchError = signal<string | null>(null);
  // usage tags
  readonly filter = signal<UsageFilter>('all');
  readonly usageName = signal<string | null>(null);
  readonly usageSaving = signal(false);
  readonly usageError = signal<string | null>(null);
  // installed models or candidates
  readonly view = signal<CatalogView>('installed');
  readonly checking = signal<string | null>(null);     // candidate name being checked (as typed / of the card)
  readonly candidateError = signal<string | null>(null);
  readonly views = computed(() => [
    { key: 'installed' as CatalogView, label: 'Installiert', count: this.overview()?.models.length ?? 0 },
    { key: 'candidates' as CatalogView, label: 'Kandidaten', count: this.overview()?.candidates?.length ?? 0 },
  ]);

  /** Origin cards with the models the filter lets through; cards that end up empty are left out. */
  readonly groups = computed(() => {
    const ov = this.overview();
    const f = this.filter();
    return (ov ? groupViews(ov) : [])
      .map((g) => ({ ...g, models: g.models.filter((m) => matchesUsage(m, f)) }))
      .filter((g) => g.models.length > 0);
  });
  readonly usedCount = computed(() => this.overview()?.models.filter((m) => inUse(m.usage)).length ?? 0);
  readonly filters = computed(() => {
    const all = this.overview()?.models.length ?? 0;
    const used = this.usedCount();
    return [
      { key: 'all' as UsageFilter, label: `Alle (${all})` },
      { key: 'used' as UsageFilter, label: `Im Einsatz (${used})` },
      { key: 'unused' as UsageFilter, label: `Ohne Einsatz (${all - used})` },
    ];
  });
  readonly usageModel = computed(() => this.overview()?.models.find((m) => m.name === this.usageName()) ?? null);
  readonly facts = computed(() => {
    const ov = this.overview();
    return ov ? serverFacts(ov.server) : [];
  });
  readonly serverSource = computed(() => {
    const ov = this.overview();
    return ov ? serverSourceText(ov.server) : '';
  });
  readonly detail = computed(() => this.overview()?.models.find((m) => m.name === this.detailName()) ?? null);
  readonly budget = computed(() => {
    const ov = this.overview();
    return ov ? budgetFacts(ov.budget) : [];
  });
  readonly benchModel = computed(() => this.overview()?.models.find((m) => m.name === this.benchName()) ?? null);
  /** The run the dialog shows: the one started there, or a run of that model that is going on right now. */
  readonly panelRun = computed(() => {
    const b = this.bench();
    if (!b || b.name !== this.benchName()) return null;
    return b.id === this.shownRun() || isBenchRunning(b) ? b : null;
  });
  /** The running test run (SSE reports every phase; the overview only start and end). */
  readonly liveBench = computed(() => {
    const b = this.bench();
    return isBenchRunning(b) ? b : null;
  });
  /** Why no test run may start right now (rows and dialog), or null. */
  readonly testLocked = computed<string | null>(() => {
    const ov = this.overview();
    if (!ov) return 'Lade …';
    if (!ov.tests.allowed) return ov.tests.reason ?? 'Testläufe gesperrt';
    if (!ov.ollama.online) return 'Ollama ist nicht erreichbar';
    if (isBenchRunning(this.bench()) || isBenchRunning(ov.bench)) return 'Es läuft schon ein Testlauf';
    return null;
  });
  readonly loadedCount = computed(() => this.overview()?.models.filter((m) => m.loaded).length ?? 0);
  /** Why reading from Ollama is not possible right now, or null. */
  readonly locked = computed<string | null>(() => {
    const ov = this.overview();
    if (!ov) return 'Lade …';
    if (this.busy() !== null) return 'Liest gerade ein …';
    return null;
  });
  readonly downCallout = `${ui.calloutFrame} ${ui.callout.critical}`;
  readonly infoCallout = `${ui.calloutFrame} ${ui.callout.info}`;
  readonly measured = ui.origin.measured;
  readonly estimated = ui.origin.estimated;
  /** Shown when Ollama's defaults are unknown: where to set them. Placeholder values, nothing from the box. */
  readonly example = [
    '[modules.catalog]',
    'server_context_length = 65536   # OLLAMA_CONTEXT_LENGTH',
    'kv_cache_type = "q8_0"          # OLLAMA_KV_CACHE_TYPE',
    'flash_attention = true          # OLLAMA_FLASH_ATTENTION',
  ].join('\n');

  constructor() {
    const off = this.stream.on<CatalogOverview>(OVERVIEW_TOPIC, (ov) => this.accept(ov));
    const offBench = this.stream.on<BenchStatus>(BENCH_TOPIC, (b) => this.acceptBench(b));
    inject(DestroyRef).onDestroy(() => {
      off();
      offBench();
    });
  }

  async ngOnInit(): Promise<void> {
    await this.load();
    this.loaded.set(true);
  }

  async load(): Promise<void> {
    try {
      this.accept(await this.api.invoke(catalogOverview));
      this.error.set(null);
    } catch (e) {
      this.error.set(message(e));
    }
  }

  accept(ov: CatalogOverview): void {
    if (!isNewerOverview(ov, this.overview())) return;
    this.overview.set(ov);
    if (ov.bench) this.acceptBench(ov.bench);
  }

  acceptBench(b: BenchStatus): void {
    if (isNewerBench(b, this.bench())) this.bench.set(b);
  }

  // ---- test runs ----------------------------------------------------------------------------

  openBench(name: string): void {
    this.benchError.set(null);
    this.shownRun.set(null);
    this.benchName.set(name);
  }

  closeBench(): void {
    this.benchName.set(null);
    this.shownRun.set(null);
  }

  async startBench(s: BenchStart): Promise<void> {
    const name = this.benchName();
    if (!name || this.benchStarting()) return;
    this.benchStarting.set(true);
    this.benchError.set(null);
    try {
      const st = await this.api.invoke(catalogStartBench, { body: { name, numCtx: s.numCtx, confirm: s.confirm } });
      this.acceptBench(st);
      this.shownRun.set(st.id);
    } catch (e) {
      this.benchError.set(message(e));
    } finally {
      this.benchStarting.set(false);
    }
  }

  // ---- usage ------------------------------------------------------------------------------------

  openUsage(name: string): void {
    this.usageError.set(null);
    this.usageName.set(name);
  }

  async saveUsage(change: UsageChange): Promise<void> {
    const name = this.usageName();
    if (!name || this.usageSaving()) return;
    this.usageSaving.set(true);
    this.usageError.set(null);
    try {
      this.accept(await this.api.invoke(catalogSetUsage, { body: { name, tags: change.tags, note: change.note } }));
      this.usageName.set(null);
    } catch (e) {
      this.usageError.set(message(e));
    } finally {
      this.usageSaving.set(false);
    }
  }

  // ---- candidates ---------------------------------------------------------------------------------

  /** Asks the registry (manifest, config, GGUF header) - nothing is pulled. The new candidate comes first. */
  async checkCandidate(name: string): Promise<void> {
    if (this.checking() !== null) return;
    this.checking.set(name);
    this.candidateError.set(null);
    try {
      const r = await this.api.invoke(catalogCheckCandidate, { body: { name } });
      this.accept(r.overview);
    } catch (e) {
      this.candidateError.set(message(e));
    } finally {
      this.checking.set(null);
    }
  }

  async forgetCandidate(name: string): Promise<void> {
    this.candidateError.set(null);
    try {
      this.accept(await this.api.invoke(catalogForgetCandidate, { name }));
    } catch (e) {
      this.candidateError.set(message(e));
    }
  }

  /** Read /api/tags and /api/show again - all models (name null) or one. Loads nothing into the GPU. */
  async refresh(name: string | null): Promise<void> {
    if (this.busy() !== null) return;
    this.busy.set(name ?? ALL);
    this.actionError.set(null);
    try {
      this.accept(await this.api.invoke(catalogRefresh, { body: { name } }));
    } catch (e) {
      this.actionError.set(message(e));
    } finally {
      this.busy.set(null);
    }
  }

  isBusy(name: string): boolean {
    return this.busy() === name || this.busy() === ALL;
  }

  /** Tree level inside the card: members of a missing origin start one level down. */
  indent(depth: number, installedOrigin: boolean): number {
    return Math.max(0, depth - (installedOrigin ? 0 : 1));
  }
}
