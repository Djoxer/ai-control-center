import { Component, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router } from '@angular/router';
import { filter, map } from 'rxjs';

import { ShellStore } from '../core/shell.store';
import { Menu } from '../ui/menu';
import { ui } from '../ui/tokens';
import { AboutDialog } from './about-dialog';
import { Icon } from './icon';
import { SidebarService } from './sidebar.service';

@Component({
  selector: 'app-topbar',
  imports: [Icon, Menu, AboutDialog],
  templateUrl: './topbar.html'
})
export class Topbar {
  private readonly router = inject(Router);
  readonly sidebar = inject(SidebarService);
  readonly shell = inject(ShellStore);   // backend status pill
  readonly ui = ui;
  readonly aboutOpen = signal(false);

  /** Title of the current page from the route config ('Übersicht', 'Protokoll', …). */
  readonly pageTitle = toSignal(
    this.router.events.pipe(
      filter((e): e is NavigationEnd => e instanceof NavigationEnd),
      map(() => this.currentTitle()),
    ),
    { initialValue: this.currentTitle() },
  );

  private currentTitle(): string {
    let route = this.router.routerState.snapshot.root;
    while (route.firstChild) route = route.firstChild;   // deepest active route carries the title
    return route.title ?? '';
  }
}
