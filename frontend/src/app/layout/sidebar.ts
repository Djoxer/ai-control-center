import { Component, CUSTOM_ELEMENTS_SCHEMA, ElementRef, effect, inject, viewChild } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { Route, Router, RouterLink, RouterLinkActive } from '@angular/router';
import { SidebarService } from './sidebar.service';
import { Icon } from './icon';

type NavGroup = 'main' | 'footer';

interface NavItem {
  label: string;
  path: string;
  exact: boolean;
  icon: string | null; // null = render no icon
  group: NavGroup;
}

const isNavGroup = (v: unknown): v is NavGroup => v === 'main' || v === 'footer';

@Component({
  selector: 'app-sidebar',
  imports: [RouterLink, RouterLinkActive, NgTemplateOutlet, Icon],
  schemas: [CUSTOM_ELEMENTS_SCHEMA],
  templateUrl: './sidebar.html'
})
export class Sidebar {
  private readonly router = inject(Router);
  readonly sidebar = inject(SidebarService);
  private readonly dialog = viewChild.required<ElementRef<HTMLDialogElement>>('mobileDialog');


  // Build all nav entries once from the router config
  private readonly items: NavItem[] = this.router.config
    .filter((r): r is Route & { path: string; title: string } =>
      isNavGroup(r.data?.['nav']) &&
      typeof r.path === 'string' &&
      typeof r.title === 'string'
    )
    .map(r => ({
      label: r.title,
      path: '/' + r.path,
      exact: r.path === '',
      icon: typeof r.data?.['icon'] === 'string' ? r.data['icon'] : null,
      group: r.data!['nav'] as NavGroup,
    }));

  // Split by group – the template renders each list in its own <li>
  readonly mainItems = this.items.filter(i => i.group === 'main');
  readonly footerItems = this.items.filter(i => i.group === 'footer');

  constructor() {
    // Sync signal -> DOM. Re-runs whenever mobileOpen() or dialog() changes.
    effect(() => {
      const el = this.dialog().nativeElement;
      const shouldBeOpen = this.sidebar.mobileOpen();

      // Guards matter: showModal() on an already open dialog throws InvalidStateError
      if (shouldBeOpen && !el.open) el.showModal();
      if (!shouldBeOpen && el.open) el.close();
    });
  }

  // Native dialogs don't close on backdrop click – detect clicks on the dialog element itself
  onDialogClick(event: MouseEvent): void {
    if (event.target === this.dialog().nativeElement) {
      this.sidebar.close();
    }
  }
}
