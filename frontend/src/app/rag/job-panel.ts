import { Component, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import { JobStatus } from '../api/models/job-status';
import { Meter } from '../dashboard/meter';
import * as fmt from '../dashboard/format';
import { Icon } from '../layout/icon';
import { ui } from '../ui/tokens';
import { isRunning, jobStateView, phaseLabel, progressPercent, sourceStateView, when } from './state';

/**
 * The running reindex (progress, current file, cancel) or the summary of the last one.
 * Presentational: the page owns the job and sends the cancel request.
 */
@Component({
  selector: 'app-rag-job-panel',
  imports: [Icon, Meter, RouterLink],
  host: { class: 'block' },
  templateUrl: './job-panel.html',
})
export class JobPanel {
  readonly job = input.required<JobStatus>();
  readonly cancelBusy = input(false);
  readonly cancel = output<void>();

  protected readonly ui = ui;
  protected readonly fmt = fmt;
  protected readonly when = when;
  protected readonly phaseLabel = phaseLabel;

  readonly running = computed(() => isRunning(this.job()));
  readonly view = computed(() => jobStateView(this.job()));
  readonly pillClass = computed(() => `${ui.pill} ${ui.pillTone[this.view().tone]}`);
  readonly dotClass = computed(() => `${ui.dot} ${this.view().dot}`);
  readonly percent = computed(() => progressPercent(this.job().current));
  /** Title of the source being worked on (the progress only knows the collection name). */
  readonly currentTitle = computed(() => {
    const cur = this.job().current;
    return cur ? (this.job().sources.find((s) => s.collection === cur.collection)?.title ?? cur.collection) : '';
  });
  readonly durationS = computed(() => {
    const j = this.job();
    if (!j.startedAt || !j.finishedAt) return null;
    return (Date.parse(j.finishedAt) - Date.parse(j.startedAt)) / 1000;
  });

  sourcePill(state: JobStatus['sources'][number]['state']): string {
    return `${ui.pill} ${ui.pillTone[sourceStateView(state).tone]}`;
  }

  sourceLabel(state: JobStatus['sources'][number]['state']): string {
    return sourceStateView(state).label;
  }
}
