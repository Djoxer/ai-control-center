import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';

import { ClipboardService } from '../core/clipboard.service';
import { routes } from '../app.routes';
import { Dev } from './dev';

const SPRITE = `<svg xmlns="http://www.w3.org/2000/svg">
  <!-- Navigation -->
  <symbol id="menu"/><symbol id="home"/>
  <!-- Status -->
  <symbol id="warning"/>
</svg>`;

function setup(copyResult = true) {
  const copied: string[] = [];
  TestBed.configureTestingModule({
    imports: [Dev],
    providers: [
      provideHttpClient(), provideHttpClientTesting(),
      { provide: ClipboardService, useValue: { copy: async (t: string) => (copied.push(t), copyResult) } },
    ],
  });
  const fixture = TestBed.createComponent(Dev);
  fixture.detectChanges();
  TestBed.inject(HttpTestingController).expectOne('/icons.svg').flush(SPRITE);
  fixture.detectChanges();
  return { fixture, el: fixture.nativeElement as HTMLElement, copied };
}

describe('Dev page', () => {
  it('shows every icon of the sprite under its group', () => {
    const { el } = setup();
    expect([...el.querySelectorAll('#dev-icons h3')].map((h) => h.textContent?.trim())).toEqual(['Navigation', 'Status']);
    expect(el.querySelectorAll('#dev-icons ul button').length).toBe(3);
  });

  it('filters by name or group', () => {
    const { fixture, el } = setup();
    fixture.componentInstance.filter.set('stat');
    fixture.detectChanges();
    expect([...el.querySelectorAll('#dev-icons ul button')].map((b) => b.textContent?.trim())).toEqual(['warning']);
    fixture.componentInstance.filter.set('zzz');
    fixture.detectChanges();
    expect(el.textContent).toContain('Kein Icon passt');
  });

  it('copies the template snippet and says so', async () => {
    const { fixture, el, copied } = setup();
    (el.querySelector('#dev-icons ul button') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(copied).toEqual(['<app-icon name="menu" class="size-5" />']);
    expect(el.querySelector('[aria-live]')?.textContent).toContain('Kopiert');
  });

  it('tells when copying failed', async () => {
    const { fixture, el } = setup(false);
    (el.querySelector('#dev-icons ul button') as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(el.querySelector('[aria-live]')?.textContent).toContain('nicht möglich');
  });
});

describe('Dev page export and style guide', () => {
  it('copies the whole Markdown export including the icons it loaded', async () => {
    const { fixture, el, copied } = setup();
    const button = [...el.querySelectorAll('#dev-export button')].find((b) => b.textContent?.includes('Markdown'));
    (button as HTMLButtonElement).click();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(copied[0]).toMatch(/^---\ntitle: AI Control Center/);
    expect(copied[0]).toContain('`menu`');                            // icon names from the sprite
    expect(el.querySelector('#dev-export')?.textContent).toMatch(/≈ [\d.]+ Tokens/);
  });

  it('copies a token as Angular code from the style guide', async () => {
    const { fixture, el, copied } = setup();
    const card = el.querySelector('[data-token="card"]')!;
    expect(card.querySelector('article.rounded-xl.p-4')).not.toBeNull();  // preview rendered from the sample
    (card.querySelector('button') as HTMLButtonElement).click();       // first button = Angular
    await fixture.whenStable();
    expect(copied[0]).toContain('[class]="ui.card" class="p-4"');
  });
});

describe('dev route', () => {
  it('jumps to its sections without leaving the page', () => {
    const { el } = setup();
    // <base href="/"> turns href="#x" into "/#x" - the overview. No such links on this page.
    expect(el.querySelectorAll('a[href^="#"]').length).toBe(0);

    const scrolled: string[] = [];
    for (const s of el.querySelectorAll<HTMLElement>('section[id]')) s.scrollIntoView = () => scrolled.push(s.id);
    const links = [...el.querySelectorAll<HTMLButtonElement>('nav[aria-label="Abschnitte"] button')];
    expect(links.map((b) => b.textContent?.trim())).toEqual(['Export', 'Style Guide', 'Icons']);
    links.forEach((b) => b.click());
    expect(scrolled).toEqual(['dev-export', 'dev-style', 'dev-icons']);
    expect(document.activeElement?.id).toBe('dev-icons');
  });

  it('exists in dev mode, in the footer group right after the logs', () => {
    // unit tests run with dev mode on; the production build drops the route (isDevMode() === false)
    const footer = routes.filter((r) => r.data?.['nav'] === 'footer').map((r) => r.path);
    expect(footer.indexOf('dev')).toBe(footer.indexOf('logs') + 1);
    expect(routes.find((r) => r.path === 'dev')?.loadComponent).toBeTypeOf('function');
  });
});
