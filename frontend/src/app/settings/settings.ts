import { Component } from '@angular/core';

import { ui } from '../ui/tokens';

@Component({
  selector: 'app-settings',
  imports: [],
  templateUrl: './settings.html'
})
export class Settings {
  readonly ui = ui;                                  // shared class strings (ui/tokens.ts)
}
