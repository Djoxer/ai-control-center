import '../testing/dialog-polyfill';

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';

import { ExportDocument } from '../api/models/export-document';
import { ClipboardService } from '../core/clipboard.service';
import { ExportDialog } from './export-dialog';

const settle = () => new Promise((r) => setTimeout(r, 0));
const URL_EXPORT = '/api/v1/dashboard/export';

const doc = (content: string, over: Partial<ExportDocument> = {}): ExportDocument => ({
  format: 'md', detail: 'short', filename: 'acc-export-2026-10-07-1403.md', mediaType: 'text/markdown; charset=utf-8',
  tokens: Math.ceil(content.length / 4), content, ...over,
});

describe('ExportDialog', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<ExportDialog>;
  let dialog: ExportDialog;
  let copied: string[];

  const el = () => fixture.nativeElement as HTMLElement;
  /** Open export requests - match() also removes them from the controller's list. */
  const pending = (): TestRequest[] => http.match((r) => r.url === URL_EXPORT);
  const button = (label: string) =>
    [...el().querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent?.trim() === label)!;
  const render = async () => {
    await settle();
    fixture.detectChanges();
  };

  beforeEach(() => {
    copied = [];
    TestBed.configureTestingModule({
      imports: [ExportDialog],
      providers: [
        provideHttpClient(), provideHttpClientTesting(),
        { provide: ClipboardService, useValue: { copy: async (t: string) => (copied.push(t), true) } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(ExportDialog);
    dialog = fixture.componentInstance;
    fixture.componentRef.setInput('open', false);
    fixture.detectChanges();
  });

  afterEach(() => http.verify());

  async function openWith(content = '---\nformat: acc-export/v1\n---\n# AI-Rechner') {
    fixture.componentRef.setInput('open', true);
    fixture.detectChanges();
    const [req] = pending();
    req.flush(doc(content));
    await render();
    return req;
  }

  it('fetches nothing while closed', () => {
    expect(pending()).toEqual([]);
  });

  it('asks for anonymized short Markdown with everything when opened and shows it', async () => {
    const req = await openWith();
    const p = req.request.params;
    expect([p.get('format'), p.get('detail'), p.get('range'), p.get('anonymize')]).toEqual(['md', 'short', '1h', 'true']);
    expect(p.getAll('parts')).toEqual(['snapshot', 'events', 'history']);
    expect(el().querySelector('pre')?.textContent).toContain('format: acc-export/v1');
    expect(el().textContent).toContain('≈ 11 Tokens');
  });

  it('asks again on every option and ignores answers of outdated requests', async () => {
    await openWith();
    button('JSON').click();
    fixture.detectChanges();
    button('Ausführlich').click();
    fixture.detectChanges();
    const [first, second] = pending();
    expect(first.request.params.get('format')).toBe('json');
    expect(second.request.params.get('detail')).toBe('full');

    second.flush(doc('{"newest": true}', { format: 'json', detail: 'full' }));
    first.flush(doc('{"older": true}', { format: 'json' }));          // arrives late
    await render();
    expect(el().querySelector('pre')?.textContent).toBe('{"newest": true}');
  });

  it('leaves out unchecked parts, and the time span goes with the history', async () => {
    await openWith();
    const boxes = [...el().querySelectorAll<HTMLInputElement>('fieldset input[type="checkbox"]')];
    boxes[2].click();                                                  // Verlauf off
    fixture.detectChanges();
    const [req] = pending();                                           // match() takes requests off the list
    expect(req.request.params.getAll('parts')).toEqual(['snapshot', 'events']);
    req.flush(doc('x'));
    await render();
    expect(el().querySelector('[aria-label="Zeitraum Verlauf"]')).toBeNull();
  });

  it('asks nothing and offers nothing when no part is chosen', async () => {
    await openWith();
    for (const box of el().querySelectorAll<HTMLInputElement>('fieldset input[type="checkbox"]')) {
      box.click();
      fixture.detectChanges();
      pending().forEach((r) => r.flush(doc('x')));
    }
    await render();
    expect(dialog.nothingChosen()).toBe(true);
    expect(el().textContent).toContain('Mindestens einen Inhalt wählen');
    expect(button('Kopieren').disabled).toBe(true);
    expect(button('Speichern').disabled).toBe(true);
  });

  it('switches anonymizing off on request', async () => {
    await openWith();
    const boxes = el().querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
    boxes[boxes.length - 1].click();                                   // the one below the parts
    fixture.detectChanges();
    const [req] = pending();
    expect(req.request.params.get('anonymize')).toBe('false');
    req.flush(doc('x'));
  });

  it('copies the text and says so', async () => {
    await openWith('# Inhalt');
    button('Kopieren').click();
    await render();
    expect(copied).toEqual(['# Inhalt']);
    expect(el().textContent).toContain('In die Zwischenablage kopiert');
  });

  it('saves the text under the file name from the backend', async () => {
    await openWith('# Inhalt');
    const created = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:x');
    const revoked = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const names: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      names.push(this.download);
    });
    try {
      button('Speichern').click();
      await render();
      expect(names).toEqual(['acc-export-2026-10-07-1403.md']);
      expect(el().textContent).toContain('Gespeichert: acc-export-2026-10-07-1403.md');
    } finally {
      created.mockRestore();
      revoked.mockRestore();
      click.mockRestore();
    }
  });

  it('shows why the export failed', async () => {
    fixture.componentRef.setInput('open', true);
    fixture.detectChanges();
    pending()[0].flush({ detail: 'dashboard module is not running' }, { status: 503, statusText: 'Unavailable' });
    await render();
    expect(el().querySelector('[role="alert"]')?.textContent).toContain('dashboard module is not running');
    expect(button('Kopieren').disabled).toBe(true);
  });
});
