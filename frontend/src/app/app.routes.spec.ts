import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import { routes } from './app.routes';
import { Help } from './help/help';

@Component({ template: '' })
class Stub {}

describe('app routes', () => {
  it('ends with a catch-all that leads to the overview', () => {
    expect(routes.at(-1)).toEqual({ path: '**', redirectTo: '' });
  });

  it('redirects an unknown address instead of showing an empty page', async () => {
    // same catch-all, stub pages: the real ones would start HTTP and SSE
    TestBed.configureTestingModule({
      providers: [provideRouter([{ path: '', component: Stub }, { path: 'logs', component: Stub }, routes.at(-1)!])],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/gibt-es-nicht');
    expect(TestBed.inject(Router).url).toBe('/');
  });

  it('loads the help page lazily and keeps it out of the sidebar', async () => {
    const help = routes.find((r) => r.path === 'help')!;
    expect(help.title).toBe('Hilfe');
    expect(help.data?.['nav']).toBeUndefined();               // reached from the ⋮ menu, not the sidebar
    expect(await help.loadComponent!()).toBe(Help);
  });
});
