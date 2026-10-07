import { plainText, renderDoc, tocSelector } from './render';

const DOC = [
  'Einleitung',
  '',
  '## Bedienung',
  '',
  '### `[modules.logs]` & **Filter**',
  '',
  '| Filter | Wirkung |',
  '|---|---|',
  '| **Suche** | Text |',
  '',
  '#### Zu tief für das Inhaltsverzeichnis',
  '',
  '## Betrieb',
  '',
  '```toml',
  'key = "<ollama>"',
  '```',
].join('\n');

describe('help render', () => {
  it('builds a table of contents from h2 and h3 in document order', () => {
    const { toc } = renderDoc(DOC);
    expect(toc).toEqual([
      { depth: 2, text: 'Bedienung', index: 0 },
      { depth: 3, text: '[modules.logs] & Filter', index: 1 },    // inline Markdown gone, & stays plain
      { depth: 2, text: 'Betrieb', index: 2 },
    ]);
  });

  it('limits the table of contents to the given depth', () => {
    expect(renderDoc(DOC, 2).toc.map((e) => e.text)).toEqual(['Bedienung', 'Betrieb']);
    expect(renderDoc(DOC, 2).toc.map((e) => e.index)).toEqual([0, 1]);
  });

  it('points every entry at the matching heading of the rendered HTML', () => {
    for (const depth of [2, 3]) {
      const { html, toc } = renderDoc(DOC, depth);
      const box = document.createElement('div');
      box.innerHTML = html;
      const headings = box.querySelectorAll(tocSelector(depth));
      expect(headings.length).toBe(toc.length);
      toc.forEach((e) => expect(headings[e.index].textContent).toBe(e.text));
    }
  });

  it('renders GFM tables in a scroll box and escapes code', () => {
    const { html } = renderDoc(DOC);
    expect(html).toContain('<div class="overflow-x-auto"><table>');
    expect(html).toContain('<td><strong>Suche</strong></td>');
    expect(html).toContain('key = &quot;&lt;ollama&gt;&quot;');      // code is text, not markup
  });

  it('handles empty text and text without headings', () => {
    expect(renderDoc('')).toEqual({ html: '', toc: [] });
    expect(renderDoc('Nur ein Absatz.').toc).toEqual([]);
  });

  it('maps depth to a selector', () => {
    expect(tocSelector(2)).toBe('h2');
    expect(tocSelector(3)).toBe('h2, h3');
  });

  it('reads plain text from nested inline tokens', () => {
    expect(plainText([{ type: 'text', raw: 'a', text: 'a' }])).toBe('a');
    expect(plainText([])).toBe('');
  });
});
