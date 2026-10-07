import { Component, ElementRef, afterRenderEffect, inject, input, signal, viewChild } from '@angular/core';

import { ui } from './tokens';

let nextId = 0;

/**
 * Dropdown menu: a trigger button and a panel of entries, state owned by Angular.
 *
 *   <app-menu label="Menü">
 *     <app-icon menuTrigger name="more-vertical" class="size-6" />
 *     <button role="menuitem" [class]="ui.menuItem" (click)="…">Über …</button>
 *     <a role="menuitem" [class]="ui.menuItem" href="/docs" target="_blank">API-Dokumentation</a>
 *   </app-menu>
 *
 * Closes on: entry click, click outside, Esc (focus returns to the trigger), Tab.
 * Arrow keys / Home / End move between entries. No native popover on purpose: Angular state keeps it
 * testable, and the panel stays positioned under its trigger without anchor-positioning CSS.
 */
@Component({
  selector: 'app-menu',
  host: {
    class: 'relative',
    '(document:click)': 'onDocumentClick($event)',
    '(keydown)': 'onKeydown($event)',
  },
  template: `
    <button #trigger type="button" [class]="triggerClass()" aria-haspopup="menu"
            [attr.aria-expanded]="open()" [attr.aria-controls]="panelId" (click)="toggle()">
      <span class="sr-only">{{ label() }}</span>
      <ng-content select="[menuTrigger]" />
    </button>
    @if (open()) {
      <div #panel [id]="panelId" role="menu" [attr.aria-label]="label()" (click)="onPanelClick($event)"
           [class]="ui.menu" class="absolute right-0 z-50 mt-2 min-w-56">
        <ng-content />
      </div>
    }
  `,
})
export class Menu {
  readonly label = input.required<string>();                 // screen readers: what this menu is
  readonly triggerClass = input<string>(ui.iconButton);

  protected readonly ui = ui;
  protected readonly panelId = `app-menu-${nextId++}`;
  readonly open = signal(false);

  private readonly host: HTMLElement = inject(ElementRef<HTMLElement>).nativeElement;
  private readonly trigger = viewChild.required<ElementRef<HTMLElement>>('trigger');
  private readonly panel = viewChild<ElementRef<HTMLElement>>('panel');

  constructor() {
    // after the panel is in the DOM: move focus to the first entry (keyboard users land inside)
    afterRenderEffect(() => {
      if (this.open()) this.items()[0]?.focus();
    });
  }

  toggle(): void {
    this.open.update((o) => !o);
  }

  close(returnFocus = false): void {
    if (!this.open()) return;
    this.open.set(false);
    if (returnFocus) this.trigger().nativeElement.focus();
  }

  protected onDocumentClick(event: MouseEvent): void {
    // the click that opened the menu bubbles up here too - it happened inside, so it is ignored
    if (!this.host.contains(event.target as Node)) this.close();
  }

  protected onPanelClick(event: MouseEvent): void {
    // the entry's own (click) ran first (it is deeper in the tree); now the menu goes away
    if ((event.target as HTMLElement).closest('[role="menuitem"]')) this.close();
  }

  protected onKeydown(event: KeyboardEvent): void {
    if (!this.open()) return;
    const items = this.items();
    const index = items.indexOf(document.activeElement as HTMLElement);
    const focus = (i: number) => items[(i + items.length) % items.length]?.focus();
    switch (event.key) {
      case 'Escape': this.close(true); break;
      case 'Tab': this.close(); return;                       // let the browser move focus on
      case 'ArrowDown': focus(index + 1); break;
      case 'ArrowUp': focus(index < 0 ? items.length - 1 : index - 1); break;
      case 'Home': focus(0); break;
      case 'End': focus(items.length - 1); break;
      default: return;
    }
    event.preventDefault();                                   // no page scrolling on arrow keys
  }

  private items(): HTMLElement[] {
    const panel = this.panel()?.nativeElement;
    return panel ? [...panel.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])')] : [];
  }
}
