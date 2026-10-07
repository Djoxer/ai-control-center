import { Component, computed, input, output } from '@angular/core';

import { Icon } from '../layout/icon';
import { Modal } from './modal';
import { ui } from './tokens';

let nextId = 0;

const WIDTHS = {
  sm: 'max-w-sm',
  md: 'max-w-lg',
  lg: 'max-w-2xl',
} as const;

/**
 * Standard modal: title bar with close button, content, optional action row.
 *
 *   <app-dialog title="Über …" [open]="aboutOpen()" (dismiss)="aboutOpen.set(false)">
 *     <p>Content</p>
 *     <div dialogActions><button …>OK</button></div>
 *   </app-dialog>
 *
 * The owner keeps the open state; every way of closing (×, Esc, backdrop) ends in (dismiss).
 */
@Component({
  selector: 'app-dialog',
  imports: [Modal, Icon],
  host: { class: 'contents' },
  template: `
    <!-- m-auto: Tailwind's reset removes the browser's centering margin of <dialog> -->
    <dialog [appModal]="open()" (dismiss)="dismiss.emit()" [attr.aria-labelledby]="titleId"
            [class]="panelClass()" class="m-auto w-[calc(100%-2rem)] p-0">
      <div class="flex max-h-[85dvh] flex-col">
        <header class="flex items-start justify-between gap-4 border-b border-white/10 px-5 py-4">
          <h2 [id]="titleId" [class]="ui.dialogTitle">{{ title() }}</h2>
          <button type="button" [class]="ui.iconButton" (click)="dismiss.emit()">
            <span class="sr-only">Schließen</span>
            <app-icon name="close" class="size-5" />
          </button>
        </header>
        <div class="overflow-y-auto px-5 py-4">
          <ng-content />
        </div>
        <!-- hidden when no [dialogActions] element was projected -->
        <footer class="flex justify-end gap-2 border-t border-white/10 px-5 py-3 [&:not(:has(*))]:hidden">
          <ng-content select="[dialogActions]" />
        </footer>
      </div>
    </dialog>
  `,
})
export class Dialog {
  readonly title = input.required<string>();
  readonly open = input.required<boolean>();
  readonly size = input<keyof typeof WIDTHS>('md');
  readonly dismiss = output<void>();

  protected readonly ui = ui;
  protected readonly titleId = `app-dialog-title-${nextId++}`;
  // one [class] binding per element: token and width joined here
  protected readonly panelClass = computed(() => `${ui.dialog} ${WIDTHS[this.size()]}`);
}
