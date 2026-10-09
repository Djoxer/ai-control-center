/**
 * Single source for the Tailwind class strings that make up the look of the app.
 *
 * Rules
 * - A token describes WHAT an element is (card, field, section title), not how it looks.
 * - Tokens carry the look: color, border, radius, typography, and spacing only where an element
 *   always has the same inner spacing (buttons, fields, badges).
 * - Layout around an element (margins, grid, width, padding of cards and table cells) stays in the
 *   template - it differs from place to place, and two conflicting utilities on one element
 *   (p-4 from a token, p-2 from the template) would be decided by CSS order, not by intent.
 * - One-off decorations stay in their template; a string moves here once it repeats or once it is
 *   an element the style guide should show.
 *
 * Templates bind them: <article [class]="ui.card" class="p-4">. Angular merges the static class
 * attribute with the [class] binding. Tailwind scans this file, so every class listed here is
 * generated even if no template spells it out.
 *
 * The /dev style guide (roadmap step 4) renders and copies every entry from UI_DOCS.
 */

export type Tone = 'normal' | 'warning' | 'critical';
export type Level = 'info' | 'warning' | 'critical';
/** Where a number comes from (catalog): seen on this GPU, computed before loading, taken over from BenchLM. */
export type Origin = 'measured' | 'estimated' | 'adopted';

