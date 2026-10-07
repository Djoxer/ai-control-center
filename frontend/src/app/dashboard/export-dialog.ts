import { Component, DestroyRef, computed, effect, inject, input, output, signal, untracked } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';

import { Api } from '../api/api';
import { DashboardExport$Params, dashboardExport } from '../api/fn/dashboard/dashboard-export';
import { ExportDocument } from '../api/models/export-document';
import { ClipboardService } from '../core/clipboard.service';
import { saveText } from '../core/save-file';
import { Icon } from '../layout/icon';
import { Dialog } from '../ui/dialog';
import { ui } from '../ui/tokens';
import * as fmt from './format';
import { HistoryRange, RANGES } from './history-panel';

export type ExportPart = 'snapshot' | 'events' | 'history';

export const PARTS: { key: ExportPart; label: string }[] = [
  { key: 'snapshot', label: 'Zustand' },
  { key: 'events', label: 'Ereignisse' },
  { key: 'history', label: 'Verlauf' },
];

/**
 * "Export für AI-Chats": choose what goes in, see the text, copy or save it.
 *
 * The text comes from GET /api/v1/dashboard/export - the same endpoint curl or OpenCode can call
 * (/export/raw without the envelope). Every option change asks again; answers of outdated requests
 * are dropped, so fast clicking never shows an older preview over a newer one.
 */
@Component({
  selector: 'app-export-dialog',
  imports: [Dialog, Icon],
  templateUrl: './export-dialog.html',
})
export class ExportDialog {
  readonly open = input.required<boolean>();
  readonly dismiss = output<void>();

  protected readonly ui = ui;
  protected readonly parts = PARTS;
  protected readonly ranges = RANGES;
  private readonly api = inject(Api);
  private readonly clipboard = inject(ClipboardService);

  readonly chosen = signal<ReadonlySet<ExportPart>>(new Set(PARTS.map((p) => p.key)));
  readonly format = signal<'md' | 'json'>('md');
  readonly detail = signal<'short' | 'full'>('short');
  readonly range = signal<HistoryRange>('1h');
  readonly anonymize = signal(true);

  readonly doc = signal<ExportDocument | null>(null);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly notice = signal<{ ok: boolean; text: string } | null>(null);

  readonly params = computed<DashboardExport$Params>(() => ({
    format: this.format(),
    detail: this.detail(),
    parts: PARTS.map((p) => p.key).filter((k) => this.chosen().has(k)),
    range: this.range(),
    anonymize: this.anonymize(),
  }));
  readonly nothingChosen = computed(() => this.chosen().size === 0);
  /** "≈ 1.234 Tokens · 4,9 kB" - kB as UTF-8 bytes, what the file will have. */
  readonly sizeLabel = computed(() => {
    const d = this.doc();
    if (!d) return '';
    const kb = new TextEncoder().encode(d.content).length / 1000;
    return `≈ ${fmt.num(d.tokens)} Tokens · ${fmt.num(kb, 1)} kB`;
  });

  private seq = 0;
  private noticeTimer: ReturnType<typeof setTimeout> | undefined;

  constructor() {
    // open + any option -> new preview; closed -> nothing is fetched
    effect(() => {
      const params = this.params();
      if (!this.open() || this.nothingChosen()) return;
      untracked(() => void this.load(params));
    });
    inject(DestroyRef).onDestroy(() => clearTimeout(this.noticeTimer));
  }

  togglePart(key: ExportPart): void {
    this.chosen.update((set) => {
      const next = new Set(set);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  }

  async copy(): Promise<void> {
    const d = this.doc();
    if (!d) return;
    const ok = await this.clipboard.copy(d.content);
    this.say(ok, ok ? 'In die Zwischenablage kopiert' : 'Kopieren nicht möglich – bitte „Speichern“ nehmen');
  }

  save(): void {
    const d = this.doc();
    if (!d) return;
    saveText(d.content, d.filename, d.mediaType);
    this.say(true, `Gespeichert: ${d.filename}`);
  }

  private async load(params: DashboardExport$Params): Promise<void> {
    const seq = ++this.seq;
    this.loading.set(true);
    this.error.set(null);
    try {
      const doc = await this.api.invoke(dashboardExport, params);
      if (seq !== this.seq) return;                       // a newer request is on its way
      this.doc.set(doc);
    } catch (e) {
      if (seq !== this.seq) return;
      this.doc.set(null);
      this.error.set(this.message(e));
    } finally {
      if (seq === this.seq) this.loading.set(false);
    }
  }

  private say(ok: boolean, text: string): void {
    this.notice.set({ ok, text });
    clearTimeout(this.noticeTimer);
    this.noticeTimer = setTimeout(() => this.notice.set(null), 2500);
  }

  private message(e: unknown): string {
    if (e instanceof HttpErrorResponse) {
      if (e.status === 0) return 'Backend nicht erreichbar';
      const detail = (e.error as { detail?: unknown } | null)?.detail;
      return typeof detail === 'string' ? detail : `HTTP ${e.status}`;
    }
    return e instanceof Error ? e.message : String(e);
  }
}
