import { UiToken, ui } from '../ui/tokens';

/**
 * Style-guide samples: one per token, written once in a tiny template dialect and rendered twice.
 *
 *   class="{card} p-4"            token placeholders inside class attributes, layout classes next to them
 *   class="{callout.critical}"    nested tone maps with a dot
 *   <icon name="warning" class="size-5"/>   stands for <app-icon> (innerHTML cannot render components)
 *
 * renderHtml()    -> plain HTML with resolved Tailwind classes (preview, AI chats, other projects)
 * renderAngular() -> template code as written in this repo: [class]="ui.card" class="p-4", <app-icon …>
 *
 * Overlay components get an explicit Angular snippet: their static look is a panel, their use is a component.
 */
export interface Sample {
  html: string;
  angular?: string;          // override for components (<app-dialog>, <app-menu>)
}

const PLACEHOLDER = /\{([a-zA-Z]+(?:\.[a-zA-Z]+)?)\}/g;
const CLASS_ATTR = /class="([^"]*)"/g;
const ICON_TAG = /<icon\s+name="([^"]+)"((?:\s+[a-z-]+="[^"]*")*)\s*\/>/g;

/** "card" or "fill.normal" -> its class string; unknown or non-string paths are a bug in a sample. */
export function resolveToken(path: string): string {
  const [key, sub] = path.split('.');
  const top: unknown = (ui as Record<string, unknown>)[key];
  const value: unknown = sub === undefined ? top
    : typeof top === 'object' && top !== null ? (top as Record<string, unknown>)[sub] : undefined;
  if (typeof value !== 'string') throw new Error(`unknown ui token in sample: {${path}}`);
  return value;
}

const squash = (s: string) => s.replace(/\s+/g, ' ').trim();

export function renderHtml(sample: string): string {
  return sample
    .replace(ICON_TAG, (_m, name: string, attrs: string) =>
      // same markup <app-icon> renders; the sprite path is absolute so it works on every page
      `<svg${attrs} fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="square" aria-hidden="true">` +
      `<use href="/icons.svg#${name}"></use></svg>`)
    .replace(CLASS_ATTR, (_m, cls: string) =>
      `class="${squash(cls.replace(PLACEHOLDER, (_p, path: string) => resolveToken(path)))}"`);
}

export function renderAngular(sample: string): string {
  return sample
    .replace(ICON_TAG, (_m, name: string, attrs: string) => `<app-icon name="${name}"${attrs} />`)
    .replace(CLASS_ATTR, (whole, cls: string) => {
      const tokens = [...cls.matchAll(PLACEHOLDER)].map((m) => {
        resolveToken(m[1]);                                // fail early on typos
        return `ui.${m[1]}`;
      });
      if (!tokens.length) return whole;
      const rest = squash(cls.replace(PLACEHOLDER, ''));
      // one [class] binding per element: several tokens are joined with spaces
      const binding = `[class]="${tokens.join(" + ' ' + ")}"`;
      return rest ? `${binding} class="${rest}"` : binding;
    });
}

const CALLOUT = (level: string, icon: string, word: string, text: string) =>
  `<div class="{calloutFrame} {callout.${level}}"><icon name="${icon}" class="mt-0.5 size-5"/>` +
  `<p><span class="font-semibold">${word}:</span> ${text}</p></div>`;
const TABLE =
  '<table class="{table}"><thead class="{thead}"><tr class="{headRow}">' +
  '<th class="px-4 py-2 font-medium">Prozess</th><th class="px-4 py-2 text-right font-medium">RAM</th></tr></thead>' +
  '<tbody class="{divided}"><tr><td class="px-4 py-2 text-white">ollama.exe</td><td class="px-4 py-2 text-right">4,2 GiB</td></tr>' +
  '<tr><td class="px-4 py-2 text-white">python.exe</td><td class="px-4 py-2 text-right">124 MiB</td></tr></tbody></table>';
const SEGMENTS =
  '<div class="{segmented} w-fit" role="group" aria-label="Zeitraum">' +
  '<button type="button" class="{segment}" aria-pressed="true">1 h</button>' +
  '<button type="button" class="{segment}" aria-pressed="false">24 h</button>' +
  '<button type="button" class="{segment}" aria-pressed="false">7 T</button></div>';
const MENU_ITEMS =
  '<button type="button" role="menuitem" class="{menuItem}"><icon name="info" class="size-4 text-gray-400"/> Über AI Control Center</button>' +
  '<a role="menuitem" href="/docs" target="_blank" rel="noopener" class="{menuItem}"><icon name="external-link" class="size-4 text-gray-400"/> API-Dokumentation</a>';

