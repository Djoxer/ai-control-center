import { UI_DOCS, UiDoc, UiToken } from '../ui/tokens';
import { IconEntry, iconSnippet } from './icons';
import { SAMPLES, angularSnippet, classText, renderHtml } from './samples';

export interface MarkdownInput {
  icons: IconEntry[];
  version: string | null;    // backend version from /api/v1/health, null when unknown
  generatedAt: Date;
}

/** Style-guide groups in display order (first appearance in UI_DOCS). */
export function groupedTokens(): { group: UiDoc['group']; tokens: UiToken[] }[] {
  const map = new Map<UiDoc['group'], UiToken[]>();
  for (const token of Object.keys(UI_DOCS) as UiToken[]) {
    const group = UI_DOCS[token].group;
    map.set(group, [...(map.get(group) ?? []), token]);
  }
  return [...map].map(([group, tokens]) => ({ group, tokens }));
}

/** Rough size for a chat context: ~4 characters per token is the usual rule of thumb for code/English. */
export function estimateTokens(text: string): number {
  return Math.ceil(text.length / 4);
}

/**
 * Everything an AI needs to write UI for this app: rules, every token with classes and both snippet
 * forms, and the icon names. YAML front matter first, so a tool can tell what and how old it is.
 */
export function buildMarkdown({ icons, version, generatedAt }: MarkdownInput): string {
  const fence = (lang: string, code: string) => '```' + lang + '\n' + code + '\n```';
  const lines: string[] = [
    '---',
    'title: AI Control Center – UI-Bausteine',
    `generated: ${generatedAt.toISOString()}`,
    `version: ${version ?? 'unbekannt'}`,
    'source: frontend/src/app/ui/tokens.ts, frontend/public/icons.svg',
    '---',
    '',
    '# UI-Bausteine – AI Control Center',
    '',
    'Angular 21 (Standalone, Signals), Tailwind v4, dunkles Theme. Regeln:',
    '',
    '- Aussehen kommt aus `ui/tokens.ts`: `[class]="ui.<token>"`. Layout (Abstände, Breiten, Zell-Padding) steht als statisches `class="…"` daneben.',
    '- Pro Element nur ein `[class]`-Binding; mehrere Tokens werden verkettet: `[class]="ui.pill + \' \' + ui.pillTone.warning"`.',
    '- Icons: `<app-icon name="…" class="size-5" />`, Größe und Farbe über Klassen.',
    '- Farbe nie als einziges Signal: Status immer auch als Text oder Icon.',
    '- Dialoge: `<app-dialog>`, Menüs: `<app-menu>` (natives `<dialog>`, kein Fremdskript).',
    '',
  ];
  for (const { group, tokens } of groupedTokens()) {
    lines.push(`## ${group}`, '');
    for (const token of tokens) {
      lines.push(
        `### ${UI_DOCS[token].label} (\`${token}\`)`,
        '',
        fence('text', classText(token)),
        '',
        'Angular:',
        '',
        fence('html', angularSnippet(token)),
        '',
        'HTML:',
        '',
        fence('html', renderHtml(SAMPLES[token].html)),
        '',
      );
    }
  }
  lines.push('## Icons', '', `Verwendung: \`${iconSnippet('<name>')}\``, '');
  const byGroup = new Map<string, string[]>();
  for (const i of icons) byGroup.set(i.group, [...(byGroup.get(i.group) ?? []), i.name]);
  for (const [group, names] of byGroup) lines.push(`- **${group}:** ${names.map((n) => `\`${n}\``).join(', ')}`);
  lines.push('');
  return lines.join('\n');
}
