import '../testing/dialog-polyfill';

import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { Router, provideRouter } from '@angular/router';

import { ShellStore } from '../core/shell.store';
import { sidebarServiceStub } from '../testing/sidebar-service.stub';
import { Topbar } from './topbar';

@Component({ template: '' })
class Page {}

async function setup() {
  TestBed.configureTestingModule({
    imports: [Topbar],
    providers: [
      provideHttpClient(), provideHttpClientTesting(), sidebarServiceStub,
      provideRouter([
        { path: '', component: Page, title: 'Übersicht' },
        { path: 'logs', component: Page, title: 'Protokoll', data: { module: 'logs' } },
        { path: 'help', component: Page, title: 'Hilfe' },
      ]),
    ],
  });
  const store = TestBed.inject(ShellStore);
  store.health.set({
    status: 'ok', version: '0.4.0', database: true,
    modules: [
      { key: 'dashboard', title: 'Dashboard', icon: 'x', order: 10, state: 'running' },
      { key: 'logs', title: 'Logs', icon: 'x', order: 30, state: 'failed', error: 'boom' },
    ],
  });
  const fixture = TestBed.createComponent(Topbar);
  document.body.appendChild(fixture.nativeElement);
  await TestBed.inject(Router).navigateByUrl('/logs');
  fixture.detectChanges();
  return { fixture, el: fixture.nativeElement as HTMLElement };
}

describe('Topbar', () => {
  afterEach(() => document.body.replaceChildren());

  it('carries no template placeholders: no external image, no dead search field', async () => {
    const { el } = await setup();
    expect(el.querySelector('img')).toBeNull();
    expect(el.querySelector('input')).toBeNull();
    expect(el.textContent).not.toContain('Tom Cook');
  });

  it('shows the title of the current page and the backend version', async () => {
    const { el } = await setup();
    expect(el.querySelector('h1')?.textContent?.trim()).toBe('Protokoll');
    expect(el.textContent).toContain('v0.4.0');
  });

  function openMenu(fixture: { detectChanges(): void }, el: HTMLElement): HTMLElement[] {
    (el.querySelector('app-menu > button') as HTMLButtonElement).click();
    fixture.detectChanges();
    return [...el.querySelectorAll<HTMLElement>('[role="menuitem"]')];
  }

  it('opens the help of the page on screen from the app menu', async () => {
    const { fixture, el } = await setup();
    let help = openMenu(fixture, el)[0] as HTMLAnchorElement;
    expect(help.textContent?.trim()).toBe('Hilfe');
    expect(help.getAttribute('href')).toBe('/help?doc=logs');     // module key of the Protokoll route

    await TestBed.inject(Router).navigateByUrl('/');                // page without a module
    fixture.detectChanges();
    help = el.querySelector('[role="menuitem"]') ?? openMenu(fixture, el)[0] as HTMLAnchorElement;
    expect(help.getAttribute('href')).toBe('/help');                // -> general part

    help.click();
    await fixture.whenStable();
    expect(TestBed.inject(Router).url).toBe('/help');
    fixture.detectChanges();
    expect(el.querySelector('h1')?.textContent?.trim()).toBe('Hilfe');
    expect(el.querySelector('[role="menu"]')).toBeNull();            // menu closed after the click
  });

  it('opens the about dialog from the app menu', async () => {
    const { fixture, el } = await setup();
    const items = openMenu(fixture, el);
    expect(items.map((i) => i.textContent?.trim())).toEqual(['Hilfe', 'Über AI Control Center', 'API-Dokumentation']);

    items[1].click();
    fixture.detectChanges();
    const dialog = el.querySelector('app-about-dialog dialog') as HTMLDialogElement;
    expect(dialog.open).toBe(true);
    expect(dialog.textContent).toContain('0.4.0');
    expect(dialog.textContent).toContain('failed');              // module states listed
    expect(el.querySelector('[role="menu"]')).toBeNull();        // menu closed behind the dialog
  });

  it('leads from the about dialog to "Was ist neu" and closes the dialog', async () => {
    const { fixture, el } = await setup();
    openMenu(fixture, el)[1].click();
    fixture.detectChanges();
    const dialog = el.querySelector('app-about-dialog dialog') as HTMLDialogElement;
    const news = [...dialog.querySelectorAll('a')].find((a) => a.textContent?.includes('Was ist neu'))!;
    expect(news.getAttribute('href')).toBe('/help?doc=changelog');

    news.click();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(TestBed.inject(Router).url).toBe('/help?doc=changelog');
    expect(dialog.open).toBe(false);
  });
});
