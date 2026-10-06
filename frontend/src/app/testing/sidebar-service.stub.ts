import { signal } from '@angular/core';
import { SidebarService } from '../layout/sidebar.service';

/** jsdom has no window.matchMedia -> tests replace the real service with this stub. */
export const sidebarServiceStub = {
  provide: SidebarService,
  useValue: { mobileOpen: signal(false).asReadonly(), open: () => {}, close: () => {} },
};
