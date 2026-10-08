import { Component, computed, input } from '@angular/core';

import { CatalogModel } from '../api/models/catalog-model';
import * as fmt from '../dashboard/format';
import { ui } from '../ui/tokens';
import {
  benchSummary, contextExplain, gib, observationText, parameterList, shortDigest, verdictOrigin, verdictView, when,
} from './state';

/**
 * Everything the catalog knows about one model - content of the details dialog. The estimate is shown
 * as a packing list (weights + KV cache + reserves), observations as a table, parameters in full.
 */
@Component({
  selector: 'app-catalog-model-details',
  host: { class: 'block' },
  templateUrl: './model-details.html',
})
export class ModelDetails {
  readonly model = input.required<CatalogModel>();

  protected readonly ui = ui;
  protected readonly fmt = fmt;
  protected readonly gib = gib;
  protected readonly when = when;
  protected readonly shortDigest = shortDigest;
  protected readonly observationText = observationText;
  protected readonly benchSummary = benchSummary;

  readonly context = computed(() => contextExplain(this.model().context));
  readonly benchCount = computed(() => {
    const n = this.model().benches?.length ?? 0;
    return n === 1 ? '1 Lauf' : `${n} Läufe`;
  });
  readonly params = computed(() => parameterList(this.model().parameters));
  readonly view = computed(() => verdictView(this.model().verdict));
  readonly verdictClass = computed(() => {
    const o = verdictOrigin(this.model().verdict);
    return o ? ui.origin[o] : 'text-gray-400';
  });
  readonly estimated = ui.origin.estimated;
  readonly measured = ui.origin.measured;
  readonly lowCallout = `${ui.calloutFrame} ${ui.callout.info}`;
}
