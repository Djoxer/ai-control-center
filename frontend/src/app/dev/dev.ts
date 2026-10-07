import { HttpClient } from '@angular/common/http';
import { Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';

import { ClipboardService } from '../core/clipboard.service';
import { Icon } from '../layout/icon';
import { ui } from '../ui/tokens';
import { IconEntry, iconSnippet, parseIconSprite } from './icons';

interface Notice {
  ok: boolean;
  text: string;
}

/** Developer page (ng serve only): icon gallery now, style guide in part 4b. */
@Component({
  selector: 'app-dev',
  imports: [Icon],
  templateUrl: './dev.html',
})
export class Dev implements OnInit {
  private readonly http = inject(HttpClient);
  private readonly clipboard = inject(ClipboardService);
  private readonly destroyRef = inject(DestroyRef);

  readonly ui = ui;
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

  async copy(text: string): Promise<void> {
    const ok = await this.clipboard.copy(text);
    this.notice.set(ok ? { ok, text: `Kopiert: ${text}` } : { ok, text: 'Kopieren nicht möglich – Browser hat es blockiert' });
    clearTimeout(this.noticeTimer);
    this.noticeTimer = setTimeout(() => this.notice.set(null), 2500);
  }
}
