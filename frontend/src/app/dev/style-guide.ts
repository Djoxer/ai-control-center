import { Component, output, signal } from '@angular/core';

import { Icon } from '../layout/icon';
import { Dialog } from '../ui/dialog';
import { Menu } from '../ui/menu';
import { UI_DOCS, UiToken, ui } from '../ui/tokens';
import { groupedTokens } from './markdown';
import { SAMPLES, angularSnippet, classText, renderHtml } from './samples';
import { TrustedHtml } from './trusted-html';

interface Entry {
  token: UiToken;
  label: string;
  classes: string;
  angular: string;
  html: string;                // rendered sample: preview AND the "HTML kopieren" text
}

/** Every ui token: live preview, its classes, and both snippet forms to copy. */
@Component({
  selector: 'app-style-guide',
  imports: [Icon, Dialog, Menu, TrustedHtml],
  templateUrl: './style-guide.html',
})
export class StyleGuide {
  /** Text to put on the clipboard - the page owns clipboard and feedback. */
  readonly copy = output<string>();

  readonly ui = ui;
  readonly demoOpen = signal(false);

  // built once: samples are constants of this repo, so writing their HTML directly is safe here
  readonly groups = groupedTokens().map(({ group, tokens }) => ({
    group,
    entries: tokens.map((token): Entry => {
      return {
        token,
        label: UI_DOCS[token].label,
        classes: classText(token),
        angular: angularSnippet(token),
        html: renderHtml(SAMPLES[token].html),
      };
    }),
  }));
}
