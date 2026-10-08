import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';

import { CatalogOverview } from '../api/models/catalog-overview';
import { StreamService } from '../core/stream.service';
import '../testing/dialog-polyfill';
import { catalogModel, catalogOverview } from '../testing/catalog-overview';
import { FakeEventSource, fakeEventSourceProvider } from '../testing/fake-event-source';
import { Catalog } from './catalog';
import { OVERVIEW_TOPIC } from './state';

const settle = () => new Promise((r) => setTimeout(r, 0));
const OVERVIEW = '/api/v1/catalog/overview';
const REFRESH = '/api/v1/catalog/refresh';

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
      verdict: { state: 'split', basis: 'estimated', needBytes: 15.5 * 1024 ** 3, expectedBytes: 16.7 * 1024 ** 3,
        vramTotalBytes: 16 * 1024 ** 3, message: '≈ 16,7 GiB von 16,0 GiB – Teil-Offload zu erwarten, Absturzgefahr.' },
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
    const dialog = el.querySelector('dialog') as HTMLDialogElement;
    expect(dialog.open).toBe(true);
    const text = dialog.textContent ?? '';
    expect(text).toContain('qwen3.5-9b-64k:latest');
    expect(text).toContain('erstellt aus qwen3.5:9b');
    expect(text).toContain('Das Modell setzt selbst num_ctx 65.536.');
    expect(text).toContain('= auf der Karte');
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
