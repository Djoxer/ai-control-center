import { jumpTo } from './jump';

describe('jumpTo', () => {
  afterEach(() => document.body.replaceChildren());

  function target(html = '<section>Ziel</section>'): HTMLElement {
    document.body.innerHTML = `<button>Start</button>${html}`;
    const el = document.body.lastElementChild as HTMLElement;
    el.scrollIntoView = vi.fn();                 // jsdom has no layout, so no scrollIntoView
    return el;
  }

  it('scrolls the target to the top and moves the focus there', () => {
    const el = target();
    jumpTo(el);
    expect(el.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' });
    expect(document.activeElement).toBe(el);
    expect(el.getAttribute('tabindex')).toBe('-1');   // focusable, but not in the tab order
  });

  it('keeps an existing tabindex', () => {
    const el = target('<section tabindex="0">Ziel</section>');
    jumpTo(el);
    expect(el.getAttribute('tabindex')).toBe('0');
  });

  it('jumps without animation when the user asked for reduced motion', () => {
    const original = window.matchMedia;
    window.matchMedia = ((q: string) => ({ matches: q.includes('reduce') })) as unknown as typeof window.matchMedia;
    try {
      const el = target();
      jumpTo(el);
      expect(el.scrollIntoView).toHaveBeenCalledWith({ behavior: 'auto', block: 'start' });
    } finally {
      window.matchMedia = original;
    }
  });

  it('ignores a missing target', () => {
    expect(() => jumpTo(null)).not.toThrow();
    expect(() => jumpTo(undefined)).not.toThrow();
  });
});
