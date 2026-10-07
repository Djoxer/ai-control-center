import { Directive, ElementRef, effect, inject, input, output } from '@angular/core';

/**
 * Drives a native <dialog> from a signal: true -> showModal(), false -> close().
 *
 *   <dialog [appModal]="open()" (dismiss)="open.set(false)">…</dialog>
 *
 * The browser does the hard parts: focus stays inside, the page behind is inert, Esc closes,
 * the dialog sits in the top layer (no z-index fights). This directive only adds what <dialog>
 * lacks: closing on a backdrop click, and telling the owner when the USER closed it (Esc,
 * backdrop, <form method="dialog">) so the owner's signal does not stay true while it is shut.
 */
@Directive({
  selector: 'dialog[appModal]',
  host: {
    '(close)': 'onClose()',
    '(click)': 'onClick($event)',
  },
})
export class Modal {
  readonly appModal = input.required<boolean>();
  /** The user closed the dialog. Not emitted when the owner closes it via [appModal]="false". */
  readonly dismiss = output<void>();

  private readonly el: HTMLDialogElement = inject(ElementRef<HTMLDialogElement>).nativeElement;

  constructor() {
    effect(() => {
      const shouldBeOpen = this.appModal();
      // guards matter: showModal() on an already open dialog throws InvalidStateError
      if (shouldBeOpen && !this.el.open) this.el.showModal();
      if (!shouldBeOpen && this.el.open) this.el.close();
    });
  }

  protected onClose(): void {
    // fires for every close; only report it when the owner still believes the dialog is open
    if (this.appModal()) this.dismiss.emit();
  }

  protected onClick(event: MouseEvent): void {
    // a click on the ::backdrop is reported with the <dialog> itself as target;
    // clicks inside land on the content wrapper, so the panel must fill the dialog (p-0)
    if (event.target === this.el) this.el.close();
  }
}
