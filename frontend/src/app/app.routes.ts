import { isDevMode } from '@angular/core';
import { Routes } from '@angular/router';
import { Dashboard } from './dashboard/dashboard';
import { Catalog } from './catalog/catalog';
import { Settings } from './settings/settings';
import { Logs } from './logs/logs';

// data.module = backend module key (folder name under backend/.../modules/).
// The sidebar looks up its state in /api/v1/health; routes without 'module' are always available.
export const routes: Routes = [
  {
    path: '',
    component: Dashboard,
    title: 'Übersicht',
    data: { nav: 'main', icon: 'dashboard', module: 'dashboard' }
  },
  {
    path: 'catalog',
    component: Catalog,
    title: 'Katalog',
    data: { nav: 'main', icon: 'list', module: 'catalog' }
  },
  // Lazy like every new module page (README "Add a module"): its code loads on the first visit
  {
    path: 'mcp',
    loadComponent: () => import('./mcp/mcp').then((m) => m.Mcp),
    title: 'MCP-Server',
    data: { nav: 'main', icon: 'server', module: 'mcp' }
  },
  {
    path: 'rag',
    loadComponent: () => import('./rag/rag').then((m) => m.Rag),
    title: 'RAG',
    data: { nav: 'main', icon: 'database', module: 'rag' }
  },
  {
    path: 'logs',
    component: Logs,
    title: 'Protokoll',
    data: { nav: 'footer', icon: 'log', module: 'logs' }
  },
  // Developer page (icons, style guide): only under "ng serve". The production build on the AI box
  // has no route at all, and the page is lazy-loaded, so it costs nothing in the main bundle.
  ...(isDevMode()
    ? [{
        path: 'dev',
        loadComponent: () => import('./dev/dev').then((m) => m.Dev),
        title: 'Entwicklung',
        data: { nav: 'footer', icon: 'grid' },
      }]
    : []),
  {
    path: 'settings',
    component: Settings,
    title: 'Einstellungen',
    data: { nav: 'footer', icon: 'settings', module: 'settings' }
  },
  // Help: no sidebar entry (no 'nav'), reached from the ⋮ menu and the About dialog. Lazy, so the
  // Markdown renderer only loads when someone opens the help.
  {
    path: 'help',
    loadComponent: () => import('./help/help').then((m) => m.Help),
    title: 'Hilfe',
  },
  // Unknown addresses (typo, old bookmark) lead to the overview instead of an empty page. Must stay last.
  { path: '**', redirectTo: '' },
];
