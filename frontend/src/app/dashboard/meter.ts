import { Component, computed, input } from '@angular/core';

import { Tone, ui } from '../ui/tokens';

export type MeterTone = Tone;

/**
 * One ratio against a limit (VRAM, RAM, disk, power). Thin bar on a track of the same family.
 * The tone only repeats what a text label next to it already says - color is never the only signal.
 */
@Component({
  selector: 'app-meter',
  host: { class: 'block' },
  template: `
    <div class="h-1.5 rounded-full bg-white/10" role="meter" aria-valuemin="0" aria-valuemax="100"
         [attr.aria-valuenow]="clamped()" [attr.aria-label]="label()" [attr.title]="hint() || null">
      @if (clamped() !== null) {
        <div class="h-1.5 rounded-full transition-[width] duration-500" [class]="toneClass()"
             [style.width.%]="clamped()"></div>
      }
    </div>
  `,
})
export class Meter {
  readonly value = input<number | null | undefined>(null);   // percent, 0..100; null = unknown (empty track)
  readonly tone = input<MeterTone>('normal');
  readonly label = input('');                                  // screen readers
  readonly hint = input('');                                   // hover text with the exact numbers

  readonly clamped = computed(() => {
    const v = this.value();
    return v === null || v === undefined || Number.isNaN(v) ? null : Math.max(0, Math.min(100, v));
  });
  readonly toneClass = computed(() => ui.fill[this.tone()]);
}
