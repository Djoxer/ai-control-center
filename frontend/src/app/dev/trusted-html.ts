import { Directive, ElementRef, effect, inject, input } from '@angular/core';

/**
 * Writes a TRUSTED, repo-constant HTML string into its host. Dev page only.
 *
 * [innerHTML] would pull Angular's HTML sanitizer into the shared bundle (~9 kB in production,
 * although /dev never exists there) and strip the <svg> of the icon samples anyway.
 * Never use this for text from users, the API or files.
 */
@Directive({ selector: '[devTrustedHtml]' })
export class TrustedHtml {
  readonly devTrustedHtml = input.required<string>();
  private readonly host: HTMLElement = inject(ElementRef<HTMLElement>).nativeElement;

  constructor() {
    effect(() => {
      this.host.innerHTML = this.devTrustedHtml();
    });
  }
}
