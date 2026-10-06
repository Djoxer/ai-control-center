import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';

import { DashboardEvent } from '../api/models/dashboard-event';
import { EventPage } from '../api/models/event-page';
import { StreamService } from '../core/stream.service';
import { FakeEventSource, fakeEventSourceProvider } from '../testing/fake-event-source';
import { EVENT_TOPIC, EventsPanel, mergeEvents } from './events-panel';

const settle = () => new Promise((r) => setTimeout(r, 0));
const URL = '/api/v1/dashboard/events';

const ev = (id: number | null, sec: number, over: Partial<DashboardEvent> = {}): DashboardEvent => ({
  id, ts: new Date(Date.UTC(2026, 9, 6, 12, 0, sec)).toISOString(), kind: 'model_loaded', level: 'info',
  subject: null, message: `event ${id}`, ...over,
});
const page = (events: DashboardEvent[], over: Partial<EventPage> = {}): EventPage => ({
  events, nextBefore: null, quiet: [], quietSince: '2026-10-06T10:00:00Z',
  crashWatch: { active: true, file: 'server.log', reason: null }, ...over,
});

describe('mergeEvents', () => {
  it('sorts by event time and drops duplicates by id', () => {
    const merged = mergeEvents([ev(3, 30), ev(1, 10), ev(3, 30), ev(null, 20), ev(2, 5)]);
    expect(merged.map((e) => e.id)).toEqual([3, null, 1, 2]);   // "down" events are dated back: time wins
  });
});

describe('EventsPanel', () => {
  let http: HttpTestingController;
  let panel: EventsPanel;

  beforeEach(() => {
    FakeEventSource.instances = [];
    TestBed.configureTestingModule({
      imports: [EventsPanel],
      providers: [provideHttpClient(), provideHttpClientTesting(), fakeEventSourceProvider],
    });
    http = TestBed.inject(HttpTestingController);
    TestBed.inject(StreamService).connect();
    const fixture = TestBed.createComponent(EventsPanel);
    panel = fixture.componentInstance;
    fixture.detectChanges();
  });

  afterEach(() => http.verify());

  it('shows the first page and live events without doubles', async () => {
    const es = FakeEventSource.latest();
    es.emit(EVENT_TOPIC, ev(5, 50));                         // arrives while the page is loading
    http.expectOne((r) => r.url === URL).flush(page([ev(5, 50), ev(4, 40)], { nextBefore: 4 }));
    await settle();
    expect(panel.events().map((e) => e.id)).toEqual([5, 4]);
    es.emit(EVENT_TOPIC, ev(6, 60, { level: 'critical', kind: 'ollama_crash' }));
    expect(panel.events()[0].id).toBe(6);
    expect(panel.meta()?.crashWatch.file).toBe('server.log');
  });

  it('loads older pages with the cursor', async () => {
    http.expectOne((r) => r.url === URL).flush(page([ev(9, 90)], { nextBefore: 9 }));
    await settle();
    void panel.loadOlder();
    const req = http.expectOne((r) => r.url === URL && r.params.get('before') === '9');
    req.flush(page([ev(8, 80)]));
    await settle();
    expect(panel.events().map((e) => e.id)).toEqual([9, 8]);
    expect(panel.nextBefore()).toBeNull();
  });

  it('summarises quiet models', async () => {
    http.expectOne((r) => r.url === URL).flush(page([], { quiet: [{ name: 'nomic-embed-text:latest', loads: 37 }] }));
    await settle();
    expect(panel.quietText()).toContain('nomic-embed-text:latest 37×');
  });
});
