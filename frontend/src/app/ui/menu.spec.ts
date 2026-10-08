import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

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
    expect(trigger.className).toContain('aria-expanded:bg-white/10');   // lit while open (menus can open upwards)
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

  // jsdom has no layout: the rectangles of trigger and panel are faked, the window is 1000 x 800
  function layout(trigger: Partial<DOMRect>, panelHeight = 120) {
    vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(800);
    vi.spyOn(document.documentElement, 'clientWidth', 'get').mockReturnValue(1000);
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const r = this.getAttribute('role') === 'menu' ? { top: 0, bottom: panelHeight, right: 0, height: panelHeight } : trigger;
      return { left: 0, width: 0, x: 0, y: 0, top: 0, bottom: 0, right: 0, height: 0, ...r, toJSON: () => r } as DOMRect;
    });
  }

  describe('placement', () => {
    afterEach(() => vi.restoreAllMocks());

    it('hangs on the window, not inside its container, right-aligned below the trigger', () => {
      layout({ top: 100, bottom: 124, right: 900 });
      const { trigger, render, panel } = setup();
      trigger.click();
      render();
      const p = panel() as HTMLElement;
      expect(p.classList).toContain('fixed');                    // overflow-hidden of a card cannot clip it
      expect(p.classList).not.toContain('absolute');
      expect([p.style.top, p.style.right]).toEqual(['132px', '100px']);
    });

    it('opens upwards when the window ends below the trigger', () => {
      layout({ top: 700, bottom: 724, right: 990 });
      const { trigger, render, panel } = setup();
      trigger.click();
      render();
      const p = panel() as HTMLElement;
      expect([p.style.top, p.style.right]).toEqual(['572px', '10px']);   // 700 - 8 - 120; right >= 8
    });

    it('stays below when there is no room above either, and follows on scroll', () => {
      layout({ top: 60, bottom: 84, right: 500 }, 900);
      const { trigger, render, panel } = setup();
      trigger.click();
      render();
      const p = panel() as HTMLElement;
      expect(p.style.top).toBe('92px');
      layout({ top: 30, bottom: 54, right: 500 }, 900);             // a container scrolled by 30 px
      document.body.dispatchEvent(new Event('scroll'));            // does not bubble: caught in capture
      expect(p.style.top).toBe('62px');
      trigger.click();
      render();
      layout({ top: 0, bottom: 24, right: 500 });
      document.body.dispatchEvent(new Event('scroll'));            // closed: listener removed, no error
      expect(panel()).toBeNull();
    });
  });
});
