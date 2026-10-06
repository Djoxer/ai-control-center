import { Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';

import { Api } from '../api/api';
import { dashboardEvents } from '../api/fn/dashboard/dashboard-events';
import { DashboardEvent } from '../api/models/dashboard-event';
import { EventPage } from '../api/models/event-page';
import { StreamService } from '../core/stream.service';
import { Icon } from '../layout/icon';
import { clockTime } from './format';

export const EVENT_TOPIC = 'dashboard.event';
export const PAGE = 30;
export const MAX_EVENTS = 500;
const META_REFRESH_MS = 60_000;            // quiet counters and crash-watch status change slowly

/** Newest first by event time; the same stored event (id) only once. Live + page answers overlap. */
export function mergeEvents(list: DashboardEvent[]): DashboardEvent[] {
  const seen = new Set<number>();
  const unique = list.filter((e) => e.id === null || (!seen.has(e.id) && !!seen.add(e.id)));
  return unique
    .map((e, i) => ({ e, i, t: Date.parse(e.ts) }))
    .sort((a, b) => b.t - a.t || (b.e.id ?? 0) - (a.e.id ?? 0) || a.i - b.i)
    .map((x) => x.e)
    .slice(0, MAX_EVENTS);
}

@Component({
  selector: 'app-events-panel',
  host: { class: 'block' },
  imports: [Icon],
  templateUrl: './events-panel.html',
})
export class EventsPanel implements OnInit {
  private readonly api = inject(Api);
  private readonly stream = inject(StreamService);
  private readonly destroyRef = inject(DestroyRef);

  readonly events = signal<DashboardEvent[]>([]);
  readonly meta = signal<EventPage | null>(null);           // quiet counters, crash watch, cursor
  readonly nextBefore = signal<number | null>(null);
  readonly error = signal<string | null>(null);
  readonly loading = signal(false);

  readonly quietText = computed(() => {
    const m = this.meta();
    if (!m || !m.quiet.length) return null;
    const list = m.quiet.map((q) => `${q.name} ${q.loads}×`).join(', ');
    return `Nur gezählt: ${list} geladen seit ${clockTime(m.quietSince)}`;
  });

  constructor() {
    const off = this.stream.on<DashboardEvent>(EVENT_TOPIC, (e) => this.events.update((l) => mergeEvents([e, ...l])));
    const timer = setInterval(() => void this.load(false), META_REFRESH_MS);
    this.destroyRef.onDestroy(() => {
      off();
      clearInterval(timer);
    });
  }

  ngOnInit(): void {
    void this.load(true);
  }

  /** First page; on refresh only merge, so "older" pages the user loaded stay visible. */
  async load(first: boolean): Promise<void> {
    try {
      const page = await this.api.invoke(dashboardEvents, { limit: PAGE });
      this.meta.set(page);
      this.events.update((l) => mergeEvents([...l, ...page.events]));
      if (first) this.nextBefore.set(page.nextBefore ?? null);
      this.error.set(null);
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : 'Ereignisse nicht verfügbar');
    }
  }

  async loadOlder(): Promise<void> {
    const before = this.nextBefore();
    if (before === null || this.loading()) return;
    this.loading.set(true);
    try {
      const page = await this.api.invoke(dashboardEvents, { limit: PAGE, before });
      this.events.update((l) => mergeEvents([...l, ...page.events]));
      this.nextBefore.set(page.nextBefore ?? null);
    } catch (e) {
      this.error.set(e instanceof Error ? e.message : 'Ereignisse nicht verfügbar');
    } finally {
      this.loading.set(false);
    }
  }

  icon(level: DashboardEvent['level']): string {
    return level === 'critical' ? 'error' : level === 'warning' ? 'warning' : 'info';
  }

  iconClass(level: DashboardEvent['level']): string {
    return level === 'critical' ? 'text-red-400' : level === 'warning' ? 'text-amber-400' : 'text-gray-500';
  }

  /** "06.10. 14:12:10" local time. */
  time(ts: string): string {
    const d = new Date(ts);
    const p = (n: number) => String(n).padStart(2, '0');
    return `${p(d.getDate())}.${p(d.getMonth() + 1)}. ${clockTime(ts)}`;
  }
}
