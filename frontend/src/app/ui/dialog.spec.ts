import '../testing/dialog-polyfill';

import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { Dialog } from './dialog';

@Component({
  imports: [Dialog],
  template: `
    <app-dialog title="Testdialog" [open]="open()" (dismiss)="open.set(false)">
      <p class="body">Inhalt</p>
      @if (withActions) { <div dialogActions><button class="ok">OK</button></div> }
    </app-dialog>`,
})
class Host {
  readonly open = signal(true);
  withActions = true;
}

describe('Dialog', () => {
  it('renders title, content and actions, labelled by its title', () => {
    const fixture = TestBed.createComponent(Host);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    const dialog = el.querySelector('dialog')!;
    const title = el.querySelector('h2')!;
    expect(dialog.open).toBe(true);
    expect(title.textContent).toContain('Testdialog');
    expect(dialog.getAttribute('aria-labelledby')).toBe(title.id);
    expect(el.querySelector('.body')?.textContent).toBe('Inhalt');
    expect(el.querySelector('footer .ok')).not.toBeNull();
  });

  it('closes via the × button through the owner state', () => {
    const fixture = TestBed.createComponent(Host);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    (el.querySelector('header button') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(fixture.componentInstance.open()).toBe(false);
    expect(el.querySelector('dialog')!.open).toBe(false);
  });

  it('gives every dialog its own title id', () => {
    const a = TestBed.createComponent(Host);
    const b = TestBed.createComponent(Host);
    a.detectChanges();
    b.detectChanges();
    expect(a.nativeElement.querySelector('h2').id).not.toBe(b.nativeElement.querySelector('h2').id);
  });
});
