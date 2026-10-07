import { Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRouteSnapshot, NavigationEnd, Router, RouterLink } from '@angular/router';
import { filter, map } from 'rxjs';

import { ShellStore } from '../core/shell.store';
import { Menu } from '../ui/menu';
import { ui } from '../ui/tokens';
import { AboutDialog } from './about-dialog';
import { Icon } from './icon';
import { SidebarService } from './sidebar.service';

@Component({
  selector: 'app-topbar',
  imports: [Icon, Menu, AboutDialog, RouterLink],
  templateUrl: './topbar.html'
})
export class Topbar {
  private readonly router = inject(Router);
  readonly sidebar = inject(SidebarService);
  readonly shell = inject(ShellStore);   // backend status pill
  readonly ui = ui;
  readonly aboutOpen = signal(false);

  /** Deepest active route after every navigation - it carries title and data of the page on screen. */
  private readonly page = toSignal(
    this.router.events.pipe(
      filter((e): e is NavigationEnd => e instanceof NavigationEnd),
      map(() => this.currentPage()),
    ),
    { initialValue: this.currentPage() },
  );

  /** Title of the current page from the route config ('Übersicht', 'Protokoll', …). */
  readonly pageTitle = computed(() => this.page().title ?? '');

  /** "Hilfe" opens the help of the page on screen: ?doc=<module key>; other pages get the general part. */
  readonly helpParams = computed(() => {
    const module: unknown = this.page().data['module'];
    return typeof module === 'string' ? { doc: module } : null;
  });

  private currentPage(): ActivatedRouteSnapshot {
    let route = this.router.routerState.snapshot.root;
    while (route.firstChild) route = route.firstChild;
    return route;
  }
}
