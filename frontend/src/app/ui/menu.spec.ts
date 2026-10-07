import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { Menu } from './menu';

@Component({
  imports: [Menu],
  template: `
    <app-menu label="Testmenü">
      <span menuTrigger>⋮</span>
      <button role="menuitem" class="a" (click)="picked = 'a'">A</button>
      <button role="menuitem" class="b" (click)="picked = 'b'">B</button>
      <button role="menuitem" class="c" disabled>C</button>
    </app-menu>
    <p class="outside">draußen</p>`,
})
class Host {
  picked = '';
}

function setup() {
  const fixture = TestBed.createComponent(Host);
  document.body.appendChild(fixture.nativeElement);             // focus() needs a connected element
  fixture.detectChanges();
  const el: HTMLElement = fixture.nativeElement;
  const trigger = el.querySelector('app-menu > button') as HTMLButtonElement;
  const render = () => fixture.detectChanges();
  const panel = () => el.querySelector('[role="menu"]');
  const key = (k: string) => {
    (document.activeElement ?? trigger).dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true }));
    render();
  };
  return { fixture, el, trigger, render, panel, key };
}

describe('Menu', () => {
  afterEach(() => document.body.replaceChildren());

  it('toggles from the trigger and announces its state', () => {
    const { trigger, render, panel } = setup();
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(trigger.getAttribute('aria-haspopup')).toBe('menu');
    trigger.click();
    render();
    expect(panel()).not.toBeNull();
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    expect(trigger.getAttribute('aria-controls')).toBe(panel()!.id);
    trigger.click();
    render();
    expect(panel()).toBeNull();
  });

  it('runs the entry action, then closes', () => {
    const { fixture, el, trigger, render, panel } = setup();
    trigger.click();
    render();
    (el.querySelector('.b') as HTMLButtonElement).click();
    render();
    expect(fixture.componentInstance.picked).toBe('b');
    expect(panel()).toBeNull();
  });

  it('closes on a click outside, not on the opening click', () => {
    const { el, trigger, render, panel } = setup();
    trigger.click();                                             // bubbles to document as well
    render();
    expect(panel()).not.toBeNull();
    (el.querySelector('.outside') as HTMLElement).click();
    render();
    expect(panel()).toBeNull();
  });

  it('is usable from the keyboard: first entry focused, arrows wrap, disabled skipped, Esc returns focus', () => {
    const { el, trigger, render, panel, key } = setup();
    trigger.click();
    render();
    expect(document.activeElement).toBe(el.querySelector('.a'));
    key('ArrowDown');
    expect(document.activeElement).toBe(el.querySelector('.b'));
    key('ArrowDown');                                            // C is disabled -> wraps to A
    expect(document.activeElement).toBe(el.querySelector('.a'));
    key('ArrowUp');
    expect(document.activeElement).toBe(el.querySelector('.b'));
    key('Escape');
    expect(panel()).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});