export const ui = {
  // ---- surfaces ---------------------------------------------------------------------------------
  /** Frame of every panel. Padding comes from the template (p-4 for text, none around tables). */
  card: 'rounded-xl border border-white/10 bg-white/5',
  /** Footer bar of a panel (counts, "load older" button): separator line, small grey text. */
  cardFoot: 'border-t border-white/10 text-xs text-gray-500',
  /** Info line at the bottom of a KPI tile (GPU name, MiB, fan) - a shade brighter than cardFoot. */
  tileFoot: 'border-t border-white/10 text-xs text-gray-400',
  /** Divider between list or table rows. */
  divided: 'divide-y divide-white/5',

  // ---- text -------------------------------------------------------------------------------------
  /** Small upper-case heading on top of a card or section. */
  sectionTitle: 'text-xs font-medium tracking-wide text-gray-400 uppercase',
  /** Grey addition behind a section title ("· MiB", "· alle 10 s"). */
  titleDetail: 'text-gray-500 normal-case',
  /** Big number of a KPI tile. */
  kpiValue: 'text-3xl font-semibold text-white',
  /** KPI tile without a value ("—"). */
  kpiEmpty: 'text-3xl font-semibold text-gray-500',
  /** Secondary information: versions, counts, units. */
  meta: 'text-xs text-gray-500',
  /** Placeholder text for empty lists and loading states; padding from the template. */
  empty: 'text-center text-sm text-gray-500',
  /** Machine text to read or copy: command lines, program output, export previews. Padding and height
   *  from the template; whitespace handling (pre / break-all) too, it depends on the content. */
  codeBlock: 'rounded-lg border border-white/10 bg-gray-950 font-mono text-xs/5 text-gray-200',
  /**
   * Rendered Markdown (help pages): styles the generated h2, p, lists, tables, code from the container,
   * because the elements come from marked and carry no classes. Spacing between the elements is part of
   * the look here (reading rhythm); the box around it (padding, width) comes from the template.
   * Code in table cells may break anywhere: long setting names would otherwise push a table wider than
   * a phone screen.
   */
  prose:
    'text-sm/6 text-gray-300 [&>*:first-child]:mt-0 ' +
    '[&_h2]:mt-10 [&_h2]:mb-3 [&_h2]:scroll-mt-24 [&_h2]:border-b [&_h2]:border-white/10 [&_h2]:pb-2 ' +
    '[&_h2]:text-lg [&_h2]:font-semibold [&_h2]:text-white ' +
    '[&_h3]:mt-8 [&_h3]:mb-2 [&_h3]:scroll-mt-24 [&_h3]:text-base [&_h3]:font-semibold [&_h3]:text-white ' +
    '[&_h4]:mt-6 [&_h4]:mb-1 [&_h4]:font-semibold [&_h4]:text-gray-200 ' +
    '[&_p]:my-3 [&_ul]:my-3 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-3 [&_ol]:list-decimal [&_ol]:pl-5 ' +
    '[&_li]:my-1 [&_li]:marker:text-gray-500 ' +
    '[&_a]:text-sky-400 [&_a]:underline [&_a]:underline-offset-2 [&_a:hover]:text-sky-300 ' +
    '[&_strong]:font-semibold [&_strong]:text-white ' +
    '[&_code]:rounded [&_code]:bg-white/10 [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.85em] ' +
    '[&_code]:text-gray-100 ' +
    '[&_pre]:my-4 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-white/10 ' +
    '[&_pre]:bg-gray-950 [&_pre]:p-4 [&_pre]:text-xs/5 [&_pre_code]:bg-transparent [&_pre_code]:p-0 ' +
    '[&_pre_code]:text-[1em] ' +
    '[&_table]:my-4 [&_table]:w-full [&_table]:text-left ' +
    '[&_th]:border-b [&_th]:border-white/10 [&_th]:px-3 [&_th]:py-2 [&_th]:text-xs [&_th]:font-medium [&_th]:text-gray-400 ' +
    '[&_td]:border-b [&_td]:border-white/5 [&_td]:px-3 [&_td]:py-2 [&_td]:align-top ' +
    '[&_th:first-child]:pl-0 [&_td:first-child]:pl-0 [&_td_code]:[overflow-wrap:anywhere] ' +
    '[&_blockquote]:my-4 [&_blockquote]:border-l-2 [&_blockquote]:border-sky-400/40 [&_blockquote]:pl-4 ' +
    '[&_blockquote]:text-gray-400 [&_hr]:my-8 [&_hr]:border-white/10 ' +
    '[&_kbd]:rounded [&_kbd]:border [&_kbd]:border-white/20 [&_kbd]:px-1 [&_kbd]:font-mono [&_kbd]:text-xs',

  // ---- forms ------------------------------------------------------------------------------------
  /** Label above a field; the field itself goes inside the label element. */
  fieldLabel: 'block text-xs font-medium tracking-wide text-gray-400 uppercase',
  /** Text input and select. normal-case because labels are upper-case. Width from the template. */
  field: 'rounded-lg border border-white/10 bg-gray-900 px-3 py-2 text-sm text-white normal-case placeholder:text-gray-600',
  /** Checkbox or radio button. */
  check: 'size-4 accent-sky-400',
  /** Label wrapping a checkbox or radio button with its text. */
  checkLabel: 'flex items-center gap-2 text-sm text-gray-300',

  // ---- buttons ----------------------------------------------------------------------------------
  /** Secondary action ("Ältere laden"). */
  button: 'rounded-lg border border-white/10 px-3 py-1.5 text-sm text-gray-300 hover:bg-white/5 disabled:opacity-50',
  /** Same, compact - inside small text like a card footer. */
  buttonSmall: 'rounded-lg border border-white/10 px-2.5 py-1 text-xs text-gray-300 hover:bg-white/5 disabled:opacity-50',
  /** Action that ends or removes something (stop a server). Always behind a confirmation dialog. */
  buttonDanger:
    'rounded-lg border border-red-500/40 px-3 py-1.5 text-sm text-red-200 hover:bg-red-500/10 disabled:opacity-50',
  /**
   * Selectable tab-like button or link. Selected: aria-pressed="true" on a button (filter, source),
   * aria-current="page" on a link (help topics - they change the URL).
   */
  toggle:
    'rounded-lg border border-white/10 px-3 py-1.5 text-sm font-medium text-gray-300 hover:bg-white/5 ' +
    'aria-pressed:border-sky-400/50 aria-pressed:bg-sky-400/10 aria-pressed:text-white ' +
    'aria-[current=page]:border-sky-400/50 aria-[current=page]:bg-sky-400/10 aria-[current=page]:text-white',
  /** Icon-only button (menu, close). Negative margin: big hit area without shifting the layout. */
  iconButton: '-m-2.5 flex items-center p-2.5 text-gray-400 hover:text-white',
  /** Small rounded button with a status dot and text (backend status in the top bar). */
  chip: 'flex items-center gap-x-2 rounded-full border border-white/10 px-3 py-1 text-xs text-gray-300 hover:bg-white/5',
  /** Frame of a segmented control (several small options in one box). */
  segmented: 'flex rounded-lg border border-white/10 p-0.5 text-xs',
  /** One option of a segmented control; selected via aria-pressed="true". */
  segment: 'rounded-md px-2.5 py-1 text-gray-400 hover:text-white aria-pressed:bg-white/10 aria-pressed:text-white',

  // ---- overlays (native <dialog>, dropdown menu) -------------------------------------------------
  /** Modal dialog panel. Centering and width come from <app-dialog>; the backdrop dims the page. */
  dialog: 'rounded-xl border border-white/10 bg-gray-900 text-sm text-gray-300 shadow-2xl backdrop:bg-gray-950/80',
  /** Heading inside a dialog. */
  dialogTitle: 'text-base font-semibold text-white',
  /** Dropdown panel of <app-menu>. */
  menu: 'rounded-lg border border-white/10 bg-gray-800 py-1 shadow-lg',
  /** One entry of a dropdown menu (button or link with role="menuitem"). */
  menuItem:
    'flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-gray-200 ' +
    'hover:bg-white/5 focus:bg-white/5 focus:outline-hidden',

  // ---- tables -----------------------------------------------------------------------------------
  /** Table body text. Cell padding stays in the template (density differs per table). */
  table: 'w-full text-left text-sm text-gray-300',
  /** Header row group. */
  thead: 'text-xs text-gray-500',
  /** Line under the header row. */
  headRow: 'border-b border-white/10',

  // ---- notices ----------------------------------------------------------------------------------
  /** Technical error box (request failed). Size and padding from the template. */
  errorBox: 'rounded-lg border border-red-500/30 bg-red-500/10 text-red-300',
  /** Warning or hint with icon - color by level, always together with an icon and a level word. */
  callout: {
    critical: 'border-red-500/40 bg-red-500/10 text-red-200',
    warning: 'border-amber-400/40 bg-amber-400/10 text-amber-100',
    info: 'border-sky-400/30 bg-sky-400/10 text-sky-100',
  } satisfies Record<Level, string>,
  /** Frame of a callout; the color comes from ui.callout[level]. */
  calloutFrame: 'flex items-start gap-3 rounded-lg border px-4 py-3 text-sm',
  /** Small rounded label; color from ui.pillTone. */
  pill: 'rounded-full border px-2 py-0.5 text-[11px]',
  pillTone: {
    normal: 'border-white/10 text-gray-400',
    warning: 'border-amber-400/40 text-amber-200',
    critical: 'border-red-500/40 text-red-200',
  } satisfies Record<Tone, string>,
  /** Team label on an item ("OpenCode", "OpenWebUI" in the catalog): set by people, not measured. */
  tag: 'rounded-md bg-sky-400/10 px-1.5 py-0.5 text-[11px] font-medium text-sky-200',
  /** Status dot; color via [class.bg-…] next to it, meaning always also in text. */
  dot: 'size-2 shrink-0 rounded-full',

  // ---- tone colors (meters, bars, icons) --------------------------------------------------------
  /** Fill of a meter or bar segment. */
  fill: {
    normal: 'bg-sky-400',
    warning: 'bg-amber-400',
    critical: 'bg-red-400',
  } satisfies Record<Tone, string>,
  /** Icon color next to an event or message. */
  iconTone: {
    info: 'text-gray-500',
    warning: 'text-amber-400',
    critical: 'text-red-400',
  } satisfies Record<Level, string>,
  /**
   * Text color of a value by its origin (catalog). Facts from Ollama stay plain white; the color is never
   * alone: estimates carry "≈", measured and adopted values a word next to them.
   */
  origin: {
    measured: 'text-emerald-300',
    estimated: 'text-amber-200',
    adopted: 'text-violet-300',
  } satisfies Record<Origin, string>,
} as const;

