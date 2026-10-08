import { Component, ElementRef, afterRenderEffect, computed, effect, inject, input, signal, viewChild } from '@angular/core';

import { ui } from './tokens';

let nextId = 0;
const GAP = 8;                                               // px between trigger and panel / window edge

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
 * testable. The panel is position: fixed, placed from the trigger's rectangle - like a sticky note on
 * the window instead of on the card: a card with overflow-hidden (or a scrolling table) cannot cut it
 * off. It opens below the trigger, or above it when the window ends first, and follows on scroll/resize.
 */
@Component({
  selector: 'app-menu',
  host: {
    class: 'relative',
    '(document:click)': 'onDocumentClick($event)',
    '(keydown)': 'onKeydown($event)',
  },
  template: `
    <button #trigger type="button" [class]="triggerClasses()" aria-haspopup="menu"
            [attr.aria-expanded]="open()" [attr.aria-controls]="panelId" (click)="toggle()">
      <span class="sr-only">{{ label() }}</span>
      <ng-content select="[menuTrigger]" />
    </button>
    @if (open()) {
      <div #panel [id]="panelId" role="menu" [attr.aria-label]="label()" (click)="onPanelClick($event)"
           [class]="ui.menu" class="fixed z-50 min-w-56">
        <ng-content />
      </div>
    }
  `,
})
export class Menu {
  readonly label = input.required<string>();                 // screen readers: what this menu is
  readonly triggerClass = input<string>(ui.iconButton);
  /** The open menu's trigger stays lit: a menu that opened upwards still shows which ⋮ it belongs to. */
  protected readonly triggerClasses = computed(
    () => `${this.triggerClass()} rounded-lg aria-expanded:bg-white/10 aria-expanded:text-white`);

  protected readonly ui = ui;
  protected readonly panelId = `app-menu-${nextId++}`;
  readonly open = signal(false);

  private readonly host: HTMLElement = inject(ElementRef<HTMLElement>).nativeElement;
  private readonly trigger = viewChild.required<ElementRef<HTMLElement>>('trigger');
  private readonly panel = viewChild<ElementRef<HTMLElement>>('panel');

  constructor() {
    // after the panel is in the DOM (before the browser paints): place it, then move focus to the
    // first entry (keyboard users land inside)
    afterRenderEffect(() => {
      if (!this.open()) return;
      this.place();
      this.items()[0]?.focus();
    });
    // while open, the panel follows its trigger: scroll events of inner containers do not bubble -> capture
    effect((onCleanup) => {
      if (!this.open()) return;
      const place = () => this.place();
      window.addEventListener('resize', place);
      document.addEventListener('scroll', place, { capture: true, passive: true });
      onCleanup(() => {
        window.removeEventListener('resize', place);
        document.removeEventListener('scroll', place, { capture: true });
      });
    });
  }

  /** Right edge flush with the trigger; below it if there is room, else above; never off the window. */
  place(): void {
    const panel = this.panel()?.nativeElement;
    if (!panel) return;
    const t = this.trigger().nativeElement.getBoundingClientRect();
    const height = panel.getBoundingClientRect().height;
    const viewHeight = window.innerHeight;
    const below = t.bottom + GAP;
    const above = t.top - GAP - height;
    const top = below + height <= viewHeight - GAP || above < GAP ? below : above;
    const right = Math.max(GAP, document.documentElement.clientWidth - t.right);
    panel.style.top = `${Math.round(top)}px`;
    panel.style.right = `${Math.round(right)}px`;
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
