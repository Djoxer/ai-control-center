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
  {
    path: 'logs',
    component: Logs,
    title: 'Protokoll',
    data: { nav: 'footer', icon: 'log', module: 'logs' }
  },
  {
    path: 'settings',
    component: Settings,
    title: 'Einstellungen',
    data: { nav: 'footer', icon: 'settings', module: 'settings' }
  }
];
