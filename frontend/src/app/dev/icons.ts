/** One symbol of public/icons.svg, with the group comment it stands under. */
export interface IconEntry {
  name: string;   // symbol id = <app-icon name="…">
  group: string;  // "Navigation & layout", taken from the comment above it
}

/**
 * Reads symbol ids from the sprite text. Groups come from the XML comments between the symbols,
 * so adding an icon under an existing comment is all it takes - no second list to maintain.
 */
export function parseIconSprite(svgText: string): IconEntry[] {
  const doc = new DOMParser().parseFromString(svgText, 'image/svg+xml');
  if (doc.querySelector('parsererror')) return [];
  const root = doc.documentElement;
  const out: IconEntry[] = [];
  let group = 'Ohne Gruppe';
  for (const node of Array.from(root.childNodes)) {
    if (node.nodeType === Node.COMMENT_NODE) {
      group = (node.textContent ?? '').trim() || group;   // the latest comment wins
    } else if (node instanceof Element && node.localName === 'symbol' && node.id) {
      out.push({ name: node.id, group });
    }
  }
  return out;
}

/** <app-icon> snippet as used in templates; size-5 = the common inline size. */
export function iconSnippet(name: string): string {
  return `<app-icon name="${name}" class="size-5" />`;
}
