import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import { Changelog } from '../api/models/changelog';
import { HelpDoc } from '../api/models/help-doc';
import { Help } from './help';

const settle = () => new Promise((r) => setTimeout(r, 0));

const DOCS: HelpDoc[] = [
  { key: 'core', title: 'Allgemein', moduleState: null, markdown: 'Kontrollebene.\n\n## Bedienung\n\n### Aufbau\n\nText' },
  { key: 'dashboard', title: 'Übersicht (Dashboard)', moduleState: 'running', markdown: '## Bedienung\n\nKacheln' },
  { key: 'logs', title: 'Protokoll (Logs)', moduleState: 'failed', markdown: '## Betrieb\n\n`[modules.logs]`' },
];
const LOG: Changelog = { title: 'Was ist neu', markdown: '## 0.6.0 – 07.10.2026\n\n### Neu\n\n- **help:** Seite', hint: null };

describe('Help page', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  beforeEach(async () => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(), provideHttpClientTesting(),
        provideRouter([{ path: 'help', component: Help }]),
      ],
    });
    http = TestBed.inject(HttpTestingController);
    harness = await RouterTestingHarness.create();
  });

  afterEach(() => http.verify());

  /** Opens the page and answers both requests; null = let that request fail. */
  async function open(url: string, docs: HelpDoc[] | null = DOCS, log: Changelog | null = LOG): Promise<HTMLElement> {
    await harness.navigateByUrl(url, Help);
    const index = http.expectOne('/api/v1/help');
    const changelog = http.expectOne('/api/v1/help/changelog');
    if (docs) index.flush({ docs });
    else index.flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    if (log) changelog.flush(log);
    else changelog.flush(null, { status: 0, statusText: '' });
    await settle();
    harness.detectChanges();
    return harness.routeNativeElement!;
  }

  const topicLinks = (el: HTMLElement) => [...el.querySelectorAll<HTMLAnchorElement>('nav[aria-label="Hilfethemen"] a')];
  const title = (el: HTMLElement) => el.querySelector('article h2')?.textContent?.trim();

  it('lists every help text plus "Was ist neu" and starts with the general part', async () => {
    const el = await open('/help');
    expect(topicLinks(el).map((a) => a.textContent?.trim()))
      .toEqual(['Allgemein', 'Übersicht (Dashboard)', 'Protokoll (Logs)', 'Was ist neu']);
    expect(topicLinks(el).map((a) => a.getAttribute('href')))
      .toEqual(['/help?doc=core', '/help?doc=dashboard', '/help?doc=logs', '/help?doc=changelog']);
    expect(title(el)).toBe('Allgemein');
    expect(topicLinks(el)[0].getAttribute('aria-current')).toBe('page');
    expect(el.querySelector('article h3')?.textContent).toBe('Aufbau');      // Markdown rendered
  });

  it('opens the topic named in ?doc and marks it', async () => {
    const el = await open('/help?doc=dashboard');
    expect(title(el)).toBe('Übersicht (Dashboard)');
    expect(topicLinks(el).filter((a) => a.getAttribute('aria-current') === 'page').map((a) => a.textContent?.trim()))
      .toEqual(['Übersicht (Dashboard)']);
  });

  it('falls back to the general part for keys without a help text', async () => {
    const el = await open('/help?doc=catalog');              // module without HELP.md (yet), or a typo
    expect(title(el)).toBe('Allgemein');
  });

  it('switches topics without reloading the texts', async () => {
    await open('/help');
    await harness.navigateByUrl('/help?doc=changelog');     // no new requests: http.verify() in afterEach
    harness.detectChanges();
    const el = harness.routeNativeElement!;
    expect(title(el)).toBe('Was ist neu');
    expect(el.querySelector('article li')?.textContent).toBe('help: Seite');
  });

  it('flags a module that is not running', async () => {
    const el = await open('/help?doc=logs');
    expect(el.querySelector('article header span')?.textContent?.trim()).toBe('Modul failed');

    await harness.navigateByUrl('/help?doc=dashboard');      // running: no flag
    harness.detectChanges();
    expect(el.querySelector('article header span')).toBeNull();
  });

  it('shows the hint while there is no CHANGELOG.md', async () => {
    const el = await open('/help?doc=changelog', DOCS, { title: 'Was ist neu', markdown: null, hint: 'Noch kein CHANGELOG.md' });
    expect(el.querySelector('article')?.textContent).toContain('Noch kein CHANGELOG.md');
  });

  it('keeps the help texts when only the changelog fails', async () => {
    const el = await open('/help?doc=changelog', DOCS, null);
    expect(topicLinks(el).length).toBe(4);
    expect(el.querySelector('article')?.textContent).toContain('„Was ist neu“ konnte nicht geladen werden: Backend nicht erreichbar');
  });

  it('shows an error when the help texts cannot be loaded', async () => {
    const el = await open('/help', null);
    expect(el.querySelector('[role="alert"]')?.textContent).toContain('HTTP 500');
    expect(topicLinks(el)).toEqual([]);
  });

  it('removes scripts and event handlers from the Markdown (Angular sanitizer)', async () => {
    const evil = '## Titel\n\n<script>window.hacked = true</script>\n\n<img src="x.png" onerror="window.hacked = true">';
    const el = await open('/help', [{ key: 'core', title: 'Allgemein', moduleState: null, markdown: evil }]);
    const body = el.querySelector('article')!;
    expect(body.querySelector('script')).toBeNull();
    expect(body.querySelector('img')?.getAttribute('onerror')).toBeNull();
    expect(body.querySelector(':scope > div h2')?.textContent).toBe('Titel');   // rendered text, not the page title
  });

  it('jumps to a heading from the table of contents', async () => {
    const el = await open('/help');
    const headings = [...el.querySelectorAll<HTMLElement>('article h2, article h3')].slice(1);   // first h2 = page title
    const scrolled: string[] = [];
    for (const h of headings) h.scrollIntoView = () => scrolled.push(h.textContent ?? '');

    const toc = [...el.querySelectorAll<HTMLButtonElement>('aside button')];
    expect(toc.map((b) => b.textContent?.trim())).toEqual(['Bedienung', 'Aufbau']);
    toc[1].click();
    expect(scrolled).toEqual(['Aufbau']);
    expect(document.activeElement?.textContent).toBe('Aufbau');      // focus follows for screen readers
  });
});
