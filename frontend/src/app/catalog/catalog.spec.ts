import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';

import { BenchStatus } from '../api/models/bench-status';
import { CatalogOverview } from '../api/models/catalog-overview';
import { StreamService } from '../core/stream.service';
import '../testing/dialog-polyfill';
import { ClipboardService } from '../core/clipboard.service';
import { benchStatus, candidate, catalogModel, catalogOverview, opencodeFit, preflight } from '../testing/catalog-overview';
import { FakeEventSource, fakeEventSourceProvider } from '../testing/fake-event-source';
import { Catalog } from './catalog';
import { BENCH_TOPIC, OVERVIEW_TOPIC } from './state';

const settle = () => new Promise((r) => setTimeout(r, 0));
const OVERVIEW = '/api/v1/catalog/overview';
const REFRESH = '/api/v1/catalog/refresh';
const PREFLIGHT = '/api/v1/catalog/preflight';
const BENCH = '/api/v1/catalog/bench';
const USAGE = '/api/v1/catalog/usage';
const CANDIDATES = '/api/v1/catalog/candidates';

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

  it('shows what the runner holds beyond Ollama\'s count, measured by a test run', async () => {
    const GIB = 1024 ** 3;
    const vis = catalogModel({
      verdict: { state: 'fits', basis: 'measured', needBytes: 6.7 * GIB, availableBytes: 14.15 * GIB,
        extraBytes: 0.75 * GIB, message: 'Läuft komplett auf der GPU (beobachtet).' },
      overhead: { measuredBytes: 1.2 * GIB, reserveBytes: 0.45 * GIB, extraBytes: 0.75 * GIB,
        model: 'qwen3.5-9b-64k-code:latest', measuredAt: '2026-10-08T15:24:00Z' },
    });
    await open(catalogOverview({ models: [vis], groups: [{ origin: 'qwen3.5:9b', installed: true, members: ['qwen3.5:9b'] }] }));
    expect(rows()[0].textContent).toContain('6,7 GiB');
    expect(rows()[0].textContent).toContain('+0,8');
    rows()[0].querySelector<HTMLButtonElement>('app-menu button')!.click();
    await render();
    button('Details', rows()[0]).click();
    await render();
    const text = (el.querySelector('dialog[open]')?.textContent ?? '').replace(/\s+/g, ' ');
    expect(text).toContain('belegt der Runner 1,2 GiB');
    expect(text).toContain('Testlauf von qwen3.5-9b-64k-code:latest, gleiche Gewichte');
    expect(text).toContain('Die Reserve deckt 0,45 GiB davon, + 0,8 GiB zählt die Prognose dazu.');
  });

  it('shows the GPU budget the verdicts compare against', async () => {
    await open();
    expect(el.textContent).toContain('GPU-Budget');
    expect(el.textContent).toContain('andere Programme');
    expect(el.textContent).toMatch(/1,4 GiB \(gemessen (\d\d\.\d\d\. )?\d\d:\d\d\)/);   // date only when not today
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

  it('counts the measured extra in the test dialog as well', async () => {
    await open();
    await openTest();
    preflightReq().flush(preflight({ verdict: { ...catalogModel().verdict, extraBytes: 0.75 * 1024 ** 3 } }));
    await render();
    expect(dialogOpen().textContent).toContain('+0,8');
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

  // ---- usage and OpenCode ------------------------------------------------------------------------

  const tagged = () => catalogOverview({
    models: [
      catalogModel({ usage: { tags: ['opencode'], note: 'Standard für OpenCode', derived: [] }, opencode: opencodeFit() }),
      catalogModel({ name: 'old:1', origin: 'old:1', usage: { tags: ['remove'], derived: [] },
        opencode: opencodeFit({ state: 'no', reasons: ['Nicht geeignet für OpenCode', 'Tool-Calls: 0/3 – nur als Text.'] }) }),
      catalogModel({ name: 'nomic-embed-text:latest', origin: 'nomic-embed-text:latest', testable: false,
        usage: { tags: [], derived: [{ tag: 'rag', source: 'RAG-Modul (embedding_model)' }] } }),
    ],
    groups: [{ origin: 'qwen3.5:9b', installed: true, members: ['qwen3.5:9b'] },
      { origin: 'old:1', installed: true, members: ['old:1'] },
      { origin: 'nomic-embed-text:latest', installed: true, members: ['nomic-embed-text:latest'] }],
  });

  it('shows what each model is used for and filters by it', async () => {
    await open(tagged());
    const [code, old, embed] = rows();
    const tag = code.querySelector('.bg-sky-400\\/10') as HTMLElement;      // used in OpenCode and checked: one chip
    expect(tag.textContent).toBe('OpenCode✓');                           // mark spaced by margin, not text
    expect(tag.title).toContain('Geeignet für OpenCode');
    expect(code.textContent).not.toContain('OpenCode ✓OpenCode');
    expect(code.textContent).toContain('Standard für OpenCode');
    expect(old.textContent).toContain('Löschkandidat');
    expect(old.textContent).toContain('OpenCode ✗');
    expect(embed.querySelector('[title="laut RAG-Modul (embedding_model)"]')?.textContent).toBe('RAG');
    expect(button('Im Einsatz (2)')).toBeTruthy();                       // the deletion candidate does not count
    button('Ohne Einsatz (1)').click();
    await render();
    expect(rows().map((r) => r.querySelector('.font-mono')?.textContent)).toEqual(['old:1']);
    expect(button('Ohne Einsatz (1)').getAttribute('aria-pressed')).toBe('true');
    button('Im Einsatz (2)').click();
    await render();
    expect(rows().length).toBe(2);
    button('Alle (3)').click();
    await render();
    expect(rows().length).toBe(3);
  });

  it('sets the usage of a model from its menu', async () => {
    await open();
    rows()[0].querySelector<HTMLButtonElement>('app-menu button')!.click();
    await render();
    button('Einsatz', rows()[0]).click();
    await render();
    const dialog = el.querySelector('dialog[open]') as HTMLDialogElement;
    expect(dialog.textContent).toContain('Einsatz: qwen3.5:9b');
    const boxes = [...dialog.querySelectorAll<HTMLInputElement>('input[type=checkbox]')];
    expect(boxes.length).toBe(5);
    boxes[1].checked = true;                                             // OpenWebUI
    boxes[1].dispatchEvent(new Event('change'));
    boxes[0].checked = true;                                             // OpenCode - order is fixed anyway
    boxes[0].dispatchEvent(new Event('change'));
    const note = dialog.querySelector<HTMLInputElement>('input[type=text]')!;
    note.value = '  für das Team  ';
    note.dispatchEvent(new Event('input'));
    button('Speichern', dialog).click();
    await render();
    const req = http.expectOne(USAGE);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ name: 'qwen3.5:9b', tags: ['opencode', 'openwebui'], note: 'für das Team' });
    req.flush(catalogOverview({ asOf: '2026-10-09T09:00:00Z' }));
    await render();
    expect(el.querySelector('dialog[open]')).toBeNull();
  });

  it('keeps the usage dialog open with the error when saving fails', async () => {
    await open();
    rows()[0].querySelector<HTMLButtonElement>('app-menu button')!.click();
    await render();
    button('Einsatz', rows()[0]).click();
    await render();
    button('Speichern', el.querySelector('dialog[open]')!).click();
    http.expectOne(USAGE).flush({ detail: 'Modell nicht installiert: qwen3.5:9b' }, { status: 404, statusText: 'x' });
    await render();
    expect(el.querySelector('dialog[open] [role=alert]')?.textContent).toContain('Modell nicht installiert');
  });

  it('explains the OpenCode fit in the details and copies the opencode.json entry', async () => {
    await open(tagged());
    const copied: string[] = [];
    TestBed.inject(ClipboardService).copy = async (t: string) => { copied.push(t); return true; };
    rows()[0].querySelector<HTMLButtonElement>('app-menu button')!.click();
    await render();
    button('Details', rows()[0]).click();
    await render();
    const dialog = el.querySelector('dialog[open]') as HTMLDialogElement;
    expect(dialog.textContent).toContain('Geeignet für OpenCode');
    expect(dialog.textContent).toContain('Tool-Calls: 3/3 strukturiert.');
    expect(dialog.textContent).toContain('125,1 tok/s');
    expect(dialog.querySelector('pre')?.textContent).toContain('"limit": { "context": 65536, "output": 8192 }');
    button('Kopieren', dialog).click();
    await render();
    expect(copied[0]).toContain('"name": "qwen3.5:9b"');
    expect(button('Kopiert', dialog)).toBeTruthy();
  });

  it('lists the tool calls of a finished test run', async () => {
    await open();
    await openTest();
    preflightReq().flush(preflight());
    await render();
    const dialog = dialogOpen();
    button('Testlauf starten', dialog).click();
    http.expectOne(BENCH).flush(benchStatus({ state: 'queued', revision: 1, result: null, finishedAt: null }));
    await render();
    pushBench(benchStatus({ revision: 9, result: { ...benchStatus().result!,
      tools: { passed: 2, total: 3, cases: [
        { key: 'read', label: 'Datei lesen', ok: true, detail: "read_file(path='src/app/app.config.ts')", seconds: 1.4 },
        { key: 'choose', label: 'Werkzeug wählen', ok: true, detail: "run_command(command='npm test')", seconds: 1.1 },
        { key: 'types', label: 'Argumente mit Typen', ok: false, detail: "Argumente falsch: max_results = '5' (Text statt Zahl)",
          seconds: 1.2, thinking: true }] } } }));
    await render();
    const text = dialog.textContent ?? '';
    expect(text).toContain('Tool-Calls · 2/3 strukturiert');
    expect(text).toContain("read_file(path='src/app/app.config.ts')");
    expect(text).toContain('(Text statt Zahl)');
    expect(text).toContain('1,2 s · denkt vorher');
    expect(dialog.querySelectorAll('li app-icon.text-red-400').length).toBe(1);
    pushBench(benchStatus({ revision: 10, result: { ...benchStatus().result!,
      tools: { passed: 0, total: 3, skipped: 'Ollama lehnt Tools für dieses Modell ab (Template ohne Tool-Format).', cases: [] } } }));
    await render();
    expect(dialog.textContent).toContain('Tool-Calls · nicht geprüft');
    expect(dialog.textContent).toContain('Template ohne Tool-Format');
  });

  // ---- candidates ---------------------------------------------------------------------------------

  const later = (over: Partial<CatalogOverview> = {}) => catalogOverview({ asOf: '2026-10-08T09:10:00Z', ...over });
  const cards = () => [...el.querySelectorAll('app-catalog-candidate-card')] as HTMLElement[];

  async function candidatesView(ov: CatalogOverview = catalogOverview()) {
    await open(ov);
    button('Kandidaten').click();
    await render();
  }

  function type(text: string) {
    const input = el.querySelector('app-catalog-candidates input') as HTMLInputElement;
    input.value = text;
    input.dispatchEvent(new Event('input'));
  }

  it('checks a candidate before the download and shows up to which context it fits', async () => {
    await candidatesView();
    expect(button('Kandidaten').getAttribute('aria-pressed')).toBe('true');
    expect(el.querySelector('app-catalog-model-row')).toBeNull();
    expect(el.textContent).toContain('Noch kein Kandidat geprüft.');
    expect(el.textContent).toContain('Erlaubte Registries: registry.ollama.ai, hf.co.');
    expect(button('Prüfen').disabled).toBe(true);                      // nothing typed yet
    type('ollama pull qwen3:14b');
    await render();
    button('Prüfen').click();
    await render();
    const req = http.expectOne(CANDIDATES);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ name: 'ollama pull qwen3:14b' });
    expect(button('Prüft').disabled).toBe(true);
    req.flush({ name: 'qwen3:14b', overview: later({ candidates: [candidate()] }) });
    await render();
    const card = cards()[0];
    const text = card.textContent ?? '';
    expect(card.querySelector('h3')?.textContent?.trim()).toBe('qwen3:14b');
    expect(text).toContain('qwen3 · 14.8B · Q4_K_M');
    expect(text).toContain('Tools');
    expect(text).toContain('Teil-Offload');
    expect(text).toContain('8,6 GiB');                                   // download
    expect(text).toContain('trainiert 131.072');
    expect(text).toContain('≈ 15,2 GiB');
    expect([...card.querySelectorAll('ul[aria-label] li')].map((li) => li.textContent?.trim())).toEqual(
      ['8k ✓', '32k ✓', '48k !', '64k ✗', '128k ✗']);
    expect(card.querySelector('ul[aria-label] li')?.getAttribute('title')).toBe('8.192 Token: ≈ 9,7 GiB – passt');
    expect(text).toContain('Passt bis 32.768 Token, knapp bis 49.152 – darüber kippt es in den Teil-Offload.');
    expect(text).toContain('OpenCode ✗');
    expect(text).toContain('Tool-Calls erst nach dem Download messbar');
    expect(text).toContain('ollama pull qwen3:14b');
    expect(text).toContain('Metadaten 78 KB gelesen');
    expect((el.querySelector('app-catalog-candidates input') as HTMLInputElement).value).toBe('');   // ready for the next
    expect(button('Kandidaten').textContent).toContain('· 1');
    expect(card.querySelector('[role=note]')).toBeNull();              // a normal estimate: no "Ungenau"
  });

  it('says when the formula is rough and shows what it worked with', async () => {
    const rough = candidate({ name: 'gemma4:latest',
      estimate: { ...candidate().estimate!, confidence: 'low', calibrated: null,
        notes: ['48 Schichten × 8 KV-Köpfe × 256+256 × q8_0',
          'Sliding Window mit unbekanntem Schichtplan – volle Länge gerechnet (zu hoch)'] },
      modelInfo: { 'general.architecture': 'gemma4', 'gemma4.attention.sliding_window': 512 } });
    await candidatesView(catalogOverview({ candidates: [rough] }));
    const card = cards()[0];
    expect(card.querySelector('[role=note]')?.textContent).toContain('Ungenau:');
    const folded = card.querySelector('details') as HTMLDetailsElement;
    expect(folded.querySelector('summary')?.textContent).toContain('Rechnung und Modelldaten');
    expect(folded.textContent).toContain('Sliding Window mit unbekanntem Schichtplan');
    expect(folded.textContent).toContain('gemma4.attention.sliding_window');
    expect(folded.textContent).toContain('2 Einträge');
  });

  it('keeps the name and shows why a check failed', async () => {
    await candidatesView();
    type('evil.example/x/y');
    await render();
    button('Prüfen').click();
    http.expectOne(CANDIDATES).flush({ detail: 'Registry „evil.example“ ist nicht freigegeben.' },
      { status: 400, statusText: 'Bad Request' });
    await render();
    expect(el.querySelector('app-catalog-candidates [role=alert]')?.textContent).toContain('nicht freigegeben');
    expect((el.querySelector('app-catalog-candidates input') as HTMLInputElement).value).toBe('evil.example/x/y');
  });

  it('warns about the Ollama version, says what is installed, checks again and removes', async () => {
    const old = candidate({ name: 'qwen3.5:9b', pull: 'ollama pull qwen3.5:9b', requires: '0.17.1', requiresOk: false,
      verdict: { ...candidate().verdict!, state: 'fits', needBytes: 6.7 * 1024 ** 3, extraBytes: 0.75 * 1024 ** 3 },
      notes: ['Braucht Ollama ≥ 0.17.1, installiert ist 0.12.6 – erst Ollama aktualisieren.',
        'Schon installiert – mit genau diesen Gewichten.'], installed: true, simulated: true });
    await candidatesView(catalogOverview({ candidates: [old] }));
    const card = cards()[0];
    expect(card.querySelector('[role=note]')?.textContent).toContain('Version: Braucht Ollama ≥ 0.17.1');
    expect(card.textContent).toContain('Schon installiert – mit genau diesen Gewichten.');
    expect(card.querySelector('dl')?.textContent?.replace(/\s+/g, ' ')).toContain('≈ 6,7 GiB +0,8 von');   // extra of a test run
    expect(card.textContent).toContain('Simulation');
    card.querySelector<HTMLButtonElement>('app-menu button')!.click();
    await render();
    expect(card.querySelector('a[role=menuitem]')?.getAttribute('href')).toBe('https://ollama.com/library/qwen3:14b');
    button('Neu prüfen', card).click();
    await render();
    const again = http.expectOne(CANDIDATES);
    expect(again.request.body).toEqual({ name: 'qwen3.5:9b' });
    again.flush({ name: 'qwen3.5:9b', overview: later({ candidates: [old] }) });
    await render();
    cards()[0].querySelector<HTMLButtonElement>('app-menu button')!.click();
    await render();
    button('Aus der Liste nehmen', cards()[0]).click();
    const del = http.expectOne((r) => r.url === CANDIDATES && r.method === 'DELETE');
    expect(del.request.params.get('name')).toBe('qwen3.5:9b');
    del.flush(catalogOverview({ asOf: '2026-10-08T09:20:00Z', candidates: [] }));
    await render();
    expect(cards().length).toBe(0);
  });

  it('shows a cloud model as nothing for the card, and copies the pull command of the others', async () => {
    const cloud = candidate({ name: 'qwen3-coder:480b-cloud', weightsBytes: 0, downloadBytes: 268, verdict: null,
      context: null, estimate: null, steps: [], opencode: null, fitsUpTo: null, loadsUpTo: null,
      error: 'Cloud-Modell: läuft auf https://ollama.com:443, nicht auf dieser Karte.' });
    await candidatesView(catalogOverview({ candidates: [cloud, candidate()] }));
    const copied: string[] = [];
    TestBed.inject(ClipboardService).copy = async (t: string) => { copied.push(t); return true; };
    const [c, q] = cards();
    expect(c.textContent).toContain('Cloud-Modell: läuft auf https://ollama.com:443');
    expect(c.textContent).toContain('Cloud');
    expect(c.textContent).not.toContain('ollama pull');
    button('Kopieren', q).click();
    await render();
    expect(copied).toEqual(['ollama pull qwen3:14b']);
    expect(button('Kopiert', q)).toBeTruthy();
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
