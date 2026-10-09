import { Component, computed, inject, input, output, signal } from '@angular/core';

import { Candidate } from '../api/models/candidate';
import { ClipboardService } from '../core/clipboard.service';
import * as fmt from '../dashboard/format';
import { Icon } from '../layout/icon';
import { Menu } from '../ui/menu';
import { ui } from '../ui/tokens';
import {
  candidateFacts, candidateFit, capabilityChips, contextSource, downloadText, extraText, fitView, gib, readText,
  stepViews, verdictView, vramText, when,
} from './state';

/**
 * One model of Ollama's library, checked before the pull: what the download brings, which context the
 * card can take, the OpenCode prognosis and the pull command. Presentational: the page sends requests.
 */
@Component({
  selector: 'app-catalog-candidate-card',
  imports: [Icon, Menu],
  host: { class: 'block' },
  templateUrl: './candidate-card.html',
})
export class CandidateCard {
  readonly candidate = input.required<Candidate>();
  readonly available = input<number | null>(null);     // budget of the card (bytes)
  readonly busy = input(false);                         // a check of this name is running
  readonly recheck = output<void>();
  readonly forget = output<void>();

  protected readonly ui = ui;
  protected readonly fmt = fmt;
  protected readonly gib = gib;
  protected readonly when = when;
  protected readonly contextSource = contextSource;
  protected readonly readText = readText;

  private readonly clipboard = inject(ClipboardService);
  readonly copied = signal(false);

  readonly facts = computed(() => candidateFacts(this.candidate()));
  readonly chips = computed(() => capabilityChips(this.candidate().capabilities));
  readonly download = computed(() => downloadText(this.candidate()));
  readonly steps = computed(() => stepViews(this.candidate().steps));
  readonly fit = computed(() => candidateFit(this.candidate(), this.available()));
  readonly view = computed(() => {
    const v = this.candidate().verdict;
    return v ? verdictView(v) : null;
  });
  readonly pill = computed(() => {
    const v = this.view();
    return v ? `${ui.pill} ${ui.pillTone[v.tone]}` : `${ui.pill} ${ui.pillTone.normal}`;
  });
  readonly need = computed(() => {
    const v = this.candidate().verdict;
    return v ? vramText(v) : '—';
  });
  /** "+0,8": measured by a test run of the same weights beyond Ollama's count - part of the verdict. */
  readonly extra = computed(() => {
    const v = this.candidate().verdict;
    return v ? extraText(v) : '';
  });
  readonly opencode = computed(() => fitView(this.candidate().opencode));
  /** The minimum version is the one note that blocks a pull: it gets the warning color. */
  readonly warnings = computed(() => (this.candidate().notes ?? []).filter((n) => n.startsWith('Braucht Ollama')));
  readonly notes = computed(() => (this.candidate().notes ?? []).filter((n) => !n.startsWith('Braucht Ollama')));
  readonly estimated = ui.origin.estimated;
  readonly warnCallout = `${ui.calloutFrame} ${ui.callout.warning}`;

  async copy(text: string): Promise<void> {
    this.copied.set(await this.clipboard.copy(text));
    setTimeout(() => this.copied.set(false), 2000);
  }
}
