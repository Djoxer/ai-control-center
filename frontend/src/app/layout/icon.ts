import { Component, input } from '@angular/core';

// Renders one symbol of /icons.svg; size and color come from the host classes (e.g. class="size-5")
@Component({
  selector: 'app-icon',
  host: { class: 'inline-block shrink-0' },
  template: `
    <!-- 'block' removes the baseline gap an inline <svg> leaves below itself -->
    <svg class="block size-full" fill="none" stroke="currentColor" stroke-width="1.5"
         stroke-linecap="square" aria-hidden="true">
      <use [attr.href]="'/icons.svg#' + name()" />
    </svg>
  `,
})
export class Icon {
  readonly name = input.required<string>();
}