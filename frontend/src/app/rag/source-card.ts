import { Component, computed, input, output } from '@angular/core';

import { SourceInfo } from '../api/models/source-info';
import * as fmt from '../dashboard/format';
import { Icon } from '../layout/icon';
import { Tone, ui } from '../ui/tokens';
import { includeText, reportFacts, sourceStateView, when } from './state';

/** Value colors of the report counters - text color only, the label next to it says the same. */
const FACT_TONE: Record<Tone, string> = {
  normal: 'text-white',
  warning: 'text-amber-300',
  critical: 'text-red-300',
};

/**
 * One configured source: folder and rules, size of its collection, last run with file lists.
 * Presentational: the page decides whether reindexing is allowed and sends the request.
 */
@Component({
  selector: 'app-rag-source-card',
  imports: [Icon],
  host: { class: 'block' },
  templateUrl: './source-card.html',
})
export class SourceCard {
  readonly source = input.required<SourceInfo>();
  /** null = reindex allowed; otherwise the reason it is not (shown as tooltip). */
  readonly blocked = input<string | null>(null);
  readonly active = input(false);                     // this source is being indexed right now
  readonly reachable = input(true);                   // store reachable: otherwise stats are unknown, not missing
  readonly reindex = output<void>();

  protected readonly ui = ui;
  protected readonly fmt = fmt;
  protected readonly when = when;
  protected readonly includeText = includeText;
  protected readonly factTone = FACT_TONE;

  readonly run = computed(() => this.source().lastRun ?? null);
  readonly facts = computed(() => {
    const r = this.run();
    return r ? reportFacts(r) : [];
  });
  readonly runView = computed(() => sourceStateView(this.run()?.state));
  readonly runPill = computed(() => `${ui.pill} ${ui.pillTone[this.runView().tone]}`);

  /** Pill next to the title: what the collection looks like right now. */
  readonly sizePill = computed(() => {
    const s = this.source();
    if (!this.reachable()) return { text: 'Stand unbekannt', cls: `${ui.pill} ${ui.pillTone.normal}` };
    if (s.stats) return { text: `${fmt.num(s.stats.points ?? 0)} Punkte`, cls: `${ui.pill} ${ui.pillTone.normal}` };
    if (!s.pathExists) return { text: 'Ordner fehlt', cls: `${ui.pill} ${ui.pillTone.critical}` };
    return { text: 'Collection fehlt noch', cls: `${ui.pill} ${ui.pillTone.warning}` };
  });
  readonly missingCallout = `${ui.calloutFrame} ${ui.callout.warning}`;
}
