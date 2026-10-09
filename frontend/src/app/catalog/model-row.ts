import { Component, computed, input, output } from '@angular/core';

import { BenchStatus } from '../api/models/bench-status';
import { CatalogModel } from '../api/models/catalog-model';
import * as fmt from '../dashboard/format';
import { Meter } from '../dashboard/meter';
import { Icon } from '../layout/icon';
import { Menu } from '../ui/menu';
import { ui } from '../ui/tokens';
import {
  benchSummary, capabilityChips, contextSource, extraText, fitView, isBenchRunning, originWord, relationText,
  usageChips, verdictOrigin, verdictView, vramHint, vramPercent, vramText, when,
} from './state';

/**
 * One installed model inside its origin group: name and what it changes, effective context,
 * VRAM need against the card's budget, verdict, latest test run. Presentational: the page sends requests.
 */
@Component({
  selector: 'app-catalog-model-row',
  imports: [Icon, Menu, Meter],
  host: { class: 'block' },
  templateUrl: './model-row.html',
})
export class ModelRow {
  readonly model = input.required<CatalogModel>();
  readonly indent = input(0);                       // tree level inside the group card
  readonly busy = input(false);                     // a refresh of this model is running
  readonly locked = input<string | null>(null);     // why refreshing is not possible right now
  readonly testLocked = input<string | null>(null); // why a test run is not possible right now
  readonly live = input<BenchStatus | null>(null);  // the running test run of this model (SSE), newer than the overview
  readonly refresh = output<void>();
  readonly details = output<void>();
  readonly test = output<void>();
  readonly usageEdit = output<void>();

  protected readonly ui = ui;
  protected readonly fmt = fmt;
  protected readonly contextSource = contextSource;

  readonly chips = computed(() => capabilityChips(this.model().capabilities));
  readonly usage = computed(() => usageChips(this.model().usage));
  readonly fit = computed(() => fitView(this.model().opencode));
  readonly fitTitle = computed(() => this.model().opencode?.reasons?.join(' ') ?? '');
  readonly taggedOpencode = computed(() => (this.model().usage?.tags ?? []).includes('opencode'));
  readonly relation = computed(() => relationText(this.model()));
  readonly view = computed(() => verdictView(this.model().verdict));
  readonly pill = computed(() => `${ui.pill} ${ui.pillTone[this.view().tone]}`);
  readonly percent = computed(() => vramPercent(this.model().verdict));
  readonly vram = computed(() => vramText(this.model().verdict));
  readonly extra = computed(() => extraText(this.model().verdict));
  readonly hint = computed(() => vramHint(this.model().verdict));
  /** Value color by origin; plain gray when there is no number. */
  readonly vramClass = computed(() => {
    const o = verdictOrigin(this.model().verdict);
    return o ? ui.origin[o] : 'text-gray-500';
  });
  readonly originWord = computed(() => originWord(this.model()));
  /** Latest test run of this model: the live one, else the newest the overview knows. */
  readonly bench = computed(() => this.live() ?? this.model().benches?.[0] ?? null);
  readonly benchRunning = computed(() => isBenchRunning(this.bench()));
  readonly benchText = computed(() => {
    const b = this.bench();
    if (!b) return '';
    return this.benchRunning() ? benchSummary(b) : `${benchSummary(b)} · ${when(b.finishedAt ?? b.createdAt)}`;
  });
  readonly benchClass = computed(() => {
    const b = this.bench();
    if (!b || this.benchRunning()) return 'text-sky-300';
    return b.state === 'done' ? ui.origin.measured : 'text-red-300';
  });
  /** Why the test entry is disabled, or null. */
  readonly testBlock = computed(() =>
    this.model().testable === false ? 'Einbettungsmodelle haben (noch) keinen Testlauf.' : this.testLocked());
  /** Warnings deserve their sentence in the row, not only in a tooltip. */
  readonly warn = computed(() => this.view().tone !== 'normal');
}
