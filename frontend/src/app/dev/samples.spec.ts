import { ui } from '../ui/tokens';
import { SAMPLES, angularSnippet, classText, renderAngular, renderHtml, resolveToken } from './samples';

describe('style-guide samples', () => {
  it('exist for every token and render without leftovers', () => {
    expect(Object.keys(SAMPLES).sort()).toEqual(Object.keys(ui).sort());
    for (const [token, sample] of Object.entries(SAMPLES)) {
      const html = renderHtml(sample.html);
      expect(html, token).not.toMatch(/\{[a-zA-Z.]+\}/);                 // every placeholder resolved
      expect(html, token).not.toContain('<icon');
      const angular = renderAngular(sample.html);
      expect(angular, token).not.toMatch(/class="[^"]*\{/);
    }
  });

  it('show each token where its own sample uses it', () => {
    // a sample that forgets its token would show something else under that name
    for (const token of Object.keys(SAMPLES)) {
      expect(SAMPLES[token as keyof typeof SAMPLES].html, token).toMatch(new RegExp(`\\{${token}[.}]`));
    }
  });

  it('turns placeholders into resolved classes or into ui bindings', () => {
    const sample = '<article class="{card} p-4"><span class="{pill} {pillTone.warning}">x</span><b class="font-bold">y</b></article>';
    expect(renderHtml(sample)).toBe(
      `<article class="${ui.card} p-4"><span class="${ui.pill} ${ui.pillTone.warning}">x</span><b class="font-bold">y</b></article>`,
    );
    expect(renderAngular(sample)).toBe(
      `<article [class]="ui.card" class="p-4"><span [class]="ui.pill + ' ' + ui.pillTone.warning">x</span><b class="font-bold">y</b></article>`,
    );
  });

  it('renders <icon> as sprite svg in HTML and as <app-icon> in Angular', () => {
    const sample = '<icon name="warning" class="size-5 {iconTone.warning}"/>';
    expect(renderHtml(sample)).toBe(
      `<svg class="size-5 ${ui.iconTone.warning}" fill="none" stroke="currentColor" stroke-width="1.5" ` +
      'stroke-linecap="square" aria-hidden="true"><use href="/icons.svg#warning"></use></svg>',
    );
    expect(renderAngular(sample)).toBe('<app-icon name="warning" [class]="ui.iconTone.warning" class="size-5" />');
  });

  it('refuses unknown tokens instead of rendering an empty class', () => {
    expect(() => resolveToken('cardd')).toThrow(/cardd/);
    expect(() => resolveToken('fill.huge')).toThrow();
    expect(() => resolveToken('card.normal')).toThrow();
    expect(() => renderHtml('<p class="{nope}"></p>')).toThrow();
  });

  it('uses the component snippet for overlays and lists tone maps line by line', () => {
    expect(angularSnippet('dialog')).toContain('<app-dialog');
    expect(angularSnippet('menu')).toContain('<app-menu');
    expect(classText('fill')).toBe(`normal: ${ui.fill.normal}\nwarning: ${ui.fill.warning}\ncritical: ${ui.fill.critical}`);
    expect(classText('card')).toBe(ui.card);
  });
});
