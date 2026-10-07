import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { ActivatedRoute, convertToParamMap } from '@angular/router';

import { LogEntry } from '../api/models/log-entry';
import { StreamService } from '../core/stream.service';
import { FakeEventSource, fakeEventSourceProvider } from '../testing/fake-event-source';
import { Logs, PAGE_SIZE } from './logs';

const settle = () => new Promise((r) => setTimeout(r, 0));
const entry = (msg: string, over: Partial<LogEntry> = {}): LogEntry => ({
  source: 'control-center', file: 'control-center.log', ts: '2026-10-06T12:00:00+00:00',
  level: 'INFO', logger: 'control_center.core', msg, ...over,
});
const SOURCES = [
  { key: 'control-center', title: 'AI Control Center', format: 'json', available: true,
    files: [{ name: 'control-center.log', sizeBytes: 2048, modified: '2026-10-06T12:00:00Z' }] },
  { key: 'ollama', title: 'Ollama', format: 'text', available: false, files: [] },
];

describe('Logs page', () => {
  let http: HttpTestingController;
  let page: Logs;

  const entriesReq = (): TestRequest => http.expectOne((r) => r.url === '/api/v1/logs/entries');

  beforeEach(async () => {
    FakeEventSource.instances = [];
    TestBed.configureTestingModule({
      imports: [Logs],
      providers: [provideHttpClient(), provideHttpClientTesting(), fakeEventSourceProvider],
    });
    http = TestBed.inject(HttpTestingController);
    TestBed.inject(StreamService).connect();
    const fixture = TestBed.createComponent(Logs);
    page = fixture.componentInstance;
    fixture.detectChanges();                                   // ngOnInit
    http.expectOne('/api/v1/logs/sources').flush(SOURCES);
    await settle();
  });

  afterEach(() => http.verify());

  async function flushFirstPage(entries: LogEntry[], nextCursor: string | null = null) {
    const req = entriesReq();
    req.flush({ entries, nextCursor });
    await settle();
    return req;
  }

  it('picks the first available source and asks with sensible defaults', async () => {
    const req = await flushFirstPage([entry('b'), entry('a')]);
    expect(page.source()).toBe('control-center');
    expect(req.request.params.get('source')).toBe('control-center');
    expect(req.request.params.get('limit')).toBe(String(PAGE_SIZE));
    expect(req.request.params.getAll('exclude')).toEqual(['uvicorn.access']);   // own API noise hidden
    expect(page.entries().map((e) => e.msg)).toEqual(['b', 'a']);
  });

  it('prepends matching live lines and ignores the rest', async () => {
    await flushFirstPage([entry('old')]);
    page.setLevel('WARNING');
    await flushFirstPage([entry('warn-old', { level: 'WARNING' })]);
    const es = FakeEventSource.latest();
    es.emit('logs.control-center', entry('live-warn', { level: 'ERROR' }));
    es.emit('logs.control-center', entry('live-info'));                           // below level
    es.emit('logs.ollama', entry('other source', { level: 'ERROR' }));            // not selected
    page.live.set(false);
    es.emit('logs.control-center', entry('paused', { level: 'ERROR' }));          // live off
    expect(page.entries().map((e) => e.msg)).toEqual(['live-warn', 'warn-old']);
  });

  it('keeps live lines that arrive while the first page is loading', async () => {
    await flushFirstPage([]);
    page.setLevel('INFO');
    FakeEventSource.latest().emit('logs.control-center', entry('during-load'));
    await flushFirstPage([entry('from-page')]);
    expect(page.entries().map((e) => e.msg)).toEqual(['during-load', 'from-page']);
  });

  it('loads older entries with the cursor', async () => {
    await flushFirstPage([entry('new')], 'control-center.log|100');
    void page.loadOlder();
    const req = entriesReq();
    expect(req.request.params.get('cursor')).toBe('control-center.log|100');
    req.flush({ entries: [entry('older')], nextCursor: null });
    await settle();
    expect(page.entries().map((e) => e.msg)).toEqual(['new', 'older']);
    expect(page.nextCursor()).toBeNull();
  });

  it('drops the answer of an outdated request', async () => {
    await flushFirstPage([]);
    page.setLevel('ERROR');
    const first = entriesReq();
    page.setLevel('WARNING');
    const second = entriesReq();
    second.flush({ entries: [entry('current')], nextCursor: null });
    await settle();
    first.flush({ entries: [entry('stale')], nextCursor: null });                 // arrives late
    await settle();
    expect(page.entries().map((e) => e.msg)).toEqual(['current']);
  });

  it('formats timestamps in local time and tolerates missing ones', () => {
    const local = new Date(2026, 9, 6, 14, 5, 9, 7).toISOString();
    expect(page.formatTime(local)).toBe('06.10. 14:05:09.007');
    expect(page.formatTime(null)).toBe('—');
    void flushFirstPage([]);
  });
});

describe('Logs page opened with ?source=', () => {
  it('preselects the requested source (link from the MCP page), even without files', async () => {
    FakeEventSource.instances = [];
    TestBed.configureTestingModule({
      imports: [Logs],
      providers: [
        provideHttpClient(), provideHttpClientTesting(), fakeEventSourceProvider,
        { provide: ActivatedRoute, useValue: { snapshot: { queryParamMap: convertToParamMap({ source: 'ollama' }) } } },
      ],
    });
    const http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(Logs);
    fixture.detectChanges();
    http.expectOne('/api/v1/logs/sources').flush(SOURCES);
    await settle();
    expect(fixture.componentInstance.source()).toBe('ollama');           // not the first available one
    http.expectOne((r) => r.url === '/api/v1/logs/entries' && r.params.get('source') === 'ollama')
      .flush({ entries: [], nextCursor: null });
    http.verify();
  });
});
