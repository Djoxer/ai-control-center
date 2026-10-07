import { TestBed } from '@angular/core/testing';

import { ClipboardService } from './clipboard.service';

/** Temporarily replace a (possibly read-only) property; returns the undo. */
function stub<T extends object>(target: T, key: string, value: unknown): () => void {
  const before = Object.getOwnPropertyDescriptor(target, key);
  Object.defineProperty(target, key, { configurable: true, value });
  return () => (before ? Object.defineProperty(target, key, before) : delete (target as Record<string, unknown>)[key]);
}

describe('ClipboardService', () => {
  const undo: (() => void)[] = [];
  afterEach(() => undo.splice(0).reverse().forEach((u) => u()));

  it('uses the Clipboard API in a secure context', async () => {
    const written: string[] = [];
    undo.push(stub(window, 'isSecureContext', true));
    undo.push(stub(navigator, 'clipboard', { writeText: async (t: string) => void written.push(t) }));
    const exec = vi.fn(() => true);
    undo.push(stub(document, 'execCommand', exec));

    expect(await TestBed.inject(ClipboardService).copy('hallo')).toBe(true);
    expect(written).toEqual(['hallo']);
    expect(exec).not.toHaveBeenCalled();
  });

  it('falls back to a textarea on http (no secure context) and cleans up', async () => {
    undo.push(stub(window, 'isSecureContext', false));
    let copied = '';
    undo.push(stub(document, 'execCommand', (cmd: string) => {
      copied = (document.activeElement as HTMLTextAreaElement).value;   // the selected textarea
      return cmd === 'copy';
    }));
    const button = document.body.appendChild(document.createElement('button'));
    button.focus();

    expect(await TestBed.inject(ClipboardService).copy('<app-icon name="x" />')).toBe(true);
    expect(copied).toBe('<app-icon name="x" />');
    expect(document.querySelector('textarea')).toBeNull();               // no leftover element
    expect(document.activeElement).toBe(button);                          // focus is back
    button.remove();
  });

  it('also falls back when the Clipboard API refuses, and reports a total failure as false', async () => {
    undo.push(stub(window, 'isSecureContext', true));
    undo.push(stub(navigator, 'clipboard', { writeText: async () => { throw new Error('denied'); } }));
    undo.push(stub(document, 'execCommand', () => false));
    expect(await TestBed.inject(ClipboardService).copy('x')).toBe(false);
  });
});
