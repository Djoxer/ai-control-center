import { Injectable, computed, inject, signal } from '@angular/core';

import { Api } from '../api/api';
import { coreHealth } from '../api/fn/core/core-health';
import { HealthInfo } from '../api/models/health-info';

/** Shell state: backend health + module list. One request feeds header status and menu. */
@Injectable({ providedIn: 'root' })
export class ShellStore {
  private readonly api = inject(Api);

  readonly health = signal<HealthInfo | null>(null);
  readonly error = signal<string | null>(null);     // backend unreachable -> shown instead of a blank page
  readonly loading = signal(false);

  // menu = every module the backend knows, sorted by the backend (order, key)
  readonly modules = computed(() => this.health()?.modules ?? []);
  readonly status = computed(() => (this.error() ? 'offline' : this.health()?.status ?? 'unknown'));

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
