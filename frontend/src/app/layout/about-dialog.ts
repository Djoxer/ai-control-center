import { Component, inject, input, output } from '@angular/core';

import { RouterLink } from '@angular/router';

import { ModuleInfo } from '../api/models/module-info';
import { ShellStore } from '../core/shell.store';
import { Dialog } from '../ui/dialog';
import { Tone, ui } from '../ui/tokens';
import { Icon } from './icon';

const STATE_TONE: Record<ModuleInfo['state'], Tone> = {
  running: 'normal',
  loaded: 'normal',
  starting: 'normal',
  disabled: 'normal',
  failed: 'critical',
};

/** "Über AI Control Center": version, backend state and module states from /api/v1/health. */
@Component({
  selector: 'app-about-dialog',
  imports: [Dialog, Icon, RouterLink],
  template: `
    <app-dialog title="Über AI Control Center" [open]="open()" (dismiss)="dismiss.emit()">
      <p>Kontrollebene für den AI-Rechner: Modelle, Ressourcen, Messwerte, Logs.
        Gearbeitet wird in OpenWebUI – hier wird überwacht und gesteuert.</p>

      @if (shell.health(); as h) {
        <dl class="mt-4 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5">
          <dt class="text-gray-400">Version</dt><dd class="text-white">{{ h.version }}</dd>
          <dt class="text-gray-400">Backend</dt><dd class="text-white">{{ shell.status() }}</dd>
          <dt class="text-gray-400">Datenbank</dt><dd class="text-white">{{ h.database ? 'erreichbar' : 'Fehler' }}</dd>
        </dl>

        <p [class]="ui.sectionTitle" class="mt-5">Module</p>
        <ul [class]="ui.divided" class="mt-2">
          @for (m of h.modules; track m.key) {
            <li class="flex items-center justify-between gap-3 py-2" [title]="m.error ?? ''">
              <span class="text-white">{{ m.title }} <span [class]="ui.meta">{{ m.key }}</span></span>
              <span [class]="ui.pill + ' ' + ui.pillTone[stateTone(m.state)]">{{ m.state }}</span>
            </li>
          }
        </ul>
      } @else {
        <p [class]="ui.errorBox" class="mt-4 px-3 py-2 text-xs">{{ shell.error() ?? 'Backend-Status noch unbekannt' }}</p>
      }

      <div dialogActions class="flex flex-wrap justify-end gap-2">
        <!-- in-app link: the router navigates, the dialog closes itself -->
        <a routerLink="/help" [queryParams]="{ doc: 'changelog' }" (click)="dismiss.emit()"
           [class]="ui.button" class="flex items-center gap-2">
          <app-icon name="history" class="size-4" /> Was ist neu
        </a>
        <a href="/docs" target="_blank" rel="noopener" [class]="ui.button" class="flex items-center gap-2">
          <app-icon name="external-link" class="size-4" /> API-Dokumentation
        </a>
      </div>
    </app-dialog>
  `,
})
export class AboutDialog {
  readonly open = input.required<boolean>();
  readonly dismiss = output<void>();

  protected readonly shell = inject(ShellStore);
  protected readonly ui = ui;

  protected stateTone(state: ModuleInfo['state']): Tone {
    return STATE_TONE[state];
  }
}
