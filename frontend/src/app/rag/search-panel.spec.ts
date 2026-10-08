import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';

import { ClipboardService } from '../core/clipboard.service';
import { SearchPanel } from './search-panel';

const settle = () => new Promise((r) => setTimeout(r, 0));
const SEARCH = '/api/v1/rag/search';

describe('RAG test search', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<SearchPanel>;
  let panel: SearchPanel;
  let el: HTMLElement;
  let copied: string[];

  function open(collections = ['bent', 'bent_php'], preferred: string | null = 'bent_php', available = true) {
    copied = [];
    TestBed.configureTestingModule({
      imports: [SearchPanel],
      providers: [provideHttpClient(), provideHttpClientTesting(),
        { provide: ClipboardService, useValue: { copy: async (t: string) => (copied.push(t), true) } }],
    });
    http = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(SearchPanel);
    panel = fixture.componentInstance;
    el = fixture.nativeElement;
    fixture.componentRef.setInput('collections', collections);
    fixture.componentRef.setInput('preferred', preferred);
    fixture.componentRef.setInput('model', 'nomic-embed-text');
    fixture.componentRef.setInput('available', available);
    fixture.detectChanges();
  }

  function type(text: string) {
    const input = el.querySelector('input[type=search]') as HTMLInputElement;
    input.value = text;
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
  }

  const submit = () => el.querySelector('button[type=submit]') as HTMLButtonElement;

  afterEach(() => http.verify());

  it('starts on the preferred collection and needs a question', () => {
    open();
    expect(panel.collection()).toBe('bent_php');
    expect(submit().disabled).toBe(true);
    type('  ');
    expect(submit().disabled).toBe(true);
    type('Wo wird das JWT geprüft?');
    expect(submit().disabled).toBe(false);
  });

  it('searches, shows score, file and text, and copies the MCP answer', async () => {
    open();
    type(' Wo wird das JWT geprüft? ');
    submit().click();
    const req = http.expectOne(SEARCH);
    expect(req.request.body).toEqual({ collection: 'bent_php', query: 'Wo wird das JWT geprüft?', limit: 8 });
    req.flush({ collection: 'bent_php', query: 'x', model: 'nomic-embed-text', embedMs: 120.4, searchMs: 3.2,
      hits: [{ id: 'a', score: 0.7312, filename: 'src/Auth/JWT.php', text: '<?php class JWT {}', truncated: true }] });
    await settle();
    fixture.detectChanges();
    expect(el.textContent).toContain('1 Treffer');
    expect(el.textContent).toContain('0,73');
    expect(el.textContent).toContain('src/Auth/JWT.php');
    expect(el.textContent).toContain('abgeschnitten');
    expect(el.querySelector('pre')?.textContent).toBe('<?php class JWT {}');

    const copy = [...el.querySelectorAll('button')].find((b) => b.textContent?.includes('MCP-Antwort')) as HTMLButtonElement;
    copy.click();
    await settle();
    fixture.detectChanges();
    expect(copied).toEqual(['Datei: src/Auth/JWT.php\n<?php class JWT {}']);
    expect(copy.textContent).toContain('Kopiert');
  });

  it('shows the error the backend names', async () => {
    open();
    type('x');
    void panel.run();
    http.expectOne(SEARCH).flush({ detail: 'Ollama nicht erreichbar (http://127.0.0.1:11434): ConnectError' },
      { status: 503, statusText: 'Service Unavailable' });
    await settle();
    fixture.detectChanges();
    expect(el.querySelector('[role=alert]')?.textContent).toContain('Ollama nicht erreichbar');
  });

  it('clamps the number of hits and stays off while Qdrant is down', () => {
    open(['bent_php'], null, false);
    panel.setLimit('500');
    expect(panel.limit()).toBe(50);
    panel.setLimit('0');
    expect(panel.limit()).toBe(1);
    type('x');
    expect(submit().disabled).toBe(true);
  });

  it('follows the preferred collection until the user picks one', () => {
    open(['bent'], null);
    expect(panel.collection()).toBe('bent');
    fixture.componentRef.setInput('collections', ['bent', 'bent_php']);   // first reindex created it
    fixture.componentRef.setInput('preferred', 'bent_php');
    fixture.detectChanges();
    expect(panel.collection()).toBe('bent_php');
    const select = el.querySelector('select') as HTMLSelectElement;
    select.value = 'bent';
    select.dispatchEvent(new Event('change'));
    fixture.componentRef.setInput('collections', ['bent', 'bent_php', 'typo3']);
    fixture.detectChanges();
    expect(panel.collection()).toBe('bent');
  });

  it('follows a deleted collection', () => {
    open();
    fixture.componentRef.setInput('collections', ['bent']);
    fixture.detectChanges();
    expect(panel.collection()).toBe('bent');
  });
});
