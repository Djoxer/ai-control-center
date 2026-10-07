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
        { path: 'logs', component: Page, title: 'Protokoll' },
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

  it('opens the about dialog from the app menu', async () => {
    const { fixture, el } = await setup();
    (el.querySelector('app-menu > button') as HTMLButtonElement).click();
    fixture.detectChanges();
    const items = [...el.querySelectorAll('[role="menuitem"]')].map((i) => i.textContent?.trim());
    expect(items).toEqual(['Über AI Control Center', 'API-Dokumentation']);

    (el.querySelector('[role="menuitem"]') as HTMLButtonElement).click();
    fixture.detectChanges();
    const dialog = el.querySelector('app-about-dialog dialog') as HTMLDialogElement;
    expect(dialog.open).toBe(true);
    expect(dialog.textContent).toContain('0.4.0');
    expect(dialog.textContent).toContain('failed');              // module states listed
    expect(el.querySelector('[role="menu"]')).toBeNull();        // menu closed behind the dialog
  });
});
