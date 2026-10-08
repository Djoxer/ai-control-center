import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';

import { RagOverview } from '../api/models/rag-overview';
import { StreamService } from '../core/stream.service';
import '../testing/dialog-polyfill';
import { FakeEventSource, fakeEventSourceProvider } from '../testing/fake-event-source';
import { ragJob, ragOverview, ragReport, ragSource } from '../testing/rag-overview';
import { JOB_TOPIC, Rag } from './rag';

const settle = () => new Promise((r) => setTimeout(r, 0));
const OVERVIEW = '/api/v1/rag/overview';
const JOBS = '/api/v1/rag/jobs';

describe('RAG page', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<Rag>;
  let page: Rag;
  let el: HTMLElement;

  async function open(ov: RagOverview = ragOverview()) {
    FakeEventSource.instances = [];
    TestBed.configureTestingModule({
      imports: [Rag],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([]), fakeEventSourceProvider],
    });
    http = TestBed.inject(HttpTestingController);
    TestBed.inject(StreamService).connect();
    fixture = TestBed.createComponent(Rag);
    page = fixture.componentInstance;
    el = fixture.nativeElement;
    fixture.detectChanges();
    http.expectOne(OVERVIEW).flush(ov);
    await settle();
    fixture.detectChanges();
  }

  async function render() {
    await settle();
    fixture.detectChanges();
  }

  const buttons = (text: string) =>
    [...el.querySelectorAll('button')].filter((b) => b.textContent?.trim().includes(text)) as HTMLButtonElement[];
  const dialogButton = (text: string) =>
    [...el.querySelectorAll('dialog button')].find((b) => b.textContent?.trim().includes(text)) as HTMLButtonElement;

  afterEach(() => http.verify());

  it('shows store, sources and every collection, managed or not', async () => {
    await open();
    expect(el.textContent).toContain('Qdrant 1.19.2');
    expect(el.textContent).toContain('nomic-embed-text');
    expect(el.querySelector('article h3')?.textContent?.trim()).toBe('Bent PHP');
    expect(el.textContent).toContain('124 Punkte');
    expect(el.textContent).toContain('src → .php');
    const rows = [...el.querySelectorAll('tbody tr')].map((r) => r.textContent ?? '');
    expect(rows.length).toBe(2);
    expect(rows[0]).toContain('nicht hier verwaltet');                    // the old "bent" collection
    expect(el.textContent).toContain('Noch nicht über das Control Center indexiert');
    const link = [...el.querySelectorAll('a')].find((a) => a.textContent?.includes('Qdrant-Weboberfläche'));
    expect(link?.getAttribute('href')).toBe(`http://${location.hostname}:6333/dashboard`);
  });

  it('explains where sources come from when none is configured', async () => {
    await open(ragOverview({ sources: [] }));
    expect(el.textContent).toContain('Noch keine Quelle eingetragen');
    expect(el.textContent).toContain('[[modules.rag.sources]]');
    expect(el.textContent).not.toMatch(/\b(?!127\.)\d{1,3}(\.\d{1,3}){3}\b/);   // placeholders, no LAN address
  });

  it('shows the counters and file lists of the last run', async () => {
    await open(ragOverview({ sources: [ragSource({ lastRun: ragReport() })] }));
    expect(el.textContent).toContain('Abgeschnitten');
    expect(el.textContent).toContain('31');
    expect(el.textContent).toContain('Secret-Filter (1 Treffer)');
    expect(el.textContent).toContain('src/Config.php:3');
    expect(el.textContent).toContain('Abgeschnittene Dateien (1)');
  });

  it('asks before reindexing and sends nothing on cancel', async () => {
    await open();
    buttons('Neu indexieren')[0].click();
    fixture.detectChanges();
    expect(page.confirmReindex()?.collections).toEqual(['bent_php']);
    expect(el.querySelector('dialog')?.textContent).toContain('entlädt das Chat-Modell');
    dialogButton('Abbrechen').click();
    fixture.detectChanges();
    http.expectNone(JOBS);

    buttons('Neu indexieren')[0].click();
    fixture.detectChanges();
    dialogButton('Starten').click();
    const req = http.expectOne(JOBS);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ collections: ['bent_php'] });
    req.flush(ragJob({ state: 'queued', current: null }), { status: 202, statusText: 'Accepted' });
    await render();
    expect(el.textContent).toContain('Indexierung');
    expect(buttons('Neu indexieren')[0].disabled).toBe(true);             // one job at a time
  });

  it('follows the job via SSE, ignores old echoes and reloads when it ends', async () => {
    await open();
    const es = FakeEventSource.latest();
    es.emit(JOB_TOPIC, ragJob({ revision: 3 }));
    await render();
    expect(el.textContent).toContain('40 / 124');
    expect(el.textContent).toContain('src/Note.php');
    expect(el.querySelector('article')?.textContent).toContain('wird indexiert');
    es.emit(JOB_TOPIC, ragJob({ revision: 2, current: { collection: 'bent_php', phase: 'embed', done: 1, total: 124 } }));
    expect(page.job()?.revision).toBe(3);

    es.emit(JOB_TOPIC, ragJob({ revision: 4, state: 'done', current: null, finishedAt: '2026-10-08T09:02:00Z',
      sources: [ragReport()] }));
    http.expectOne(OVERVIEW).flush(ragOverview({ sources: [ragSource({ lastRun: ragReport() })] }));
    await render();
    expect(el.textContent).toContain('Letzte Indexierung');
    expect(el.textContent).toContain('Fertig');
    expect(buttons('Neu indexieren')[0].disabled).toBe(false);
  });

  it('cancels the running job', async () => {
    await open(ragOverview({ job: ragJob() }));
    buttons('Abbrechen')[0].click();
    const req = http.expectOne(`${JOBS}/job1/cancel`);
    expect(req.request.method).toBe('POST');
    req.flush(ragJob({ revision: 2, cancelRequested: true }));
    await render();
    expect(el.textContent).toContain('Bricht ab …');
  });

  it('shows why a start was refused', async () => {
    await open();
    page.askReindex(null);
    void page.startReindex();
    http.expectOne(JOBS).flush({ detail: 'Es läuft schon eine Indexierung – erst abwarten oder abbrechen.' },
      { status: 409, statusText: 'Conflict' });
    await render();
    expect(el.querySelector('[role=alert]')?.textContent).toContain('Es läuft schon eine Indexierung');
  });

  it('locks writing when Qdrant runs on another machine', async () => {
    const reason = 'Schreibschutz: Qdrant läuft auf einem anderen Rechner (10.0.0.5).';
    await open(ragOverview({ writes: { allowed: false, reason },
      store: { mode: 'qdrant', url: 'http://10.0.0.5:6333', reachable: true, version: '1.19.2', local: false, port: 6333 } }));
    expect(el.textContent).toContain('Nur lesen:');
    const btn = buttons('Neu indexieren')[0];
    expect(btn.disabled).toBe(true);
    expect(btn.title).toBe(reason);
  });

  it('says so when Qdrant is down', async () => {
    await open(ragOverview({ collections: [],
      store: { mode: 'qdrant', url: 'http://127.0.0.1:6333', reachable: false, local: true, port: 6333,
               error: 'Qdrant nicht erreichbar (http://127.0.0.1:6333): ConnectError' } }));
    expect(el.querySelector('[role=alert]')?.textContent).toContain('Keine Verbindung: Qdrant nicht erreichbar');
    expect(el.textContent).toContain('Stand unbekannt');
    expect(el.textContent).not.toContain('Collection fehlt noch');          // unknown is not missing
    expect(buttons('Neu indexieren')[0].disabled).toBe(true);
  });

  it('deletes only after the name was typed', async () => {
    await open();
    page.askDelete(page.collections()[0]);                                // "bent", not managed here
    fixture.detectChanges();
    const del = dialogButton('Löschen');
    expect(del.disabled).toBe(true);
    const input = el.querySelector('dialog input') as HTMLInputElement;
    input.value = 'ben';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    expect(del.disabled).toBe(true);
    input.value = 'bent';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    expect(del.disabled).toBe(false);
    del.click();
    const req = http.expectOne((r) => r.url === '/api/v1/rag/collections/bent');
    expect(req.request.method).toBe('DELETE');
    expect(req.request.params.get('confirm')).toBe('bent');
    req.flush(null, { status: 204, statusText: 'No Content' });
    await settle();
    http.expectOne(OVERVIEW).flush(ragOverview({ collections: [ragOverview().collections[1]] }));
    await render();
    expect(page.confirmDelete()).toBeNull();
    expect(el.querySelectorAll('tbody tr').length).toBe(1);
  });
});
