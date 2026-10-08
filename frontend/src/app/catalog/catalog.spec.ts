import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';

import { BenchStatus } from '../api/models/bench-status';
import { CatalogOverview } from '../api/models/catalog-overview';
import { StreamService } from '../core/stream.service';
import '../testing/dialog-polyfill';
import { benchStatus, catalogModel, catalogOverview, preflight } from '../testing/catalog-overview';
import { FakeEventSource, fakeEventSourceProvider } from '../testing/fake-event-source';
import { Catalog } from './catalog';
import { BENCH_TOPIC, OVERVIEW_TOPIC } from './state';

const settle = () => new Promise((r) => setTimeout(r, 0));
const OVERVIEW = '/api/v1/catalog/overview';
const REFRESH = '/api/v1/catalog/refresh';
const PREFLIGHT = '/api/v1/catalog/preflight';
const BENCH = '/api/v1/catalog/bench';

describe('Catalog page', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<Catalog>;
  let el: HTMLElement;

  async function open(ov: CatalogOverview = catalogOverview()) {
    FakeEventSource.instances = [];
    TestBed.configureTestingModule({
      imports: [Catalog],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([]), fakeEventSourceProvider],
    });
    http = TestBed.inject(HttpTestingController);
    TestBed.inject(StreamService).connect();
    fixture = TestBed.createComponent(Catalog);
    el = fixture.nativeElement;
    fixture.detectChanges();
    http.expectOne(OVERVIEW).flush(ov);
    await render();
  }

  async function render() {
    await settle();
    fixture.detectChanges();
  }

  const rows = () => [...el.querySelectorAll('app-catalog-model-row')] as HTMLElement[];
  const button = (text: string, root: ParentNode = el) =>
    [...root.querySelectorAll('button')].find((b) => b.textContent?.trim().includes(text)) as HTMLButtonElement;
  const push = (ov: CatalogOverview) => {
    FakeEventSource.instances[0].emit(OVERVIEW_TOPIC, ov);
  };

  afterEach(() => http.verify());

  it('shows the server defaults, one card per origin and the tree inside it', async () => {
    await open();
    expect(el.textContent).toContain('Ollama 0.35.0');
    expect(el.textContent).toContain('aus server.log');
    expect(el.textContent).toContain('65.536');
    expect(el.querySelector('article h3')?.textContent?.trim()).toBe('qwen3.5:9b');
    const [base, derived] = rows();
    expect(base.textContent).toContain('qwen3.5:9b');
    expect(base.textContent).toContain('≈ 11,4 GiB');            // estimated: on Ollama's scale, with ≈
    expect(base.textContent).toContain('geschätzt');
    expect(derived.textContent).toContain('ändert: num_ctx 65536');
    expect(derived.textContent).toContain('geladen');
    expect(derived.textContent).toContain('11,3 GiB');             // measured: no ≈
    expect(derived.textContent).toContain('gemessen');
    expect(derived.textContent).not.toContain('≈');
    expect((derived.querySelector('[style]') as HTMLElement).style.paddingLeft).toBe('1.25rem');
  });

  it('says why a model will not fit, in the row and not only in a tooltip', async () => {
    const split = catalogModel({
      name: 'coder:14b', origin: 'coder:14b',
      verdict: { state: 'split', basis: 'estimated', needBytes: 15.5 * 1024 ** 3, availableBytes: 14 * 1024 ** 3,
        message: '≈ 15,5 GiB, die Karte lässt Ollama 14,0 GiB – Teil-Offload zu erwarten, Absturzgefahr.' },
    });
    await open(catalogOverview({ models: [split], groups: [{ origin: 'coder:14b', installed: true, members: ['coder:14b'] }] }));
    expect(rows()[0].textContent).toContain('Teil-Offload');
    expect(rows()[0].querySelector('p.text-red-300')?.textContent).toContain('Absturzgefahr');
  });

  it('groups models of a deleted parent under its name', async () => {
    const orphan = catalogModel({
      name: 'deepseek-coder-24k:latest', origin: 'deepseek-coder-v2:16b', depth: 1,
      parent: { declared: 'deepseek-coder-v2:16b', resolved: null, via: null, installed: false },
    });
    await open(catalogOverview({ models: [orphan],
      groups: [{ origin: 'deepseek-coder-v2:16b', installed: false, members: ['deepseek-coder-24k:latest'] }] }));
    expect(el.textContent).toContain('Ursprung nicht installiert');
    expect(rows()[0].textContent).toContain('erstellt aus deepseek-coder-v2:16b (nicht installiert)');
    const name = rows()[0].querySelector('.min-w-0') as HTMLElement;
    expect(['', '0rem', '0px']).toContain(name.style.paddingLeft);  // first level of a missing origin: no indent
  });

  it('accepts newer overviews from SSE and ignores late older ones', async () => {
    await open();
    push(catalogOverview({ asOf: '2026-10-08T09:01:00Z', models: [], groups: [] }));
    await render();
    expect(rows().length).toBe(0);
    expect(el.textContent).toContain('In Ollama ist kein Modell installiert.');
    push(catalogOverview({ asOf: '2026-10-08T08:59:00Z' }));        // late echo of an older state
    await render();
    expect(rows().length).toBe(0);
  });

  it('refreshes everything, and one model from its menu', async () => {
    await open();
    button('Alle neu einlesen').click();
    await render();
    expect(button('Liest …').disabled).toBe(true);
    const req = http.expectOne(REFRESH);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ name: null });
    req.flush(catalogOverview({ asOf: '2026-10-08T09:02:00Z' }));
    await render();
    expect(button('Alle neu einlesen').disabled).toBe(false);

    rows()[1].querySelector<HTMLButtonElement>('app-menu button')!.click();
    await render();
    button('Neu einlesen', rows()[1]).click();
    const one = http.expectOne(REFRESH);
    expect(one.request.body).toEqual({ name: 'qwen3.5-9b-64k:latest' });
    one.flush({ detail: 'Ollama nicht erreichbar: connection refused' }, { status: 503, statusText: 'x' });
    await render();
    expect(el.querySelector('[role=alert]')?.textContent).toContain('Ollama nicht erreichbar: connection refused');
  });

  it('opens the details of a model with estimate, observations and parameters', async () => {
    await open();
    rows()[1].querySelector<HTMLButtonElement>('app-menu button')!.click();
    await render();
    button('Details', rows()[1]).click();
    await render();
    const dialog = el.querySelector('dialog[open]') as HTMLDialogElement;
    expect(dialog).toBeTruthy();
    const text = dialog.textContent ?? '';
    expect(text).toContain('qwen3.5-9b-64k:latest');
    expect(text).toContain('erstellt aus qwen3.5:9b');
    expect(text).toContain('Das Modell setzt selbst num_ctx 65.536.');
    expect(text).toContain('= nach Formel');
    expect(text).toContain('Die Karte gibt Ollama 14,2 GiB');
    expect(text).toContain('Noch kein Testlauf.');
    expect(text).toContain('65.536 Token · 11,3 GiB · 100 % GPU');
    expect(text).toContain('3× geladen');
    expect(text).toContain('temperature');
    button('Schließen', dialog).click();
    await render();
    expect(dialog.open).toBe(false);
  });

  it('keeps the last known state when Ollama is down', async () => {
    await open(catalogOverview({ ollama: { online: false, error: 'connection refused', simulated: false } }));
    const alert = el.querySelector('[role=alert]')!;
    expect(alert.textContent).toContain('connection refused');
    expect(alert.textContent).toContain('zuletzt bekannte Stand');
    expect(el.textContent).toContain('Stand');
    expect(rows().length).toBe(2);
  });

  it('explains unknown server defaults and lists removed models', async () => {
    const ov = catalogOverview({
      server: { source: 'unknown', note: 'Ollama läuft auf 192.0.2.20 – sein Log ist von hier aus nicht lesbar.',
        overridden: [] },
      hardware: { note: 'Ollama läuft auf 192.0.2.20 – die GPU dort ist von hier aus nicht sichtbar.' },
      removed: [{ name: 'old:1', digest: 'd', removedAt: '2026-10-01T10:00:00Z', measurements: 2 }],
    });
    await open(ov);
    expect(el.textContent).toContain('sein Log ist von hier aus nicht lesbar');
    expect(el.textContent).toContain('die GPU dort ist von hier aus nicht sichtbar');
    expect(el.querySelector('pre')?.textContent).toContain('server_context_length = 65536');
    expect(el.textContent).toContain('Nicht mehr installiert');
    expect(el.textContent).toContain('2 Messwerte bleiben');
  });

  it('shows the GPU budget the verdicts compare against', async () => {
    await open();
    expect(el.textContent).toContain('GPU-Budget');
    expect(el.textContent).toContain('andere Programme');
    expect(el.textContent).toMatch(/1,4 GiB \(gemessen \d\d:\d\d\)/);
    expect(el.textContent).toContain('verfügbar');
    expect(el.textContent).toContain('14,2 GiB');
  });

  // ---- test runs ----------------------------------------------------------------------------

  const preflightReq = () => http.expectOne((r) => r.url === PREFLIGHT);
  const dialogOpen = () => el.querySelector('dialog[open]') as HTMLDialogElement;
  const pushBench = (b: BenchStatus) => FakeEventSource.instances[0].emit(BENCH_TOPIC, b);

  async function openTest(row = 0) {
    rows()[row].querySelector<HTMLButtonElement>('app-menu button')!.click();
    await render();
    button('Testlauf', rows()[row]).click();
    await render();
  }

  it('checks the cost before a test run and follows the run phase by phase', async () => {
    await open();
    await openTest();
    expect(dialogOpen().textContent).toContain('Testlauf: qwen3.5:9b');
    const first = preflightReq();
    expect(first.request.params.get('name')).toBe('qwen3.5:9b');
    expect(first.request.params.has('num_ctx')).toBe(false);       // the effective context
    first.flush(preflight());
    await render();
    const dialog = dialogOpen();
    expect(dialog.textContent).toContain('≈ 11,4 GiB');
    expect(dialog.textContent).toContain('von 14,0 GiB');
    expect(dialog.textContent).toContain('passt');
    expect(dialog.textContent).toContain('65.536 (wirksam)');

    const select = dialog.querySelector('select') as HTMLSelectElement;
    select.value = '8192';
    select.dispatchEvent(new Event('change'));
    await render();
    expect(button('Testlauf starten', dialog).disabled).toBe(true);  // until the new answer is in
    const second = preflightReq();
    expect(second.request.params.get('num_ctx')).toBe('8192');
    second.flush(preflight({ context: { ...preflight().context, effective: 8192, source: 'request' } }));
    await render();

    button('Testlauf starten', dialog).click();
    await render();
    const start = http.expectOne(BENCH);
    expect(start.request.method).toBe('POST');
    expect(start.request.body).toEqual({ name: 'qwen3.5:9b', numCtx: 8192, confirm: false });
    start.flush(benchStatus({ state: 'queued', revision: 1, result: null, finishedAt: null }), { status: 202, statusText: 'x' });
    await render();
    expect(dialog.textContent).toContain('läuft …');

    pushBench(benchStatus({ state: 'running', phase: 'load', revision: 3, result: null, finishedAt: null }));
    await render();
    const now = [...dialog.querySelectorAll('li span.text-white')].map((s) => s.textContent?.trim());
    expect(now).toEqual(['Laden und antworten']);
    expect(rows()[0].textContent).toContain('Testlauf: Laden und antworten …');    // the row follows via SSE
    rows()[1].querySelector<HTMLButtonElement>('app-menu button')!.click();
    await render();
    expect(button('Testlauf', rows()[1]).disabled).toBe(true);                     // one run at a time
    expect(button('Testlauf', rows()[1]).title).toBe('Es läuft schon ein Testlauf');

    pushBench(benchStatus({ revision: 7 }));
    await render();
    expect(dialog.textContent).toContain('Gemessen');
    expect(dialog.textContent).toContain('64,0 tok/s');
    expect(dialog.textContent).toContain('7,1 GiB laut Ollama');
    expect(dialog.textContent).toContain('0,3 GiB mehr als Ollama zählt');
    pushBench(benchStatus({ state: 'running', phase: 'measure', revision: 5 }));    // late echo: ignored
    await render();
    expect(dialog.textContent).toContain('Gemessen');

    button('Neuer Testlauf', dialog).click();
    await render();
    preflightReq().flush(preflight());
    await render();
    expect(button('Testlauf starten', dialog)).toBeTruthy();
  });

  it('wants a tick for "knapp" and refuses "Teil-Offload"', async () => {
    await open();
    await openTest();
    const tight = catalogModel().verdict;
    preflightReq().flush(preflight({ needsConfirm: true, reason: 'knapp',
      verdict: { ...tight, state: 'tight', needBytes: 13.5 * 1024 ** 3, message: 'Knapp: wenig Luft.' } }));
    await render();
    const dialog = dialogOpen();
    expect(button('Testlauf starten', dialog).disabled).toBe(true);
    const tick = dialog.querySelector('input[type=checkbox]') as HTMLInputElement;
    tick.checked = true;
    tick.dispatchEvent(new Event('change'));
    await render();
    expect(button('Testlauf starten', dialog).disabled).toBe(false);
    push(catalogOverview({ asOf: '2026-10-08T09:03:00Z' }));     // same model, new object: no re-check, tick stays
    await render();
    expect(button('Testlauf starten', dialog).disabled).toBe(false);
    button('Testlauf starten', dialog).click();
    const start = http.expectOne(BENCH);
    expect(start.request.body).toEqual({ name: 'qwen3.5:9b', numCtx: 65536, confirm: true });
    start.flush({ detail: 'Es läuft schon ein Testlauf – erst abwarten.' }, { status: 409, statusText: 'x' });
    await render();
    expect(dialog.querySelector('[role=alert]')?.textContent).toContain('Es läuft schon ein Testlauf');

    const select = dialog.querySelector('select') as HTMLSelectElement;
    select.value = '131072';
    select.dispatchEvent(new Event('change'));
    await render();
    preflightReq().flush(preflight({ allowed: false, reason: '≈ 16,9 GiB – Teil-Offload. Einen kleineren Kontext wählen.',
      verdict: { ...tight, state: 'split', needBytes: 16.9 * 1024 ** 3, message: 'Teil-Offload' } }));
    await render();
    expect(dialog.textContent).toContain('Einen kleineren Kontext wählen.');
    expect(dialog.querySelector('input[type=checkbox]')).toBeNull();
    expect(button('Testlauf starten', dialog).disabled).toBe(true);
  });

  it('warns which models a test run unloads', async () => {
    await open();
    await openTest();
    preflightReq().flush(preflight({ willUnload: ['qwen3.5-9b-64k:latest'] }));
    await render();
    expect(dialogOpen().textContent).toContain('Entlädt qwen3.5-9b-64k:latest');
  });

  it('locks test runs where they are not allowed', async () => {
    const embed = catalogModel({ name: 'nomic-embed-text:latest', origin: 'nomic-embed-text:latest', testable: false });
    await open(catalogOverview({
      tests: { allowed: false, reason: 'Testläufe gesperrt: Ollama läuft auf 192.0.2.20.' },
      models: [catalogModel(), embed],
      groups: [{ origin: 'qwen3.5:9b', installed: true, members: ['qwen3.5:9b'] },
        { origin: 'nomic-embed-text:latest', installed: true, members: ['nomic-embed-text:latest'] }],
    }));
    rows()[0].querySelector<HTMLButtonElement>('app-menu button')!.click();
    await render();
    const locked = button('Testlauf', rows()[0]);
    expect(locked.disabled).toBe(true);
    expect(locked.title).toContain('Ollama läuft auf 192.0.2.20');
    rows()[1].querySelector<HTMLButtonElement>('app-menu button')!.click();
    await render();
    expect(button('Testlauf', rows()[1]).title).toContain('Einbettungsmodelle');
  });

  it('shows a test run another tab started, and the history in the details', async () => {
    await open();
    pushBench(benchStatus({ state: 'running', phase: 'baseline', revision: 2, result: null, finishedAt: null }));
    await render();
    expect(rows()[0].textContent).toContain('Testlauf: Leere Karte messen …');
    push(catalogOverview({ asOf: '2026-10-08T09:06:00Z',
      models: [catalogModel({ benches: [benchStatus()] }), catalogOverview().models[1]] }));
    pushBench(benchStatus({ revision: 7 }));
    await render();
    expect(rows()[0].textContent).toContain('64,0 tok/s · Laden 4,5 s · 8.192 Token');
    rows()[0].querySelector<HTMLButtonElement>('app-menu button')!.click();
    await render();
    button('Details', rows()[0]).click();
    await render();
    expect(dialogOpen().textContent).toContain('Testläufe');
    expect(dialogOpen().textContent).toContain('64,0 tok/s · Laden 4,5 s · 8.192 Token');
  });

  it('shows the load error instead of an empty page', async () => {
    FakeEventSource.instances = [];
    TestBed.configureTestingModule({
      imports: [Catalog],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([]), fakeEventSourceProvider],
    });
    http = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(Catalog);
    el = fixture.nativeElement;
    fixture.detectChanges();
    http.expectOne(OVERVIEW).flush({ detail: 'Katalog-Modul läuft nicht' }, { status: 503, statusText: 'x' });
    await render();
    expect(el.querySelector('[role=alert]')?.textContent).toContain('Das Katalog-Modul läuft nicht');
  });
});
