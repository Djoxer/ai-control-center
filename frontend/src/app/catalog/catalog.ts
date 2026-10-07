import { Component } from '@angular/core';

import { ui } from '../ui/tokens';

@Component({
  selector: 'app-catalog',
  imports: [],
  templateUrl: './catalog.html'
})
export class Catalog {
  readonly ui = ui;                                  // shared class strings (ui/tokens.ts)
}
