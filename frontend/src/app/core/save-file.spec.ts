import { saveText } from './save-file';

describe('saveText', () => {
  it('hands the text to the browser as a named file and releases the URL afterwards', async () => {
    const blobs: Blob[] = [];
    const created = vi.spyOn(URL, 'createObjectURL').mockImplementation((b) => (blobs.push(b as Blob), 'blob:x'));
    const revoked = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const clicks: HTMLAnchorElement[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicks.push(this);
    });
    try {
      saveText('# Hallo', 'export.md', 'text/markdown;charset=utf-8');
      expect(clicks[0].download).toBe('export.md');
      expect(clicks[0].getAttribute('href')).toBe('blob:x');
      expect(await blobs[0].text()).toBe('# Hallo');
      expect(blobs[0].type).toBe('text/markdown;charset=utf-8');
      expect(revoked).not.toHaveBeenCalled();                  // not before the browser picked it up
      await new Promise((r) => setTimeout(r, 0));
      expect(revoked).toHaveBeenCalledWith('blob:x');
    } finally {
      created.mockRestore();
      revoked.mockRestore();
      click.mockRestore();
    }
  });
});
