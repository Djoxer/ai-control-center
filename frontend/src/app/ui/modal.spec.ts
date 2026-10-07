import '../testing/dialog-polyfill';

import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { Modal } from './modal';

@Component({
  imports: [Modal],
  template: `<dialog [appModal]="open()" (dismiss)="dismissed = dismissed + 1; open.set(false)"><p>inside</p></dialog>`,
})
class Host {
  readonly open = signal(false);
  dismissed = 0;
}

function setup() {
  const fixture = TestBed.createComponent(Host);
  fixture.detectChanges();
  const dialog: HTMLDialogElement = fixture.nativeElement.querySelector('dialog');
  const set = (v: boolean) => { fixture.componentInstance.open.set(v); fixture.detectChanges(); };
  return { fixture, dialog, set, host: fixture.componentInstance };
}

describe('Modal directive', () => {
  it('follows the signal in both directions without reporting owner-initiated closes', () => {
    const { dialog, set, host } = setup();
    expect(dialog.open).toBe(false);
    set(true);
    expect(dialog.open).toBe(true);
    set(true);                                   // no second showModal() -> would throw InvalidStateError
    set(false);
    expect(dialog.open).toBe(false);
    expect(host.dismissed).toBe(0);              // the owner closed it, nothing to report
  });

  it('reports a user close (Esc ends in a native close) and the owner state follows', () => {
    const { dialog, set, host, fixture } = setup();
    set(true);
    dialog.close();                              // what the browser does after Esc
    fixture.detectChanges();
    expect(host.dismissed).toBe(1);
    expect(host.open()).toBe(false);
  });

  it('closes on a backdrop click, but not on a click inside', () => {
    const { dialog, set, host, fixture } = setup();
    set(true);
    dialog.querySelector('p')!.click();
    expect(dialog.open).toBe(true);
    dialog.click();                              // backdrop clicks are reported with the dialog as target
    fixture.detectChanges();
    expect(dialog.open).toBe(false);
    expect(host.dismissed).toBe(1);
  });
});