/** Every token has a sample - the Record type makes a missing one a compile error. */
export const SAMPLES: Record<UiToken, Sample> = {
  card: { html: '<article class="{card} p-4"><p class="{sectionTitle}">Titel</p><p class="mt-2 text-sm">Inhalt der Karte</p></article>' },
  cardFoot: {
    html: '<div class="{card}"><p class="p-4 text-sm">Inhalt</p><footer class="{cardFoot} flex items-center justify-between px-4 py-2">' +
      '<span>12 Ereignisse</span><button type="button" class="{buttonSmall}">Ältere laden</button></footer></div>',
  },
  tileFoot: {
    html: '<article class="{card} p-4"><p class="{sectionTitle}">VRAM</p><p class="{kpiValue} mt-3">58,8 %</p>' +
      '<p class="{tileFoot} mt-6 pt-3">9.580 / 16.303 MiB · frei 6.723 MiB</p></article>',
  },
  divided: { html: '<ul class="{divided} text-sm"><li class="py-2">Ollama</li><li class="py-2">MCP-Server</li><li class="py-2">Qdrant</li></ul>' },
  sectionTitle: { html: '<p class="{sectionTitle}">Geladene Modelle</p>' },
  titleDetail: { html: '<p class="{sectionTitle}">VRAM <span class="{titleDetail}">· MiB</span></p>' },
  kpiValue: { html: '<p class="{kpiValue}">41 %</p>' },
  kpiEmpty: { html: '<p class="{kpiEmpty}">—</p>' },
  meta: { html: '<p class="{meta}">Version 0.35.0 · &lt; 1 ms</p>' },
  empty: { html: '<p class="{empty} py-6">Noch keine Ereignisse</p>' },
  codeBlock: {
    html: '<pre class="{codeBlock} overflow-x-auto px-3 py-2">C:\\rag\\.venv\\Scripts\\python.exe mcp_server.py\n' +
      'INFO:     Uvicorn running on http://0.0.0.0:8000</pre>',
  },
  prose: {
    // what marked produces from a HELP.md: plain elements, styled from the container
    html: '<div class="{prose} max-w-xl"><h2>Bedienung</h2><p>Oben ein Knopf pro <strong>Quelle</strong>, ' +
      'z. B. <code>ollama</code>.</p><h3>Filter</h3><ul><li>Level ab</li><li>Suche</li></ul>' +
      '<div class="overflow-x-auto"><table><thead><tr><th>Einstellung</th><th>Standard</th></tr></thead>' +
      '<tbody><tr><td><code>temp_warn_c</code></td><td>83</td></tr></tbody></table></div>' +
      '<pre><code>[modules.dashboard]\ntemp_warn_c = 80</code></pre></div>',
  },
  fieldLabel: { html: '<label class="{fieldLabel}">Suche<input class="{field} mt-1 w-full" placeholder="Text in Meldung"></label>' },
  field: {
    html: '<div class="flex flex-wrap gap-2"><input class="{field}" placeholder="z. B. control_center.modules">' +
      '<select class="{field}"><option>alle</option><option>WARNING</option></select></div>',
  },
  check: { html: '<input type="checkbox" checked class="{check}">' },
  checkLabel: { html: '<label class="{checkLabel}"><input type="checkbox" checked class="{check}"> API-Zugriffe ausblenden</label>' },
  button: {
    html: '<div class="flex gap-2"><button type="button" class="{button}">Ältere laden</button>' +
      '<button type="button" class="{button}" disabled>Deaktiviert</button></div>',
  },
  buttonSmall: { html: '<button type="button" class="{buttonSmall}">Ältere laden</button>' },
  buttonDanger: {
    html: '<div class="flex gap-2"><button type="button" class="{button}">Abbrechen</button>' +
      '<button type="button" class="{buttonDanger} flex items-center gap-2"><icon name="stop" class="size-4"/> Stoppen</button></div>',
  },
  iconButton: { html: '<button type="button" class="{iconButton}"><span class="sr-only">Menü öffnen</span><icon name="menu" class="size-6"/></button>' },
  chip: {
    html: '<button type="button" class="{chip}"><span class="{dot} bg-emerald-400"></span><span>ok</span>' +
      '<span class="text-gray-500">v0.5.0</span></button>',
  },
  toggle: {
    html: '<div class="flex gap-2"><button type="button" class="{toggle}" aria-pressed="true">AI Control Center</button>' +
      '<button type="button" class="{toggle}" aria-pressed="false">Ollama</button></div>',
  },
  segmented: { html: SEGMENTS },
  segment: { html: SEGMENTS },
  dialog: {
    html: '<div class="{dialog} max-w-sm"><div class="flex items-start justify-between gap-4 border-b border-white/10 px-5 py-4">' +
      '<h2 class="{dialogTitle}">Dialogtitel</h2><icon name="close" class="size-5 text-gray-400"/></div>' +
      '<p class="px-5 py-4">Inhalt des Dialogs.</p></div>',
    angular:
      '<app-dialog title="Dialogtitel" [open]="open()" (dismiss)="open.set(false)">\n' +
      '  <p>Inhalt des Dialogs.</p>\n' +
      '  <div dialogActions><button type="button" [class]="ui.button" (click)="open.set(false)">OK</button></div>\n' +
      '</app-dialog>',
  },
  dialogTitle: { html: '<h2 class="{dialogTitle}">Über AI Control Center</h2>' },
  menu: {
    html: `<div class="{menu} w-64" role="menu">${MENU_ITEMS}</div>`,
    angular:
      '<app-menu label="Anwendungsmenü">\n' +
      '  <app-icon menuTrigger name="more-vertical" class="size-6" />\n' +
      '  <button type="button" role="menuitem" [class]="ui.menuItem" (click)="doSomething()">Eintrag</button>\n' +
      '  <a role="menuitem" href="/docs" target="_blank" [class]="ui.menuItem">Link</a>\n' +
      '</app-menu>',
  },
  menuItem: { html: `<div class="{menu} w-64">${MENU_ITEMS}</div>` },
  table: { html: TABLE },
  thead: { html: TABLE },
  headRow: { html: TABLE },
  errorBox: { html: '<p class="{errorBox} px-4 py-3 text-sm" role="alert">Backend nicht erreichbar</p>' },
  callout: {
    html: '<div class="space-y-2">' +
      CALLOUT('critical', 'error', 'Kritisch', 'Teil-Offload – nur 84 % auf der GPU, Absturzgefahr.') +
      CALLOUT('warning', 'warning', 'Warnung', 'Nur noch 403 MiB VRAM frei.') +
      CALLOUT('info', 'info', 'Hinweis', 'Ollama wurde aktualisiert.') + '</div>',
  },
  calloutFrame: { html: CALLOUT('warning', 'warning', 'Warnung', 'Qdrant ist nicht erreichbar.') },
  pill: { html: '<span class="{pill} {pillTone.normal}">Leistungslimit</span>' },
  pillTone: {
    html: '<div class="flex gap-2"><span class="{pill} {pillTone.normal}">running</span>' +
      '<span class="{pill} {pillTone.warning}">Temperatur</span><span class="{pill} {pillTone.critical}">failed</span></div>',
  },
  dot: {
    html: '<div class="space-y-1 text-sm"><p class="flex items-center gap-2"><span class="{dot} bg-emerald-400"></span>erreichbar</p>' +
      '<p class="flex items-center gap-2"><span class="{dot} bg-red-500"></span>nicht erreichbar</p></div>',
  },
  fill: {
    html: '<div class="space-y-2">' +
      ['normal w-1/3', 'warning w-3/4', 'critical w-11/12']
        .map((v) => {
          const [tone, width] = v.split(' ');
          return `<div class="h-1.5 rounded-full bg-white/10"><div class="h-1.5 ${width} rounded-full {fill.${tone}}"></div></div>`;
        })
        .join('') + '</div>',
  },
  iconTone: {
    html: '<div class="flex gap-3"><icon name="info" class="size-5 {iconTone.info}"/>' +
      '<icon name="warning" class="size-5 {iconTone.warning}"/><icon name="error" class="size-5 {iconTone.critical}"/></div>',
  },
  origin: {
    html: '<dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm"><dt class="text-gray-400">VRAM</dt>' +
      '<dd class="{origin.measured}">11,3 GiB <span class="text-xs">gemessen</span></dd>' +
      '<dt class="text-gray-400">VRAM</dt><dd class="{origin.estimated}">≈ 12,0 GiB</dd>' +
      '<dt class="text-gray-400">Coding</dt><dd class="{origin.adopted}">57,0 <span class="text-xs">BenchLM</span></dd></dl>',
  },
};

/** Angular snippet for a token: the override for components, otherwise the rendered sample. */
export function angularSnippet(token: UiToken): string {
  return SAMPLES[token].angular ?? renderAngular(SAMPLES[token].html);
}

/** The token's classes as text - nested tone maps one variant per line. */
export function classText(token: UiToken): string {
  const value = ui[token] as string | Record<string, string>;
  return typeof value === 'string'
    ? value
    : Object.entries(value).map(([k, v]) => `${k}: ${v}`).join('\n');
}
