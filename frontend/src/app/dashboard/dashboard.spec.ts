import { Component } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';

import { DashboardSnapshot } from '../api/models/dashboard-snapshot';
import { LoadedModel } from '../api/models/loaded-model';
import { StreamService } from '../core/stream.service';
import { FakeEventSource, fakeEventSourceProvider } from '../testing/fake-event-source';
import { Dashboard, SNAPSHOT_TOPIC, STALE_AFTER_MS } from './dashboard';
import { GIB } from './format';
import { Meter } from './meter';
import { Icon } from '../layout/icon';

const settle = () => new Promise((r) => setTimeout(r, 0));

@Component({ selector: 'app-events-panel', template: '' })
class EventsPanelStub {}

@Component({ selector: 'app-history-panel', template: '' })
class HistoryPanelStub {}

const split = (over: Partial<LoadedModel> = {}): LoadedModel => ({
  name: 'qwen2.5-coder:14b', digest: 'd1', family: 'qwen2', parameterSize: '14.8B', quantization: 'Q4_K_M',
  sizeBytes: 16 * GIB, vramBytes: 13.5 * GIB, gpuRatio: 0.8438, placement: 'split', contextLength: 32768,
  expiresAt: '2026-10-06T12:05:00Z', pinned: false, unloading: false, ...over,
});

const snap = (over: Partial<DashboardSnapshot> = {}): DashboardSnapshot => ({
  ts: '2026-10-06T12:00:00Z', nodeId: 'ai-box', simulated: [], ollamaOnline: true, ollamaVersion: '0.12.6',
  models: [],
  gpu: { name: 'RTX 5070 Ti', driverVersion: '581.42', utilPercent: 41, vramUsedMib: 8000, vramTotalMib: 16303,
         tempC: 58, powerW: 162.8, powerLimitW: 300, fanPercent: 39, throttleReasons: ['power_cap'] },
  host: { cpuPercent: 12.5, cpuCount: 16, ramUsedBytes: 10 * GIB, ramTotalBytes: 32 * GIB, uptimeS: 3600,
          processes: [{ name: 'ollama.exe', pid: 6232, cpuPercent: 0.2, rssBytes: 112_000_000, cmdline: 'ollama.exe serve' }] },
  disks: [{ mount: 'C:\\', totalBytes: 2000 * GIB, usedBytes: 700 * GIB, freeBytes: 1300 * GIB }],
  services: [{ key: 'ollama', title: 'Ollama', up: true, httpStatus: 200, latencyMs: 3.2, error: null }],
  warnings: [], errors: [], ...over,
});

