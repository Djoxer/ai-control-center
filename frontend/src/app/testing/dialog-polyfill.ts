/**
 * jsdom (28) has HTMLDialogElement.open but no showModal()/show()/close().
 * Import this file in specs that open dialogs. Mirrors the browser behavior the app relies on:
 * showModal() on an open dialog throws, close() fires a 'close' event only if it was open.
 */
const proto = HTMLDialogElement.prototype as HTMLDialogElement & Record<string, unknown>;

if (typeof proto.showModal !== 'function') {
  proto.showModal = function (this: HTMLDialogElement) {
    if (this.open) throw new DOMException('dialog is already open', 'InvalidStateError');
    this.setAttribute('open', '');
  };
  proto.show = proto.showModal;
  proto.close = function (this: HTMLDialogElement) {
    if (!this.open) return;
    this.removeAttribute('open');
    this.dispatchEvent(new Event('close'));
  };
}

export {};