export type UiToken = keyof typeof ui;

export interface UiDoc {
  group: 'Flächen' | 'Text' | 'Formular' | 'Buttons' | 'Overlays' | 'Tabellen' | 'Hinweise' | 'Farben';
  label: string;                 // German name shown in the style guide
}

/** Style guide entries - one per token, enforced by the Record type. */
export const UI_DOCS: Record<UiToken, UiDoc> = {
  card: { group: 'Flächen', label: 'Karte' },
  cardFoot: { group: 'Flächen', label: 'Panel-Fußzeile' },
  tileFoot: { group: 'Flächen', label: 'Kachel-Infozeile' },
  divided: { group: 'Flächen', label: 'Trennlinien zwischen Zeilen' },
  sectionTitle: { group: 'Text', label: 'Abschnittstitel' },
  titleDetail: { group: 'Text', label: 'Titel-Zusatz' },
  kpiValue: { group: 'Text', label: 'Kennzahl' },
  kpiEmpty: { group: 'Text', label: 'Kennzahl ohne Wert' },
  meta: { group: 'Text', label: 'Nebeninfo' },
  empty: { group: 'Text', label: 'Leer- und Ladezustand' },
  codeBlock: { group: 'Text', label: 'Code-/Ausgabeblock' },
  prose: { group: 'Text', label: 'Markdown-Text (Hilfe)' },
  fieldLabel: { group: 'Formular', label: 'Feldbeschriftung' },
  field: { group: 'Formular', label: 'Eingabefeld / Auswahl' },
  check: { group: 'Formular', label: 'Checkbox / Radio' },
  checkLabel: { group: 'Formular', label: 'Checkbox-Beschriftung' },
  button: { group: 'Buttons', label: 'Button' },
  buttonSmall: { group: 'Buttons', label: 'Button klein' },
  buttonDanger: { group: 'Buttons', label: 'Button „beenden/löschen“' },
  toggle: { group: 'Buttons', label: 'Umschalter (Tab)' },
  iconButton: { group: 'Buttons', label: 'Icon-Button' },
  chip: { group: 'Buttons', label: 'Status-Chip' },
  segmented: { group: 'Buttons', label: 'Segment-Gruppe' },
  segment: { group: 'Buttons', label: 'Segment' },
  dialog: { group: 'Overlays', label: 'Dialog' },
  dialogTitle: { group: 'Overlays', label: 'Dialog-Titel' },
  menu: { group: 'Overlays', label: 'Menü' },
  menuItem: { group: 'Overlays', label: 'Menüeintrag' },
  table: { group: 'Tabellen', label: 'Tabelle' },
  thead: { group: 'Tabellen', label: 'Tabellenkopf' },
  headRow: { group: 'Tabellen', label: 'Kopfzeile' },
  errorBox: { group: 'Hinweise', label: 'Fehlerbox' },
  callout: { group: 'Hinweise', label: 'Hinweis nach Stufe' },
  calloutFrame: { group: 'Hinweise', label: 'Hinweis-Rahmen' },
  pill: { group: 'Hinweise', label: 'Pille' },
  pillTone: { group: 'Hinweise', label: 'Pillen-Farbe' },
  tag: { group: 'Hinweise', label: 'Team-Markierung' },
  dot: { group: 'Hinweise', label: 'Statuspunkt' },
  fill: { group: 'Farben', label: 'Balkenfarbe nach Ton' },
  iconTone: { group: 'Farben', label: 'Iconfarbe nach Stufe' },
  origin: { group: 'Farben', label: 'Wert nach Herkunft' },
};
