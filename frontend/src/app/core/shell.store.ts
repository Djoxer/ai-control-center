import { Injectable, computed, inject, signal } from '@angular/core';

import { Api } from '../api/api';
import { coreHealth } from '../api/fn/core/core-health';
import { HealthInfo } from '../api/models/health-info';
import { StreamService } from './stream.service';

export type ShellStatus = HealthInfo['status'] | 'offline' | 'reconnecting' | 'unknown';

/** Shell state: backend health + module list. One request feeds header status and menu. */
@Injectable({ providedIn: 'root' })
export class ShellStore {
  private readonly api = inject(Api);
  private readonly stream = inject(StreamService);
  private started = false;

  readonly health = signal<HealthInfo | null>(null);
  readonly error = signal<string | null>(null);     // backend unreachable -> shown instead of a blank page
  readonly loading = signal(false);

  // menu = every module the backend knows, sorted by the backend (order, key)
  readonly modules = computed(() => this.health()?.modules ?? []);
  readonly live = computed(() => this.stream.state() === 'open');
  readonly status = computed<ShellStatus>(() => {
    if (this.error()) return 'offline';
    if (this.stream.state() === 'reconnecting') return 'reconnecting';   // health may be stale
    return this.health()?.status ?? 'unknown';
  });

  /** Called once by the app: first health check, then let the live stream keep the status current. */
  start(): void {
    if (this.started) return;
    this.started = true;
    void this.refresh();
    let wasOpen = false;
    this.stream.onStateChange((s) => {
      // stream dropped -> check right away so the pill turns red without a click;
      // stream back -> the backend may have restarted with other module states
      if (s === 'reconnecting' || (s === 'open' && wasOpen)) void this.refresh();
      if (s === 'open') wasOpen = true;
    });
    this.stream.connect();
  }

  async refresh(): Promise<void> {
    this.loading.set(true);
    try {
      this.health.set(await this.api.invoke(coreHealth));   // relative URL /api/v1/health -> proxy or same origin
      this.error.set(null);
    } catch (e: unknown) {
      // keep the last known health: a short backend restart must not wipe the menu
      this.error.set(e instanceof Error ? e.message : 'backend not reachable');
    } finally {
      this.loading.set(false);
    }
  }
}
