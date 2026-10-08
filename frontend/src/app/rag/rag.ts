import { Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';

import { Api } from '../api/api';
import { ragCancelJob } from '../api/fn/rag/rag-cancel-job';
import { ragDeleteCollection } from '../api/fn/rag/rag-delete-collection';
import { ragOverview } from '../api/fn/rag/rag-overview';
import { ragStartJob } from '../api/fn/rag/rag-start-job';
import { CollectionInfo } from '../api/models/collection-info';
import { JobStatus } from '../api/models/job-status';
import { RagOverview } from '../api/models/rag-overview';
import { StreamService } from '../core/stream.service';
import * as fmt from '../dashboard/format';
import { Icon } from '../layout/icon';
import { Dialog } from '../ui/dialog';
import { Menu } from '../ui/menu';
import { ui } from '../ui/tokens';
import { JobPanel } from './job-panel';
import { SearchPanel, message } from './search-panel';
import { SourceCard } from './source-card';
import { collectionStatus, isNewerJob, isRunning, qdrantDashboardUrl } from './state';

export const JOB_TOPIC = 'rag.job';

/** Reindex asks first: it takes minutes and unloads the chat model in Ollama. */
interface ConfirmReindex {
  collections: string[] | null;     // null = all sources
  titles: string[];
}

/**
 * RAG page: sources from [modules.rag], the running or last reindex, all collections in the store,
 * test search. The overview comes via GET; job progress via SSE (topic rag.job, full JobStatus).
 */
@Component({
  selector: 'app-rag',
  imports: [Dialog, Icon, JobPanel, Menu, SearchPanel, SourceCard],
  templateUrl: './rag.html',
})
export class Rag implements OnInit {
  readonly ui = ui;
  readonly fmt = fmt;
  readonly collectionStatus = collectionStatus;
  private readonly api = inject(Api);
  private readonly stream = inject(StreamService);

  readonly overview = signal<RagOverview | null>(null);
  readonly job = signal<JobStatus | null>(null);
  readonly loaded = signal(false);
  readonly error = signal<string | null>(null);
  readonly actionError = signal<string | null>(null);
  readonly starting = signal(false);
  readonly cancelBusy = signal(false);
  readonly confirmReindex = signal<ConfirmReindex | null>(null);
  readonly confirmDelete = signal<CollectionInfo | null>(null);
  readonly deleteName = signal('');
  readonly deleteBusy = signal(false);
  readonly streamState = this.stream.state;

  readonly running = computed(() => isRunning(this.job()));
  readonly store = computed(() => this.overview()?.store ?? null);
  readonly sources = computed(() => this.overview()?.sources ?? []);
  readonly collections = computed(() => this.overview()?.collections ?? []);
  readonly collectionNames = computed(() => this.collections().map((c) => c.name));
  readonly preferredCollection = computed(() => this.sources().find((s) => s.stats)?.collection ?? null);
  /** Why reindex/delete are not possible right now, or null. */
  readonly blocked = computed<string | null>(() => {
    const ov = this.overview();
    if (!ov) return 'Lade …';
    if (!ov.writes.allowed) return ov.writes.reason ?? 'Schreibschutz';
    if (!ov.store.reachable) return 'Qdrant ist nicht erreichbar';
    if (this.running() || this.starting()) return 'Es läuft schon eine Indexierung';
    return null;
  });
  readonly dashboardUrl = computed(() => {
    const s = this.store();
    return s?.mode === 'qdrant' && s.reachable ? qdrantDashboardUrl(s.url, location.hostname) : null;
  });
  readonly lockCallout = `${ui.calloutFrame} ${ui.callout.info}`;
  readonly downCallout = `${ui.calloutFrame} ${ui.callout.critical}`;
  /** Shown when [modules.rag] has no sources yet. Placeholders in <…>, no real path in the repo. */
  readonly example = [
    '[[modules.rag.sources]]',
    'collection = "bent_php"',
    'title = "Bent PHP-Backend"',
    'path = "%USERPROFILE%/<RAG-ORDNER>/repos/bent/bent-php-api"',
    'includes = [',
    '  { dir = "src", ext = [".php"] },',
    '  { dir = "app", ext = [".php"] },',
    '  { dir = "db/migrations", ext = [".sql"] },',
    ']',
  ].join('\n');

  constructor() {
    const off = this.stream.on<JobStatus>(JOB_TOPIC, (j) => this.accept(j));
    inject(DestroyRef).onDestroy(off);
  }

  async ngOnInit(): Promise<void> {
    await this.load();
    this.loaded.set(true);
  }

  async load(): Promise<void> {
    try {
      const ov = await this.api.invoke(ragOverview);
      this.overview.set(ov);
      if (ov.job) this.accept(ov.job, false);
      this.error.set(null);
    } catch (e) {
      this.error.set(message(e));
    }
  }

  /** A job status from SSE or an HTTP answer. When a job ends, the overview gets fresh numbers. */
  accept(j: JobStatus, reloadOnEnd = true): void {
    const before = this.job();
    if (!isNewerJob(j, before)) return;
    this.job.set(j);
    const ended = before !== null && before.id === j.id && isRunning(before) && !isRunning(j);
    if (ended && reloadOnEnd) void this.load();
  }

  // ---- reindex ------------------------------------------------------------------------------

  askReindex(collection: string | null): void {
    const all = this.sources();
    const chosen = collection === null ? all : all.filter((s) => s.collection === collection);
    this.confirmReindex.set({ collections: collection === null ? null : [collection], titles: chosen.map((s) => s.title) });
  }

  async startReindex(): Promise<void> {
    const c = this.confirmReindex();
    this.confirmReindex.set(null);
    if (!c || this.starting()) return;
    this.starting.set(true);
    this.actionError.set(null);
    try {
      this.accept(await this.api.invoke(ragStartJob, { body: { collections: c.collections } }));
    } catch (e) {
      this.actionError.set(message(e));
    } finally {
      this.starting.set(false);
    }
  }

  async cancel(): Promise<void> {
    const j = this.job();
    if (!j || this.cancelBusy()) return;
    this.cancelBusy.set(true);
    try {
      this.accept(await this.api.invoke(ragCancelJob, { job_id: j.id }));
    } catch (e) {
      this.actionError.set(message(e));
    } finally {
      this.cancelBusy.set(false);
    }
  }

  // ---- delete -------------------------------------------------------------------------------

  askDelete(c: CollectionInfo): void {
    this.deleteName.set('');
    this.confirmDelete.set(c);
  }

  async deleteConfirmed(): Promise<void> {
    const c = this.confirmDelete();
    if (!c || this.deleteName() !== c.name || this.deleteBusy()) return;   // the button is disabled anyway
    this.deleteBusy.set(true);
    this.actionError.set(null);
    try {
      await this.api.invoke(ragDeleteCollection, { name: c.name, confirm: this.deleteName() });
      this.confirmDelete.set(null);
      await this.load();
    } catch (e) {
      this.confirmDelete.set(null);
      this.actionError.set(message(e));
    } finally {
      this.deleteBusy.set(false);
    }
  }

  isActive(collection: string): boolean {
    const j = this.job();
    return isRunning(j) && j!.current?.collection === collection;
  }
}
