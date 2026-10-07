import { Marked, Renderer, Token, Tokens } from 'marked';

/**
 * Markdown -> HTML for the help page, plus a table of contents from the same tokens.
 *
 * The HTML still goes through Angular's sanitizer ([innerHTML]): the text comes from files and commit
 * messages, not from this code. Raw HTML in a HELP.md (<kbd>, <details>) is fine, <script> and
 * event handlers are removed there.
 *
 * Headings get no id attribute - the sanitizer would strip it anyway. The table of contents finds a
 * heading by its position instead: entry n = the n-th element matching tocSelector(maxDepth).
 */
export interface TocEntry {
  depth: number;          // 2 = "## …", 3 = "### …"
  text: string;           // plain text, inline Markdown removed
  index: number;          // position among the headings that tocSelector() matches
}

export interface RenderedDoc {
  html: string;
  toc: TocEntry[];
}

const md = new Marked({ gfm: true, async: false });
md.use({
  renderer: {
    // wide tables scroll inside their own box instead of widening the page on a phone
    table(token) {
      return `<div class="overflow-x-auto">${Renderer.prototype.table.call(this, token)}</div>\n`;
    },
  },
});

/** CSS selector for the headings a TOC with this depth points at: 2 -> 'h2', 3 -> 'h2, h3'. */
export function tocSelector(maxDepth: number): string {
  const levels: string[] = [];
  for (let d = 2; d <= maxDepth; d++) levels.push(`h${d}`);
  return levels.join(', ');
}

/** Inline tokens -> text: '`[log]` & **Filter**' -> '[log] & Filter'. */
export function plainText(tokens: readonly Token[]): string {
  return tokens
    .map((t) => ('tokens' in t && t.tokens?.length ? plainText(t.tokens) : 'text' in t ? String(t.text) : ''))
    .join('');
}

/**
 * Renders one help text. maxDepth limits the TOC: 3 for help pages (Bedienung > Warnungen),
 * 2 for the changelog (versions only - "Neu" and "Behoben" under every version would be noise).
 */
export function renderDoc(markdown: string, maxDepth = 3): RenderedDoc {
  const tokens = md.lexer(markdown);
  const toc: TocEntry[] = [];
  // walkTokens visits nested tokens in document order - the same order querySelectorAll returns
  md.walkTokens(tokens, (t) => {
    if (t.type !== 'heading') return;
    const h = t as Tokens.Heading;            // Token also contains a generic shape, 'type' alone does not narrow
    if (h.depth >= 2 && h.depth <= maxDepth) toc.push({ depth: h.depth, text: plainText(h.tokens), index: toc.length });
  });
  return { html: md.parser(tokens), toc };
}
