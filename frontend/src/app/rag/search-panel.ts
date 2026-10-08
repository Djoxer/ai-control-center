import { Component, DestroyRef, computed, effect, inject, input, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';

import { Api } from '../api/api';
import { ragSearch } from '../api/fn/rag/rag-search';
import { SearchResult } from '../api/models/search-result';
import { ClipboardService } from '../core/clipboard.service';
import * as fmt from '../dashboard/format';
import { Icon } from '../layout/icon';
import { ui } from '../ui/tokens';
import { mcpText, score } from './state';

export const LIMIT_MAX = 50;          // backend default of [modules.rag] search_limit_max

/**
 * Test search: one question against one collection, embedded exactly like the MCP tools do.
 * Owns its request - nothing else on the page depends on the result.
 */
@Component({
  selector: 'app-rag-search',
  imports: [Icon],
  host: { class: 'block' },
  templateUrl: './search-panel.html',
})
export class SearchPanel {
  readonly collections = input.required<string[]>();
  readonly preferred = input<string | null>(null);     // first managed collection
  readonly model = input('');
  readonly available = input(true);                     // Qdrant reachable

  protected readonly ui = ui;
  protected readonly fmt = fmt;
  protected readonly score = score;
  private readonly api = inject(Api);
  private readonly clipboard = inject(ClipboardService);

  readonly collection = signal('');
  private readonly picked = signal(false);              // the user chose a collection: stop following "preferred"
  readonly query = signal('');
  readonly limit = signal(8);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly result = signal<SearchResult | null>(null);
  readonly copied = signal(false);
  readonly canSearch = computed(() =>
    this.available() && !this.busy() && this.collection() !== '' && this.query().trim() !== '');
  readonly limitMax = LIMIT_MAX;

  private copiedTimer: ReturnType<typeof setTimeout> | undefined;

  constructor() {
    // Keep the selection valid when the list changes (first load, deleted collection). Until the user picks
    // one, follow the preferred (managed) collection - it may only exist after the first reindex.
    effect(() => {
      const list = this.collections();
      const preferred = this.preferred();
      const current = this.collection();
      if (!this.picked() && preferred && list.includes(preferred)) {
        if (current !== preferred) this.collection.set(preferred);
      } else if (!list.includes(current)) {
        this.collection.set(list[0] ?? '');
      }
    });
    inject(DestroyRef).onDestroy(() => clearTimeout(this.copiedTimer));
  }

  pick(name: string): void {
    this.picked.set(true);
    this.collection.set(name);
  }

  setLimit(raw: string): void {
    const n = Math.round(Number(raw));
    this.limit.set(Number.isFinite(n) ? Math.min(LIMIT_MAX, Math.max(1, n)) : 8);
  }

  async run(): Promise<void> {
    if (!this.canSearch()) return;
    this.busy.set(true);
    this.error.set(null);
    try {
      this.result.set(await this.api.invoke(ragSearch, {
        body: { collection: this.collection(), query: this.query().trim(), limit: this.limit() },
      }));
    } catch (e) {
      this.result.set(null);
      this.error.set(message(e));
    } finally {
      this.busy.set(false);
    }
  }

  async copyMcp(): Promise<void> {
    const r = this.result();
    if (!r || !(await this.clipboard.copy(mcpText(r.hits)))) return;
    this.copied.set(true);
    clearTimeout(this.copiedTimer);
    this.copiedTimer = setTimeout(() => this.copied.set(false), 2000);
  }
}

export function message(e: unknown): string {
  if (e instanceof HttpErrorResponse) {
    if (e.status === 0) return 'Backend nicht erreichbar';
    if (e.status === 503 && (e.error as { detail?: unknown } | null)?.detail === 'RAG-Modul läuft nicht') {
      return 'Das RAG-Modul läuft nicht (Details unter ⋮ → Über und im Protokoll).';
    }
    const detail = (e.error as { detail?: unknown } | null)?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') return detail[0].msg;   // 422 validation
    return `HTTP ${e.status}`;
  }
  return e instanceof Error ? e.message : String(e);
}
