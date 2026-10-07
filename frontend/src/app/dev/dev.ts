import { HttpClient } from '@angular/common/http';
import { Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';

import { ClipboardService } from '../core/clipboard.service';
import { saveText } from '../core/save-file';
import { ShellStore } from '../core/shell.store';
import { Icon } from '../layout/icon';
import { jumpTo } from '../ui/jump';
import { ui } from '../ui/tokens';
import { IconEntry, iconSnippet, parseIconSprite } from './icons';
import { buildMarkdown, estimateTokens } from './markdown';
import { StyleGuide } from './style-guide';

interface Notice {
  ok: boolean;
  text: string;
}

/** Developer page (ng serve only): AI export, style guide, icon gallery. */
@Component({
  selector: 'app-dev',
  imports: [Icon, StyleGuide],
  templateUrl: './dev.html',
})
export class Dev implements OnInit {
  private readonly http = inject(HttpClient);
  private readonly clipboard = inject(ClipboardService);
  private readonly shell = inject(ShellStore);
  private readonly destroyRef = inject(DestroyRef);

  readonly ui = ui;
  readonly jumpTo = jumpTo;                    // section links (see dev.html)
  readonly icons = signal<IconEntry[]>([]);
  readonly iconError = signal<string | null>(null);
  readonly filter = signal('');
  readonly notice = signal<Notice | null>(null);
  private noticeTimer: ReturnType<typeof setTimeout> | undefined;

  /** Filtered icons, grouped in sprite order. */
  readonly groups = computed(() => {
    const q = this.filter().trim().toLowerCase();
    const hits = q
      ? this.icons().filter((i) => i.name.includes(q) || i.group.toLowerCase().includes(q))
      : this.icons();
    const map = new Map<string, IconEntry[]>();
    for (const icon of hits) map.set(icon.group, [...(map.get(icon.group) ?? []), icon]);
    return [...map].map(([group, items]) => ({ group, items }));
  });
  readonly shown = computed(() => this.groups().reduce((n, g) => n + g.items.length, 0));

  /** Size of the export with the current icons - shown before copying, so nobody pastes 50k by surprise. */
  readonly exportTokens = computed(() => estimateTokens(this.markdown()));

  constructor() {
    this.destroyRef.onDestroy(() => clearTimeout(this.noticeTimer));
  }

  ngOnInit(): void {
    // same file the <app-icon> component points at - the gallery can never drift from the sprite
    this.http.get('/icons.svg', { responseType: 'text' }).subscribe({
      next: (text) => this.icons.set(parseIconSprite(text)),
      error: () => this.iconError.set('icons.svg konnte nicht geladen werden'),
    });
  }

  snippet(name: string): string {
    return iconSnippet(name);
  }

  markdown(): string {
    return buildMarkdown({
      icons: this.icons(),
      version: this.shell.health()?.version ?? null,
      generatedAt: new Date(),
    });
  }

  async copy(text: string, label = text): Promise<void> {
    const ok = await this.clipboard.copy(text);
    this.say(ok, ok ? `Kopiert: ${label}` : 'Kopieren nicht möglich – Browser hat es blockiert');
  }

  copyMarkdown(): void {
    void this.copy(this.markdown(), `Markdown (≈ ${this.exportTokens().toLocaleString('de-DE')} Tokens)`);
  }

  /** Same text as a file - for chats that take attachments, or to keep a snapshot next to a handoff. */
  downloadMarkdown(): void {
    const name = `ui-bausteine-${new Date().toISOString().slice(0, 10)}.md`;
    saveText(this.markdown(), name, 'text/markdown;charset=utf-8');
    this.say(true, `Gespeichert: ${name}`);
  }

  private say(ok: boolean, text: string): void {
    this.notice.set({ ok, text });
    clearTimeout(this.noticeTimer);
    this.noticeTimer = setTimeout(() => this.notice.set(null), 2500);
  }
}
