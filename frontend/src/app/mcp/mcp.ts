import { Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';

import { Api } from '../api/api';
import { mcpRestart } from '../api/fn/mcp/mcp-restart';
import { mcpServers } from '../api/fn/mcp/mcp-servers';
import { mcpStart } from '../api/fn/mcp/mcp-start';
import { mcpStop } from '../api/fn/mcp/mcp-stop';
import { mcpTools } from '../api/fn/mcp/mcp-tools';
import { McpServerStatus } from '../api/models/mcp-server-status';
import { StreamService } from '../core/stream.service';
import { Icon } from '../layout/icon';
import { Dialog } from '../ui/dialog';
import { ui } from '../ui/tokens';
import { McpAction, ServerCard } from './server-card';
import { isNewer } from './state';

export const SERVER_TOPIC = 'mcp.server';

/** Stop and restart interrupt every client using the server - they ask first. */
interface Confirm {
  key: string;
  action: 'stop' | 'restart';
  title: string;
}

const ENDPOINTS = { start: mcpStart, stop: mcpStop, restart: mcpRestart, reset: mcpStop } as const;

/**
 * MCP-Server page: every server from [modules.mcp] as a card. The list comes once via GET,
 * every change afterwards via SSE (topic mcp.server, full status per server).
 */
@Component({
  selector: 'app-mcp',
  imports: [ServerCard, Dialog, Icon],
  templateUrl: './mcp.html',
})
export class Mcp implements OnInit {
  readonly ui = ui;
  private readonly api = inject(Api);
  private readonly stream = inject(StreamService);

  readonly servers = signal<McpServerStatus[]>([]);
  readonly loaded = signal(false);
  readonly error = signal<string | null>(null);
  readonly busy = signal<ReadonlyMap<string, McpAction>>(new Map());
  readonly actionErrors = signal<ReadonlyMap<string, string>>(new Map());
  readonly toolsLoading = signal<ReadonlySet<string>>(new Set());
  readonly confirm = signal<Confirm | null>(null);
  readonly streamState = this.stream.state;
  /** Shown when [modules.mcp] has no servers yet. Placeholders in <…>, no real path in the repo. */
  readonly example = [
    '[[modules.mcp.servers]]',
    'key = "bent-rag"',
    'title = "Bent/TYPO3-RAG"',
    'command = ["<RAG-ORDNER>/.venv/Scripts/python.exe", "mcp_server.py"]',
    'cwd = "<RAG-ORDNER>"',
    'url = "http://127.0.0.1:8000/mcp"',
    'autostart = true',
  ].join('\n');

  readonly now = signal(Date.now());                 // ticks every second for the uptime
  private readonly skewMs = signal(0);               // browser clock minus server clock
  readonly serverNow = computed(() => this.now() - this.skewMs());

  constructor() {
    const off = this.stream.on<McpServerStatus>(SERVER_TOPIC, (s) => this.accept(s));
    const timer = setInterval(() => this.now.set(Date.now()), 1000);
    inject(DestroyRef).onDestroy(() => {
      off();
      clearInterval(timer);
    });
  }

  async ngOnInit(): Promise<void> {
    try {
      const list = await this.api.invoke(mcpServers);
      // SSE updates that won the race are newer than this list: keep them
      const live = new Map(this.servers().map((s) => [s.key, s]));
      this.servers.set(list.map((s) => (isNewer(s, live.get(s.key)) ? s : live.get(s.key)!)));
      if (list.length) this.measureSkew(list[0]);
    } catch (e) {
      this.error.set(this.message(e));
    } finally {
      this.loaded.set(true);
    }
  }

  /** One status from SSE or an HTTP answer: replace the card if it is not older than what we show. */
  accept(s: McpServerStatus): void {
    this.measureSkew(s);
    this.servers.update((list) => {
      const i = list.findIndex((x) => x.key === s.key);
      if (i < 0) return this.loaded() ? [...list, s] : list;   // before the first GET the list decides the order
      if (!isNewer(s, list[i])) return list;
      const next = [...list];
      next[i] = s;
      return next;
    });
  }

  // ---- actions ------------------------------------------------------------------------------

  onAction(s: McpServerStatus, action: McpAction): void {
    if (action === 'stop' || action === 'restart') {
      this.confirm.set({ key: s.key, action, title: s.title });
      return;
    }
    void this.run(s.key, action);
  }

  confirmed(): void {
    const c = this.confirm();
    this.confirm.set(null);
    if (c) void this.run(c.key, c.action);
  }

  async run(key: string, action: McpAction): Promise<void> {
    if (this.busy().has(key)) return;
    this.busy.update((m) => new Map(m).set(key, action));
    this.setError(key, null);
    try {
      this.accept(await this.api.invoke(ENDPOINTS[action], { key }));
    } catch (e) {
      this.setError(key, this.message(e));
    } finally {
      this.busy.update((m) => {
        const next = new Map(m);
        next.delete(key);
        return next;
      });
    }
  }

  async fetchTools(key: string): Promise<void> {
    if (this.toolsLoading().has(key)) return;
    this.toolsLoading.update((set) => new Set(set).add(key));
    try {
      const tools = await this.api.invoke(mcpTools, { key });
      // the backend also publishes the new status via SSE; this makes the answer visible without it
      this.servers.update((list) => list.map((s) => (s.key === key ? { ...s, tools } : s)));
    } catch (e) {
      this.setError(key, this.message(e));
    } finally {
      this.toolsLoading.update((set) => {
        const next = new Set(set);
        next.delete(key);
        return next;
      });
    }
  }

  // ---- helpers ------------------------------------------------------------------------------

  private setError(key: string, text: string | null): void {
    this.actionErrors.update((m) => {
      const next = new Map(m);
      if (text === null) next.delete(key);
      else next.set(key, text);
      return next;
    });
  }

  private measureSkew(s: McpServerStatus): void {
    const server = Date.parse(s.asOf);
    if (!Number.isNaN(server)) this.skewMs.set(Date.now() - server);
  }

  private message(e: unknown): string {
    if (e instanceof HttpErrorResponse) {
      if (e.status === 0) return 'Backend nicht erreichbar';
      if (e.status === 503) return 'Das MCP-Modul läuft nicht (Details unter ⋮ → Über und im Protokoll).';
      const detail = (e.error as { detail?: unknown } | null)?.detail;
      return typeof detail === 'string' ? detail : `HTTP ${e.status}`;
    }
    return e instanceof Error ? e.message : String(e);
  }
}
