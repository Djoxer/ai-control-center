/**
 * In-page jump: scroll an element to the top and move the focus there.
 *
 * Why not <a href="#id">: index.html has <base href="/">, so the browser resolves "#id" against "/"
 * and leaves the page - from /dev to "/#id", which is the overview. Buttons calling this stay on the
 * page, and the URL does not change.
 *
 * The target needs a scroll-mt-* class (or ui.prose headings) so the sticky top bar does not cover it.
 * Focus goes along so screen readers and the next Tab continue from there; tabindex="-1" makes the
 * element focusable by script without adding it to the tab order.
 */
export function jumpTo(target: HTMLElement | null | undefined): void {
  if (!target) return;
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
  target.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
  if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
  target.focus({ preventScroll: true });     // scrolling is already under way, focus must not jump again
}
