import { Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';

import { Api } from '../api/api';
import { catalogOverview } from '../api/fn/catalog/catalog-overview';
import { catalogRefresh } from '../api/fn/catalog/catalog-refresh';
import { CatalogOverview } from '../api/models/catalog-overview';
import { StreamService } from '../core/stream.service';
import * as fmt from '../dashboard/format';
import { Icon } from '../layout/icon';
import { Dialog } from '../ui/dialog';
import { ui } from '../ui/tokens';
import { ModelDetails } from './model-details';
import { ModelRow } from './model-row';
import {
  OVERVIEW_TOPIC, gib, groupViews, isNewerOverview, message, serverFacts, serverSourceText, when,
} from './state';

const ALL = '*';                      // busy marker of "refresh everything"

/**
 * Catalog page: installed models grouped by origin, effective context, expected VRAM and verdict.
 * The overview comes via GET and, after every change on the server, complete via SSE (catalog.overview).
 */
@Component({
  selector: 'app-catalog',
  imports: [Dialog, Icon, ModelDetails, ModelRow],
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

  readonly groups = computed(() => {
    const ov = this.overview();
    return ov ? groupViews(ov) : [];
  });
  readonly facts = computed(() => {
    const ov = this.overview();
    return ov ? serverFacts(ov.server) : [];
  });
  readonly serverSource = computed(() => {
    const ov = this.overview();
    return ov ? serverSourceText(ov.server) : '';
  });
  readonly detail = computed(() => this.overview()?.models.find((m) => m.name === this.detailName()) ?? null);
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
    inject(DestroyRef).onDestroy(off);
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
    if (isNewerOverview(ov, this.overview())) this.overview.set(ov);
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
