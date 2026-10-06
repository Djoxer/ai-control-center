import { Component, CUSTOM_ELEMENTS_SCHEMA, ElementRef, computed, effect, inject, viewChild } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { Route, Router, RouterLink, RouterLinkActive } from '@angular/router';
import { SidebarService } from './sidebar.service';
import { Icon } from './icon';
import { ShellStore } from '../core/shell.store';
import { ModuleInfo } from '../api/models/module-info';

type NavGroup = 'main' | 'footer';

// 'unknown' = backend does not know this module yet (folder without MODULE, or backend offline)
export type NavState = ModuleInfo['state'] | 'unknown' | 'core';

interface NavItem {
  label: string;
  path: string;
  exact: boolean;
  icon: string | null; // null = render no icon
  group: NavGroup;
  module: string | null; // backend module key, null = always available
}

export interface NavEntry extends NavItem {
  state: NavState;
  available: boolean;    // only 'running' modules (and core routes) are fully usable
  hint: string | null;   // tooltip: error text or state
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
  private readonly shell = inject(ShellStore);
  readonly sidebar = inject(SidebarService);
  private readonly dialog = viewChild.required<ElementRef<HTMLDialogElement>>('mobileDialog');

  // Static part: built once from the router config (Angular knows its routes at build time)
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
      module: typeof r.data?.['module'] === 'string' ? r.data['module'] : null,
    }));

  // Dynamic part: merged with the backend's module states, recomputed when health changes
  private readonly entries = computed<NavEntry[]>(() => {
    const byKey = new Map(this.shell.modules().map(m => [m.key, m]));
    return this.items.map(item => {
      if (item.module === null) return { ...item, state: 'core', available: true, hint: null };
      const m = byKey.get(item.module);
      const state: NavState = m?.state ?? 'unknown';
      return {
        ...item,
        state,
        available: state === 'running',
        hint: m?.error ?? (state === 'running' ? null : `module ${state}`),
      };
    });
  });

  readonly mainItems = computed(() => this.entries().filter(i => i.group === 'main'));
  readonly footerItems = computed(() => this.entries().filter(i => i.group === 'footer'));

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
