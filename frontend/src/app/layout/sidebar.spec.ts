import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { sidebarServiceStub } from '../testing/sidebar-service.stub';
import { Component } from '@angular/core';
import { Sidebar } from './sidebar';
import { ShellStore } from '../core/shell.store';

@Component({ template: '' })
class Dummy {}

describe('Sidebar', () => {
  function setup() {
    TestBed.configureTestingModule({
      imports: [Sidebar],
      providers: [
        provideHttpClient(), provideHttpClientTesting(), sidebarServiceStub,
        provideRouter([
          { path: '', component: Dummy, title: 'Dashboard', data: { nav: 'main', module: 'dashboard' } },
          { path: 'logs', component: Dummy, title: 'Logs', data: { nav: 'footer', module: 'logs' } },
          { path: 'about', component: Dummy, title: 'About', data: { nav: 'footer' } },   // no module key
        ]),
      ],
    });
    const fixture = TestBed.createComponent(Sidebar);
    const store = TestBed.inject(ShellStore);
    return { sidebar: fixture.componentInstance, store };
  }

  it('marks modules unknown while the backend has not answered', () => {
    const { sidebar } = setup();
    expect(sidebar.mainItems().map(i => i.state)).toEqual(['unknown']);
  });

  it('merges backend states and keeps core routes available', () => {
    const { sidebar, store } = setup();
    store.health.set({
      status: 'degraded', version: '0.1.0', database: true,
      modules: [
        { key: 'dashboard', title: 'Dashboard', icon: 'x', order: 10, state: 'running' },
        { key: 'logs', title: 'Logs', icon: 'x', order: 30, state: 'failed', error: 'boom' },
      ],
    });
    const [dash] = sidebar.mainItems();
    const [logs, about] = sidebar.footerItems();
    expect(dash.available).toBe(true);
    expect(logs.state).toBe('failed');
    expect(logs.hint).toBe('boom');                        // error text ends up in the tooltip
    expect(about.state).toBe('core');
    expect(about.available).toBe(true);
  });
});
