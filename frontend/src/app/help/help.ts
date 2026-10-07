import { Component, ElementRef, OnInit, computed, inject, signal, viewChild } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { map } from 'rxjs';

import { Api } from '../api/api';
import { helpChangelog } from '../api/fn/help/help-changelog';
import { helpIndex } from '../api/fn/help/help-index';
import { Changelog } from '../api/models/changelog';
import { HelpDoc } from '../api/models/help-doc';
import { Icon } from '../layout/icon';
import { jumpTo } from '../ui/jump';
import { Tone, ui } from '../ui/tokens';
import { TocEntry, renderDoc, tocSelector } from './render';

export const CHANGELOG_KEY = 'changelog';

/** One topic of the page: a HELP.md from /api/v1/help or the changelog. */
export interface HelpTopic {
  key: string;                         // ?doc=<key>: 'core', a module key, or 'changelog'
  title: string;
  state: HelpDoc['moduleState'];       // module state; null for the general part and the changelog
  markdown: string | null;
  hint: string | null;                 // shown instead of the text when there is none
  tocDepth: number;                    // 3 = sections and subsections, 2 = versions only
}

/**
 * /help - one topic at a time, chosen by the query parameter ?doc=<key>.
 *
 * The ⋮ menu opens the help of the page on screen (?doc=logs on the Protokoll page), the About
 * dialog opens ?doc=changelog. Unknown or missing keys show the first topic (Allgemein).
 * Texts are fetched on every visit: the backend reads the files per request, so an edited HELP.md
 * shows up after a reload.
 */
@Component({
  selector: 'app-help',
  imports: [RouterLink, Icon],
  templateUrl: './help.html',
})
export class Help implements OnInit {
  protected readonly ui = ui;
  private readonly api = inject(Api);

  readonly docs = signal<HelpDoc[] | null>(null);
  readonly changelog = signal<Changelog | null>(null);
  readonly changelogError = signal<string | null>(null);
  readonly error = signal<string | null>(null);

  private readonly requested = toSignal(
    inject(ActivatedRoute).queryParamMap.pipe(map((p) => p.get('doc'))),
    { initialValue: null },
  );

  readonly topics = computed<HelpTopic[]>(() => {
    const docs = this.docs();
    if (docs === null) return [];
    const log = this.changelog();
    return [
      ...docs.map((d) => ({
        key: d.key, title: d.title, state: d.moduleState, markdown: d.markdown, hint: null, tocDepth: 3,
      })),
      {
        key: CHANGELOG_KEY,
        title: log?.title ?? 'Was ist neu',
        state: null,
        markdown: log?.markdown ?? null,
        hint: log?.hint ?? this.changelogError(),
        tocDepth: 2,
      },
    ];
  });

  readonly current = computed<HelpTopic | null>(() => {
    const topics = this.topics();
    return topics.find((t) => t.key === this.requested()) ?? topics[0] ?? null;
  });

  // computed: rendering runs once per topic switch, not on every change detection
  readonly rendered = computed(() => {
    const topic = this.current();
    return topic?.markdown ? renderDoc(topic.markdown, topic.tocDepth) : null;
  });

  private readonly content = viewChild<ElementRef<HTMLElement>>('content');

  async ngOnInit(): Promise<void> {
    // both at once; a missing changelog must not hide the help texts
    const [index, log] = await Promise.allSettled([this.api.invoke(helpIndex), this.api.invoke(helpChangelog)]);
    if (log.status === 'fulfilled') this.changelog.set(log.value);
    else this.changelogError.set(`„Was ist neu“ konnte nicht geladen werden: ${this.message(log.reason)}`);
    if (index.status === 'fulfilled') this.docs.set(index.value.docs);
    else this.error.set(this.message(index.reason));
  }

  /** Table of contents: scroll to the n-th heading and put the focus there (screen readers follow). */
  jump(entry: TocEntry): void {
    const topic = this.current();
    const box = this.content()?.nativeElement;
    if (!topic || !box) return;
    jumpTo(box.querySelectorAll<HTMLElement>(tocSelector(topic.tocDepth))[entry.index]);
  }

  protected stateTone(state: HelpTopic['state']): Tone {
    return state === 'failed' ? 'critical' : 'warning';
  }

  private message(e: unknown): string {
    if (e instanceof HttpErrorResponse) return e.status === 0 ? 'Backend nicht erreichbar' : `HTTP ${e.status}`;
    return e instanceof Error ? e.message : String(e);
  }
}
