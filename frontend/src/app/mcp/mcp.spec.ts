import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';

import { StreamService } from '../core/stream.service';
import '../testing/dialog-polyfill';
import { FakeEventSource, fakeEventSourceProvider } from '../testing/fake-event-source';
import { Mcp, SERVER_TOPIC } from './mcp';
import { mcpStatus as status } from '../testing/mcp-status';

const settle = () => new Promise((r) => setTimeout(r, 0));
const BASE = '/api/v1/mcp/servers';

describe('MCP page', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<Mcp>;
  let page: Mcp;
  let el: HTMLElement;

  async function open(list = [status()]) {
    FakeEventSource.instances = [];
    TestBed.configureTestingModule({
      imports: [Mcp],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([]), fakeEventSourceProvider],
    });
    http = TestBed.inject(HttpTestingController);
    TestBed.inject(StreamService).connect();
    fixture = TestBed.createComponent(Mcp);
    page = fixture.componentInstance;
    el = fixture.nativeElement;
    fixture.detectChanges();
    http.expectOne(BASE).flush(list);
    await settle();
    fixture.detectChanges();
  }

  const button = (text: string) =>
    [...el.querySelectorAll('button')].find((b) => b.textContent?.trim().includes(text)) as HTMLButtonElement;

  afterEach(() => http.verify());

  it('shows a card per server with state, command and tool hint', async () => {
    await open([status(), status({ key: 'rag', title: 'Bent-RAG', state: 'running', revision: 4,
      startedAt: '2026-10-07T11:00:00Z', pid: 4711, port: { port: 8000, open: true, managed: true } })]);
    const titles = [...el.querySelectorAll('article h2')].map((h) => h.textContent?.trim());
    expect(titles).toEqual(['Demo', 'Bent-RAG']);
    expect(el.textContent).toContain('Gestoppt');
    expect(el.textContent).toContain('Läuft');
    expect(el.textContent).toContain('python -m demo');
    expect(el.textContent).toContain('Noch nicht abgefragt.');
    expect(el.textContent).toContain('2 Server');
  });

  it('explains where servers come from when none is configured', async () => {
    await open([]);
    expect(el.textContent).toContain('Noch kein MCP-Server eingetragen');
    expect(el.textContent).toContain('[[modules.mcp.servers]]');
    expect(el.textContent).not.toMatch(/\b(?!127\.)\d{1,3}(\.\d{1,3}){3}\b/);   // placeholders, no LAN address
  });

  it('applies newer SSE updates and ignores older ones', async () => {
    await open([status({ revision: 5 })]);
    const es = FakeEventSource.latest();
    es.emit(SERVER_TOPIC, status({ revision: 4, state: 'crashed' }));   // late echo of an old state
    expect(page.servers()[0].state).toBe('stopped');
    es.emit(SERVER_TOPIC, status({ revision: 6, state: 'running' }));
    expect(page.servers()[0].state).toBe('running');
  });

  it('starts without asking and shows the answer', async () => {
    await open();
    button('Starten').click();
    const req = http.expectOne(`${BASE}/demo/start`);
    expect(req.request.method).toBe('POST');
    expect(page.busy().get('demo')).toBe('start');
    req.flush(status({ revision: 2, state: 'starting', pid: 99 }));
    await settle();
    fixture.detectChanges();
    expect(page.busy().size).toBe(0);
    expect(el.textContent).toContain('Startet …');
  });

  it('asks before stopping and sends nothing on cancel', async () => {
    await open([status({ state: 'running', port: { port: 8701, open: true, managed: true } })]);
    button('Stoppen').click();
    fixture.detectChanges();
    expect(page.confirm()?.action).toBe('stop');
    http.expectNone(`${BASE}/demo/stop`);
    button('Abbrechen').click();
    fixture.detectChanges();
    expect(page.confirm()).toBeNull();

    button('Stoppen').click();
    fixture.detectChanges();
    const dialogStop = [...el.querySelectorAll('dialog button')].find((b) => b.textContent?.includes('Stoppen')) as HTMLButtonElement;
    dialogStop.click();
    http.expectOne(`${BASE}/demo/stop`).flush(status({ revision: 3 }));
    await settle();
    expect(page.servers()[0].state).toBe('stopped');
  });

  it('shows why a start was refused', async () => {
    await open();
    void page.run('demo', 'start');
    http.expectOne(`${BASE}/demo/start`).flush(
      { detail: 'Port 8701 ist schon belegt (PID 1, x) – läuft der Server noch woanders?' },
      { status: 409, statusText: 'Conflict' });
    await settle();
    fixture.detectChanges();
    expect(page.actionErrors().get('demo')).toContain('Port 8701 ist schon belegt');
    expect(el.querySelector('[role=alert]')?.textContent).toContain('Port 8701 ist schon belegt');
  });

  it('crashed servers offer start and reset; reset needs no confirmation', async () => {
    await open([status({ state: 'crashed', exitCode: 3, lastError: 'Prozess ist abgestürzt (Exit-Code 3)',
      recentOutput: ['Traceback (most recent call last):', 'OSError: address in use'] })]);
    expect(el.textContent).toContain('Abgestürzt');
    expect(el.textContent).toContain('OSError: address in use');           // last output is visible
    button('Zurücksetzen').click();
    http.expectOne(`${BASE}/demo/stop`).flush(status({ revision: 2 }));
    await settle();
    expect(page.confirm()).toBeNull();
  });

  it('warns about a foreign process on the port and does not offer a pointless start', async () => {
    await open([status({ port: { port: 8000, open: true, managed: false, pid: 4711, process: 'python.exe' } })]);
    expect(el.textContent).toContain('Port belegt');
    expect(el.textContent).toContain('PID 4711 (python.exe)');
    expect(button('Starten').disabled).toBe(true);
  });

  it('fetches the tool list and renders the parameters', async () => {
    await open([status({ state: 'running', port: { port: 8701, open: true, managed: true } })]);
    button('Abfragen').click();
    http.expectOne(`${BASE}/demo/tools`).flush({
      fetchedAt: '2026-10-07T12:00:00Z', serverName: 'acc-demo', serverVersion: '2.3.0', protocolVersion: '2025-11-25',
      tools: [{ name: 'add', description: 'Addiert.', params: [
        { name: 'a', type: 'integer', required: true }, { name: 'b', type: 'integer', required: false, default: '1' }] }],
    });
    await settle();
    fixture.detectChanges();
    expect(el.textContent).toContain('a*: integer');
    expect(el.textContent).toContain('b: integer = 1');
    expect(el.textContent).toContain('acc-demo 2.3.0');
  });

  it('links the output to the logs page', async () => {
    await open();
    const link = el.querySelector('a[href*="/logs"]') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('/logs?source=mcp-demo');
  });

  it('computes the uptime on the server clock, not the browser clock', async () => {
    await open();
    // server clock 30 s behind the browser: as long as the status says asOf = browser - 30 s
    const browserNow = Date.now();
    FakeEventSource.latest().emit(SERVER_TOPIC, status({
      revision: 9, state: 'running', asOf: new Date(browserNow - 30_000).toISOString(),
      startedAt: new Date(browserNow - 30_000 - 60_000).toISOString(),
    }));
    page.now.set(browserNow);
    expect(Math.round((page.serverNow() - Date.parse(page.servers()[0].startedAt!)) / 1000)).toBe(60);
  });
});
