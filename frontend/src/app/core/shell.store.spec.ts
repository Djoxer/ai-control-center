import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';

import { ShellStore } from './shell.store';

describe('ShellStore', () => {
  let store: ShellStore;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    store = TestBed.inject(ShellStore);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());   // no unexpected requests

  it('loads health and exposes modules for the menu', async () => {
    const done = store.refresh();
    http.expectOne('/api/v1/health').flush({
      status: 'ok', version: '0.1.0', database: true,
      modules: [{ key: 'logs', title: 'Logs', icon: 'article', order: 30, state: 'running' }],
    });
    await done;
    expect(store.status()).toBe('ok');
    expect(store.modules().map(m => m.key)).toEqual(['logs']);
  });

  it('keeps the last known health when the backend goes away', async () => {
    let done = store.refresh();
    http.expectOne('/api/v1/health').flush({ status: 'ok', version: '0.1.0', database: true, modules: [] });
    await done;
    done = store.refresh();
    http.expectOne('/api/v1/health').error(new ProgressEvent('error'));
    await done;
    expect(store.status()).toBe('offline');
    expect(store.health()?.version).toBe('0.1.0');   // menu does not flicker away
  });
});
