import { UI_DOCS, ui } from '../ui/tokens';
import { buildMarkdown, estimateTokens, groupedTokens } from './markdown';

const ICONS = [{ name: 'menu', group: 'Navigation' }, { name: 'warning', group: 'Status' }];

describe('Markdown export', () => {
  const md = buildMarkdown({ icons: ICONS, version: '0.5.0', generatedAt: new Date('2026-10-07T10:00:00Z') });

  it('starts with YAML front matter a tool can read', () => {
    expect(md.split('\n').slice(0, 6)).toEqual([
      '---',
      'title: AI Control Center – UI-Bausteine',
      'generated: 2026-10-07T10:00:00.000Z',
      'version: 0.5.0',
      'source: frontend/src/app/ui/tokens.ts, frontend/public/icons.svg',
      '---',
    ]);
  });

  it('contains every token with classes and both snippet forms', () => {
    for (const token of Object.keys(ui)) {
      expect(md, token).toContain(`(\`${token}\`)`);
    }
    expect(md).toContain(ui.card);
    expect(md).toContain('[class]="ui.card" class="p-4"');
    expect(md).toContain(`<article class="${ui.card} p-4">`);
  });

  it('lists the icons by group and keeps code fences balanced', () => {
    expect(md).toContain('- **Navigation:** `menu`');
    expect(md).toContain('- **Status:** `warning`');
    expect((md.match(/```/g) ?? []).length % 2).toBe(0);
  });

  it('marks an unknown version instead of leaving it out', () => {
    const noVersion = buildMarkdown({ icons: [], version: null, generatedAt: new Date(0) });
    expect(noVersion).toContain('version: unbekannt');
  });

  it('groups in UI_DOCS order and estimates size', () => {
    const order = [...new Set(Object.values(UI_DOCS).map((d) => d.group))];
    expect(groupedTokens().map((g) => g.group)).toEqual(order);
    expect(estimateTokens('abcd')).toBe(1);
    expect(estimateTokens('abcde')).toBe(2);
  });
});
