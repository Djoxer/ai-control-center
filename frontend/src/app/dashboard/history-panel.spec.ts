import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';

import { DashboardHistory } from '../api/models/dashboard-history';
import { HistoryPanel } from './history-panel';

const settle = () => new Promise((r) => setTimeout(r, 0));
const URL = '/api/v1/dashboard/history';

const history = (over: Partial<DashboardHistory> = {}): DashboardHistory => ({
  range: '1h', resolution: 'raw', stepS: 10,
  since: '2026-10-06T11:00:00Z', until: '2026-10-06T12:00:00Z',
  ts: ['2026-10-06T11:59:40Z', '2026-10-06T11:59:50Z'],
  series: [{ metric: 'vram_used_mib', unit: 'MiB', scaleMax: 16303, warn: 14803, avg: [100, 200], max: [100, 200] }],
  ...over,
});

describe('HistoryPanel', () => {
  let http: HttpTestingController;
  let panel: HistoryPanel;

  beforeEach(async () => {
    TestBed.configureTestingModule({ imports: [HistoryPanel], providers: [provideHttpClient(), provideHttpClientTesting()] });
    http = TestBed.inject(HttpTestingController);
    const fixture = TestBed.createComponent(HistoryPanel);
    panel = fixture.componentInstance;
    fixture.detectChanges();
  });

  afterEach(() => http.verify());

  it('starts with one hour and maps series to charts', async () => {
    const req = http.expectOne((r) => r.url === URL);
    expect(req.request.params.get('range')).toBe('1h');
    req.flush(history());
    await settle();
    const [c] = panel.charts();
    expect(c.label).toBe('VRAM');
    expect(c.ts).toEqual([Date.parse('2026-10-06T11:59:40Z'), Date.parse('2026-10-06T11:59:50Z')]);
    expect(c.max).toBeNull();                                 // raw rows: no separate peak line
    expect(c.to - c.from).toBe(3_600_000);                    // axis spans the range, not just the data
  });

  it('switching the range drops the slower old answer', async () => {
    const first = http.expectOne((r) => r.url === URL);
    panel.setRange('7d');
    const second = http.expectOne((r) => r.url === URL && r.params.get('range') === '7d');
    second.flush(history({ range: '7d', resolution: 'hour', stepS: 3600 }));
    first.flush(history());                                   // late answer for 1h
    await settle();
    expect(panel.data()?.range).toBe('7d');
    expect(panel.charts()[0].max).toEqual([100, 200]);        // condensed: peak line available
  });
});
