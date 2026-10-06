import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';

import { FakeEventSource, fakeEventSourceProvider } from '../testing/fake-event-source';
import { ShellStore } from './shell.store';

describe('ShellStore', () => {
  let store: ShellStore;
  let http: HttpTestingController;

  beforeEach(() => {
    FakeEventSource.instances = [];
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), fakeEventSourceProvider],
    });
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

  const settle = () => new Promise((r) => setTimeout(r, 0));   // let the awaited HTTP promise resolve

  it('start() checks health once and re-checks when the stream drops and comes back', async () => {
    const ok = { status: 'ok', version: '0.2.0', database: true, modules: [] };
    store.start();
    store.start();                                            // idempotent
    http.expectOne('/api/v1/health').flush(ok);
    const es = FakeEventSource.latest();
    es.open();                                                // first open: no extra request
    http.expectNone('/api/v1/health');

    es.fail();                                                // dropped -> status + immediate check
    expect(store.status()).toBe('reconnecting');
    http.expectOne('/api/v1/health').error(new ProgressEvent('error'));
    await settle();
    expect(store.status()).toBe('offline');

    es.open();                                                // back -> check again
    http.expectOne('/api/v1/health').flush(ok);
    await settle();
    expect(store.status()).toBe('ok');
    expect(store.live()).toBe(true);
  });
});
