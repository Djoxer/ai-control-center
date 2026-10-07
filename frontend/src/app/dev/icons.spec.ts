import { iconSnippet, parseIconSprite } from './icons';

const SPRITE = `<svg xmlns="http://www.w3.org/2000/svg">
  <!-- Stroke style is inherited from the <svg> that uses a symbol -->

  <!-- Navigation & layout -->
  <symbol id="dashboard" viewBox="0 0 24 24"><path d="M4 4h7"/></symbol>
  <symbol id="home" viewBox="0 0 24 24"><path d="M3 11l9-7"/></symbol>
  <!-- Status -->
  <symbol id="warning" viewBox="0 0 24 24"><path d="M12 3"/></symbol>
  <defs><linearGradient id="not-an-icon"/></defs>
  <symbol viewBox="0 0 24 24"><path d="M0 0"/></symbol>
</svg>`;

describe('parseIconSprite', () => {
  it('reads symbol ids in order, grouped by the latest comment', () => {
    expect(parseIconSprite(SPRITE)).toEqual([
      { name: 'dashboard', group: 'Navigation & layout' },
      { name: 'home', group: 'Navigation & layout' },
      { name: 'warning', group: 'Status' },
    ]);                                                  // <defs> content and symbols without id are skipped
  });

  it('falls back to "Ohne Gruppe" and survives broken input', () => {
    expect(parseIconSprite('<svg xmlns="http://www.w3.org/2000/svg"><symbol id="x"/></svg>'))
      .toEqual([{ name: 'x', group: 'Ohne Gruppe' }]);
    expect(parseIconSprite('<svg><symbol id="x"')).toEqual([]);
    expect(parseIconSprite('')).toEqual([]);
  });

  it('builds the template snippet', () => {
    expect(iconSnippet('menu')).toBe('<app-icon name="menu" class="size-5" />');
  });
});
