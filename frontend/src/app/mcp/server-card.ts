import { Component, computed, inject, input, output, signal, DestroyRef } from '@angular/core';
import { RouterLink } from '@angular/router';

import { McpServerStatus } from '../api/models/mcp-server-status';
import { ClipboardService } from '../core/clipboard.service';
import * as fmt from '../dashboard/format';
import { Icon } from '../layout/icon';
import { ui } from '../ui/tokens';
import { externalOwner, paramText, stateView, uptimeSeconds } from './state';

export type McpAction = 'start' | 'stop' | 'restart' | 'reset';

/**
 * One configured MCP server: state, actions, facts, command, last output, tools.
 * Presentational: the page owns the data and runs the requests; the card only emits what was clicked.
 */
@Component({
  selector: 'app-mcp-server-card',
  imports: [Icon, RouterLink],
  host: { class: 'block' },         // custom elements are inline: space-y of the page would not apply
  templateUrl: './server-card.html',
})
export class ServerCard {
  readonly server = input.required<McpServerStatus>();
  readonly serverNow = input.required<number>();       // ms, server clock - for the uptime
  readonly busy = input<McpAction | null>(null);         // request in flight for this server
  readonly actionError = input<string | null>(null);
  readonly toolsLoading = input(false);
  readonly action = output<McpAction>();
  readonly refreshTools = output<void>();

  protected readonly ui = ui;
  protected readonly fmt = fmt;
  protected readonly paramText = paramText;
  private readonly clipboard = inject(ClipboardService);

  readonly view = computed(() => stateView(this.server()));
  readonly pillClass = computed(() => `${ui.pill} ${ui.pillTone[this.view().tone]}`);
  readonly dotClass = computed(() => `${ui.dot} ${this.view().dot}`);
  readonly external = computed(() => externalOwner(this.server()));
  readonly uptime = computed(() => uptimeSeconds(this.server(), this.serverNow()));
  /** Buttons: what makes sense in this state. "stopping" offers nothing - the stop is underway. */
  readonly canStart = computed(() => ['stopped', 'crashed'].includes(this.server().state));
  /** Starting is pointless while someone else holds the port - the card says who instead. */
  readonly startBlocked = computed(() => this.external() !== null);
  readonly canStop = computed(() => ['starting', 'running'].includes(this.server().state));
  readonly calloutClass = computed(() =>
    `${ui.calloutFrame} ${ui.callout[this.server().state === 'crashed' ? 'critical' : 'warning']}`);
  readonly warnCallout = `${ui.calloutFrame} ${ui.callout.warning}`;
  readonly copied = signal(false);

  private copiedTimer: ReturnType<typeof setTimeout> | undefined;

  constructor() {
    inject(DestroyRef).onDestroy(() => clearTimeout(this.copiedTimer));
  }

  async copyUrl(): Promise<void> {
    const url = this.server().url;
    if (!url || !(await this.clipboard.copy(url))) return;
    this.copied.set(true);
    clearTimeout(this.copiedTimer);
    this.copiedTimer = setTimeout(() => this.copied.set(false), 2000);
  }
}
