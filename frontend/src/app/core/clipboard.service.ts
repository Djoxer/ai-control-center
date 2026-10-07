import { DOCUMENT } from '@angular/common';
import { Injectable, inject } from '@angular/core';

/**
 * Copy text to the clipboard - also where the Clipboard API is missing.
 *
 * navigator.clipboard only exists in a secure context (https or localhost). The AI box serves the
 * app as http://<ip>:8090, so there the old way is needed: an off-screen textarea, select it,
 * document.execCommand('copy'). Deprecated, but still supported by every browser for exactly this.
 */
@Injectable({ providedIn: 'root' })
export class ClipboardService {
  private readonly doc = inject(DOCUMENT);

  /** true = copied. Never throws: a failed copy must not break the page. */
  async copy(text: string): Promise<boolean> {
    const nav = this.doc.defaultView?.navigator;
    if (this.doc.defaultView?.isSecureContext && nav?.clipboard?.writeText) {
      try {
        await nav.clipboard.writeText(text);
        return true;
      } catch {
        // permission denied or document not focused -> try the fallback below
      }
    }
    return this.copyWithTextarea(text);
  }

  private copyWithTextarea(text: string): boolean {
    const area = this.doc.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');                    // no on-screen keyboard on touch devices
    area.style.position = 'fixed';                        // off-screen, no scroll jump
    area.style.top = '-1000px';
    const previous = this.doc.activeElement as HTMLElement | null;
    this.doc.body.appendChild(area);
    area.focus({ preventScroll: true });                  // copy takes the selection of the FOCUSED element
    area.select();
    try {
      return typeof this.doc.execCommand === 'function' && this.doc.execCommand('copy');
    } catch {
      return false;
    } finally {
      area.remove();
      previous?.focus?.();                                // keyboard users keep their place
    }
  }
}
