import { Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';

import { Api } from '../api/api';
import { logsEntries } from '../api/fn/logs/logs-entries';
import { logsSources } from '../api/fn/logs/logs-sources';
import { LogEntry } from '../api/models/log-entry';
import { LogSourceInfo } from '../api/models/log-source-info';
import { StreamService } from '../core/stream.service';
import { ui } from '../ui/tokens';
import { LEVELS, Level, LogFilter, matchesFilter } from './log-filter';

export const PAGE_SIZE = 200;
export const MAX_ENTRIES = 2000;          // live lines push old ones out; "load older" is the way back
const ACCESS_LOGGER = 'uvicorn.access';    // every API call (including this page's) writes one
const TEXT_DEBOUNCE_MS = 300;

@Component({
  selector: 'app-logs',
  imports: [],
  templateUrl: './logs.html',
})
export class Logs implements OnInit {
  readonly ui = ui;                                  // shared class strings (ui/tokens.ts)
  private readonly api = inject(Api);
  private readonly stream = inject(StreamService);
  private readonly destroyRef = inject(DestroyRef);

  readonly levels = LEVELS;
  readonly sources = signal<LogSourceInfo[]>([]);
  readonly source = signal<string | null>(null);
  readonly level = signal<Level | null>(null);
  readonly logger = signal('');
  readonly query = signal('');
  readonly hideAccess = signal(true);
  readonly live = signal(true);

  readonly entries = signal<LogEntry[]>([]);
  readonly nextCursor = signal<string | null>(null);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly expanded = signal<ReadonlySet<LogEntry>>(new Set());
  readonly streamState = this.stream.state;

  readonly filter = computed<LogFilter>(() => ({
    minLevel: this.level(),
    loggerPrefix: this.logger().trim(),
    exclude: this.hideAccess() ? [ACCESS_LOGGER] : [],
    query: this.query().trim(),
  }));
  readonly currentSource = computed(() => this.sources().find((s) => s.key === this.source()) ?? null);

  private requestSeq = 0;                       // responses of outdated requests are dropped
  private liveBuffer: LogEntry[] | null = null; // live lines that arrive while the first page loads
  private debounce: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    const off = this.stream.on<LogEntry>('logs', (entry, topic) => this.onLive(entry, topic));
    this.destroyRef.onDestroy(() => {
      off();
      if (this.debounce) clearTimeout(this.debounce);
    });
  }

  async ngOnInit(): Promise<void> {
    try {
      const sources = await this.api.invoke(logsSources);
      this.sources.set(sources);
      // prefer a source that has files; on the dev PC the Ollama log does not exist
      const first = sources.find((s) => s.available) ?? sources[0];
      if (first) this.selectSource(first.key);
    } catch (e) {
      this.error.set(this.message(e));
    }
  }

  // ---- user actions ------------------------------------------------------------------------

  selectSource(key: string): void {
    if (key === this.source()) return;
    this.source.set(key);
    void this.reload();
  }

  setLevel(value: string): void {
    this.level.set((value || null) as Level | null);
    void this.reload();
  }

  setHideAccess(value: boolean): void {
    this.hideAccess.set(value);
    void this.reload();
  }

  /** Text inputs: wait until typing pauses, otherwise every key press is a request. */
  setText(field: 'logger' | 'query', value: string): void {
    this[field].set(value);
    if (this.debounce) clearTimeout(this.debounce);
    this.debounce = setTimeout(() => {
      this.debounce = null;
      void this.reload();
    }, TEXT_DEBOUNCE_MS);
  }

  setLive(value: boolean): void {
    this.live.set(value);
    if (value) void this.reload();    // catch up on everything that happened while paused
  }

  toggle(entry: LogEntry): void {
    const next = new Set(this.expanded());
    if (!next.delete(entry)) next.add(entry);
    this.expanded.set(next);
  }

  // ---- loading -----------------------------------------------------------------------------

  async reload(): Promise<void> {
    const source = this.source();
    if (!source) return;
    const seq = ++this.requestSeq;
    this.loading.set(true);
    this.error.set(null);
    this.liveBuffer = [];
    try {
      const page = await this.api.invoke(logsEntries, this.params(source, null));
      if (seq !== this.requestSeq) return;            // a newer request is underway
      const buffered = (this.liveBuffer ?? []).filter((e) => matchesFilter(e, this.filter()));
      this.entries.set([...buffered.reverse(), ...page.entries].slice(0, MAX_ENTRIES));
      this.nextCursor.set(page.nextCursor ?? null);
      this.expanded.set(new Set());
    } catch (e) {
      if (seq === this.requestSeq) this.error.set(this.message(e));
    } finally {
      if (seq === this.requestSeq) {
        this.loading.set(false);
        this.liveBuffer = null;
      }
    }
  }

  async loadOlder(): Promise<void> {
    const source = this.source();
    const cursor = this.nextCursor();
    if (!source || !cursor || this.loading()) return;
    const seq = ++this.requestSeq;
    this.loading.set(true);
    try {
      const page = await this.api.invoke(logsEntries, this.params(source, cursor));
      if (seq !== this.requestSeq) return;
      this.entries.update((list) => [...list, ...page.entries]);
      this.nextCursor.set(page.nextCursor ?? null);
    } catch (e) {
      if (seq !== this.requestSeq) return;
      if (e instanceof HttpErrorResponse && e.status === 400) {
        // cursor points into a file that was rotated meanwhile -> start fresh from the newest page
        this.loading.set(false);
        await this.reload();
        this.error.set('Logdatei wurde inzwischen rotiert – Ansicht neu geladen.');
        return;
      }
      this.error.set(this.message(e));
    } finally {
      if (seq === this.requestSeq) this.loading.set(false);
    }
  }

  private params(source: string, cursor: string | null) {
    const f = this.filter();
    return {
      source,
      level: f.minLevel,
      logger: f.loggerPrefix || null,
      exclude: f.exclude,
      q: f.query || null,
      limit: PAGE_SIZE,
      cursor,
    };
  }

  // ---- live ---------------------------------------------------------------------------------

  private onLive(entry: LogEntry, topic: string): void {
    if (!this.live() || topic !== `logs.${this.source()}`) return;
    if (this.liveBuffer) {
      this.liveBuffer.push(entry);                    // first page still loading -> merge afterwards
      return;
    }
    if (!matchesFilter(entry, this.filter())) return;
    this.entries.update((list) => [entry, ...list].slice(0, MAX_ENTRIES));
    // the trimmed tail is still on disk; a fresh cursor would be needed to page past it
    if (this.entries().length >= MAX_ENTRIES) this.nextCursor.set(null);
  }

  // ---- view helpers -------------------------------------------------------------------------

  /** "06.10. 14:12:10.346" in the viewer's local time; Intl's de-DE output adds commas we don't want. */
  formatTime(ts: string | null | undefined): string {
    if (!ts) return '—';
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts;
    const p = (n: number, w = 2) => String(n).padStart(w, '0');
    return `${p(d.getDate())}.${p(d.getMonth() + 1)}. ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
      + `.${p(d.getMilliseconds(), 3)}`;
  }

  levelClass(level: string | null | undefined): string {
    switch (level) {
      case 'CRITICAL':
      case 'ERROR': return 'bg-red-500/10 text-red-400 ring-red-500/30';
      case 'WARNING': return 'bg-amber-400/10 text-amber-300 ring-amber-400/30';
      case 'INFO': return 'bg-sky-400/10 text-sky-300 ring-sky-400/30';
      case 'DEBUG': return 'bg-white/5 text-gray-400 ring-white/10';
      default: return 'bg-white/5 text-gray-500 ring-white/10';
    }
  }

  formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  totalSize(s: LogSourceInfo): number {
    return s.files.reduce((sum, f) => sum + f.sizeBytes, 0);
  }

  private message(e: unknown): string {
    if (e instanceof HttpErrorResponse) {
      const detail = (e.error as { detail?: unknown } | null)?.detail;
      return typeof detail === 'string' ? detail : `${e.status} ${e.statusText}`;
    }
    return e instanceof Error ? e.message : String(e);
  }
}
