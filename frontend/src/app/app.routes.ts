import { Routes } from '@angular/router';
import { Dashboard } from './dashboard/dashboard';
import { Catalog } from './catalog/catalog';
import { Settings } from './settings/settings';
import { Documents } from './documents/documents';
import { Logs } from './logs/logs';

export const routes: Routes = [
  {
    path: '',
    component: Dashboard,
    title: 'Dashboard',
    data: { nav: 'main', icon: 'dashboard' }
  },
  {
    path: 'catalog',
    component: Catalog,
    title: 'Catalog',
    data: { nav: 'main', icon: 'list' }
  },
  {
    path: 'documents',
    component: Documents,
    title: 'Documents',
    data: { nav: 'main', icon: 'file-text' }
  },
  {
    path: 'logs',
    component: Logs,
    title: 'Logs',
    data: { nav: 'footer', icon: 'log' }
  },
  {
    path: 'settings',
    component: Settings,
    title: 'Settings',
    data: { nav: 'footer', icon: 'settings' }
  }
];