import { Component, computed, input, output } from '@angular/core';

import { CatalogModel } from '../api/models/catalog-model';
import * as fmt from '../dashboard/format';
import { Meter } from '../dashboard/meter';
import { Icon } from '../layout/icon';
import { Menu } from '../ui/menu';
import { ui } from '../ui/tokens';
import {
  capabilityChips, contextSource, relationText, verdictOrigin, verdictView, vramHint, vramPercent, vramText,
} from './state';

/**
 * One installed model inside its origin group: name and what it changes, effective context,
 * expected VRAM against the card, verdict. Presentational: the page sends refresh requests.
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
  readonly refresh = output<void>();
  readonly details = output<void>();

  protected readonly ui = ui;
  protected readonly fmt = fmt;
  protected readonly contextSource = contextSource;

  readonly chips = computed(() => capabilityChips(this.model().capabilities));
  readonly relation = computed(() => relationText(this.model()));
  readonly view = computed(() => verdictView(this.model().verdict));
  readonly pill = computed(() => `${ui.pill} ${ui.pillTone[this.view().tone]}`);
  readonly percent = computed(() => vramPercent(this.model().verdict));
  readonly vram = computed(() => vramText(this.model().verdict));
  readonly hint = computed(() => vramHint(this.model().verdict));
  /** Value color by origin; plain gray when there is no number. */
  readonly vramClass = computed(() => {
    const o = verdictOrigin(this.model().verdict);
    return o ? ui.origin[o] : 'text-gray-500';
  });
  readonly originWord = computed(() => {
    const v = this.model().verdict;
    return v.basis === 'measured' ? 'gemessen' : v.basis === 'estimated' ? 'geschätzt' : '';
  });
  /** Warnings deserve their sentence in the row, not only in a tooltip. */
  readonly warn = computed(() => this.view().tone !== 'normal');
}
