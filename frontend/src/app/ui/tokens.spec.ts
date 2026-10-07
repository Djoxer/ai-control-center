import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { UI_DOCS, ui } from './tokens';

/** All class strings of the registry, nested tone maps flattened: ['card', '...'], ['fill.normal', '...']. */
function entries(): [string, string][] {
  return Object.entries(ui).flatMap(([key, value]) =>
    typeof value === 'string'
      ? [[key, value] as [string, string]]
      : Object.entries(value).map(([sub, cls]) => [`${key}.${sub}`, cls] as [string, string]),
  );
}

const TEXT_SIZE = /^text-(xs|sm|base|lg|xl|[2-9]xl|\[\d+px\])$/;

describe('ui tokens', () => {
  it('documents every token for the style guide, and nothing else', () => {
    expect(Object.keys(UI_DOCS).sort()).toEqual(Object.keys(ui).sort());
  });

  it('keeps every class string clean', () => {
    for (const [key, cls] of entries()) {
      expect(cls, key).toBe(cls.trim());
      expect(cls, key).not.toMatch(/\s{2,}/);
      const parts = cls.split(' ');
      expect(new Set(parts).size, `${key}: duplicate class`).toBe(parts.length);
    }
  });

  it('has no conflicting utilities inside one token', () => {
    // two text sizes (text-xs + text-sm) would be decided by CSS order, not by intent
    for (const [key, cls] of entries()) {
      const base = cls.split(' ').filter((c) => !c.includes(':'));       // variants like aria-pressed: may repeat
      expect(base.filter((c) => TEXT_SIZE.test(c)).length, `${key}: text sizes`).toBeLessThanOrEqual(1);
      expect(base.filter((c) => /^rounded(-|$)/.test(c)).length, `${key}: radii`).toBeLessThanOrEqual(1);
    }
  });

  it('has no two tokens with the same classes, unless declared as an alias', () => {
    // same look, different meaning: allowed, but only on purpose - a table head may change on its own later
    const ALIASES = new Set(['meta=thead']);
    const seen = new Map<string, string>();
    for (const [key, cls] of entries()) {
      const norm = cls.split(' ').sort().join(' ');
      const first = seen.get(norm);
      if (first !== undefined) expect(ALIASES.has(`${first}=${key}`), `${key} duplicates ${first}`).toBe(true);
      else seen.set(norm, key);
    }
  });
});

@Component({
  template: `<article [class]="ui.card" class="p-4" [class.opacity-50]="dim"></article>`,
})
class Host {
  readonly ui = ui;
  dim = true;
}

describe('ui tokens in a template', () => {
  it('merge with static classes and [class.x] bindings on the same element', () => {
    // the whole registry relies on Angular merging these three sources instead of replacing
    const fixture = TestBed.createComponent(Host);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement.querySelector('article');
    for (const c of [...ui.card.split(' '), 'p-4', 'opacity-50']) {
      expect(el.classList.contains(c), c).toBe(true);
    }
    fixture.componentInstance.dim = false;
    fixture.changeDetectorRef.markForCheck();
    fixture.detectChanges();
    expect(el.classList.contains('opacity-50')).toBe(false);
    expect(el.classList.contains('rounded-xl')).toBe(true);
  });
});