describe('Dashboard page', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<Dashboard>;
  let page: Dashboard;

  const text = () => (fixture.nativeElement as HTMLElement).textContent?.replace(/\s+/g, ' ') ?? '';
  const render = async () => {
    await settle();
    fixture.detectChanges();
  };

  beforeEach(() => {
    FakeEventSource.instances = [];
    TestBed.configureTestingModule({
      imports: [Dashboard],
      providers: [provideHttpClient(), provideHttpClientTesting(), fakeEventSourceProvider],
    });
    // the panels have their own specs and requests; here only the page itself is under test
    TestBed.overrideComponent(Dashboard, { set: { imports: [Icon, Meter, EventsPanelStub, HistoryPanelStub] } });
    http = TestBed.inject(HttpTestingController);
    TestBed.inject(StreamService).connect();
    fixture = TestBed.createComponent(Dashboard);
    page = fixture.componentInstance;
    fixture.detectChanges();                                         // ngOnInit -> GET /snapshot
  });

  afterEach(() => http.verify());

  const flushSnapshot = async (s: DashboardSnapshot) => {
    http.expectOne('/api/v1/dashboard/snapshot').flush(s);
    await render();
  };

  it('shows the loading state, then the snapshot from the GET', async () => {
    expect(text()).toContain('Lade Dashboard');
    await flushSnapshot(snap({ models: [split()] }));
    expect(text()).toContain('qwen2.5-coder:14b');
    expect(text()).toContain('Teil-Offload: 84 % GPU · 16 % CPU');
    expect(text()).toContain('in 5 min');                           // against the snapshot ts
    expect(text()).toContain('1 Modell geladen');
  });

  it('renders warnings with role and level word, and tints the matching meter', async () => {
    await flushSnapshot(snap({ warnings: [
      { code: 'model_split', level: 'critical', message: 'nur 84 % auf der GPU', subject: 'qwen2.5-coder:14b' },
      { code: 'vram_low', level: 'warning', message: 'Nur noch 403 MiB VRAM frei', subject: null },
    ] }));
    const alert = (fixture.nativeElement as HTMLElement).querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('Kritisch:');
    expect(page.tone('vram_low')).toBe('warning');
    expect(page.tone('ram_high')).toBe('normal');
  });

  it('takes newer snapshots from the stream and drops older ones', async () => {
    await flushSnapshot(snap());
    const es = FakeEventSource.latest();
    es.emit(SNAPSHOT_TOPIC, snap({ ts: '2026-10-06T12:00:02Z', nodeId: 'newer' }));
    expect(page.snapshot()?.nodeId).toBe('newer');
    es.emit(SNAPSHOT_TOPIC, snap({ ts: '2026-10-06T11:59:58Z', nodeId: 'late GET' }));
    expect(page.snapshot()?.nodeId).toBe('newer');
  });

  it('a stream snapshot that wins the race is not replaced by the slower GET', async () => {
    FakeEventSource.latest().emit(SNAPSHOT_TOPIC, snap({ ts: '2026-10-06T12:00:04Z', nodeId: 'stream' }));
    await flushSnapshot(snap({ ts: '2026-10-06T12:00:00Z', nodeId: 'get' }));
    expect(page.snapshot()?.nodeId).toBe('stream');
  });

  it('marks the view stale when no snapshot arrives', async () => {
    await flushSnapshot(snap());
    FakeEventSource.latest().open();
    expect(page.live()).toBe(true);
    page.now.set(page.receivedAt()! + STALE_AFTER_MS + 1);
    fixture.detectChanges();
    expect(page.stale()).toBe(true);
    expect(page.live()).toBe(false);
    expect(text()).toContain('Veraltet');
  });

  it('labels simulated sources and an offline Ollama', async () => {
    await flushSnapshot(snap({
      simulated: ['gpu', 'host'], ollamaOnline: false,
      errors: [{ source: 'ollama', message: 'ConnectError: refused', since: '2026-10-06T11:59:00Z' }],
    }));
    expect(text()).toContain('Simulierte Daten: GPU, Host');
    expect(text()).toContain('Ollama nicht erreichbar');
    expect(text()).toContain('Datenquellen mit Fehlern');
  });

  it('shows null sources as missing data, not as zero', async () => {
    await flushSnapshot(snap({ gpu: null, host: null, disks: null,
      errors: [{ source: 'gpu', message: 'NVML: Library not found', since: '2026-10-06T11:59:00Z' }] }));
    expect(text()).toContain('NVML: Library not found');
    expect(text()).toContain('Keine Host-Daten');
    expect(text()).not.toContain('0 %');
  });

  it('explains a 503 instead of showing a blank page', async () => {
    http.expectOne('/api/v1/dashboard/snapshot')
      .flush({ detail: 'dashboard module is not running' }, { status: 503, statusText: 'Service Unavailable' });
    await render();
    expect(text()).toContain('Das Dashboard-Modul läuft nicht');
  });

  it('stops listening when the page is left', async () => {
    await flushSnapshot(snap());
    fixture.destroy();
    FakeEventSource.latest().emit(SNAPSHOT_TOPIC, snap({ ts: '2026-10-06T12:00:09Z', nodeId: 'after destroy' }));
    expect(page.snapshot()?.nodeId).toBe('ai-box');
  });
});
