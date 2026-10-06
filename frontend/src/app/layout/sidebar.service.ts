import { DestroyRef, Injectable, PLATFORM_ID, inject, signal } from '@angular/core';
import { isPlatformBrowser } from '@angular/common';
import { NavigationEnd, Router } from '@angular/router';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { filter } from 'rxjs';

// Must match Tailwind's 'lg' breakpoint (v4: 64rem, v3: 1024px)
const DESKTOP_QUERY = '(min-width: 64rem)';

@Injectable({ providedIn: 'root' })
export class SidebarService {
  private readonly _mobileOpen = signal(false);
  readonly mobileOpen = this._mobileOpen.asReadonly();

  constructor() {
    const router = inject(Router);
    const destroyRef = inject(DestroyRef);

    // 1) Close after every successful navigation (link click, back button, programmatic)
    router.events
      .pipe(
        filter((e): e is NavigationEnd => e instanceof NavigationEnd),
        takeUntilDestroyed(destroyRef),
      )
      .subscribe(() => this.close());

    // 2) Close when the viewport grows into desktop range – browser only (SSR has no window)
    if (isPlatformBrowser(inject(PLATFORM_ID))) {
      const mql = window.matchMedia(DESKTOP_QUERY);
      const onChange = (e: MediaQueryListEvent) => {
        if (e.matches) this.close(); // crossed into desktop -> release the modal
      };
      mql.addEventListener('change', onChange);
      destroyRef.onDestroy(() => mql.removeEventListener('change', onChange));
    }
  }

  open(): void  { this._mobileOpen.set(true); }
  close(): void { this._mobileOpen.set(false); }
}