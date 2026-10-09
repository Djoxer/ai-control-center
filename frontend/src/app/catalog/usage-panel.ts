import { Component, computed, effect, input, output, signal, untracked } from '@angular/core';

import { CatalogModel } from '../api/models/catalog-model';
import { ui } from '../ui/tokens';
import { USAGE, UsageTag, usageLabel } from './state';

export interface UsageChange {
  tags: UsageTag[];
  note: string | null;
}

/**
 * Content of the "Einsatz" dialog: what the team uses a model for. Checkboxes for the tags, one short note.
 * Tags the configuration already implies (RAG module) are shown, not editable - change them where they come
 * from. The page owns the request.
 */
@Component({
  selector: 'app-catalog-usage-panel',
  host: { class: 'block' },
  template: `
    <form class="space-y-4" (submit)="$event.preventDefault(); save()">
      <fieldset class="space-y-2">
        <legend [class]="ui.fieldLabel">Einsatz</legend>
        @for (u of usage; track u.key) {
          <label [class]="ui.checkLabel">
            <input type="checkbox" [class]="ui.check" [checked]="tags().includes(u.key)"
                   (change)="toggle(u.key, $any($event.target).checked)" />
            <span class="text-white">{{ u.label }}</span> <span [class]="ui.meta">{{ u.hint }}</span>
          </label>
        }
      </fieldset>
      @if (model().usage?.derived?.length) {
        <p [class]="ui.meta">
          @for (d of model().usage!.derived!; track d.tag) {
            {{ label(d.tag) }} laut {{ d.source }}.
          }
        </p>
      }
      <label class="block">
        <span [class]="ui.fieldLabel">Notiz</span>
        <input type="text" maxlength="200" [class]="ui.field" class="mt-1 w-full" [value]="note()"
               placeholder="z. B. Standard für OpenCode, Coding-Sampling" (input)="note.set($any($event.target).value)" />
      </label>
      @if (error(); as err) {
        <p [class]="ui.errorBox" class="px-3 py-2" role="alert">{{ err }}</p>
      }
      <button type="submit" [class]="ui.button" [disabled]="saving()">{{ saving() ? 'Speichert …' : 'Speichern' }}</button>
    </form>
  `,
})
export class UsagePanel {
  readonly model = input.required<CatalogModel>();
  readonly saving = input(false);
  readonly error = input<string | null>(null);
  readonly changed = output<UsageChange>();

  protected readonly ui = ui;
  protected readonly usage = USAGE;
  protected readonly label = usageLabel;

  // local draft: copied from the model when the dialog opens for it. computed(): a new overview for the same
  // model (another tab, SSE) changes the object, not the name - the draft is kept.
  private readonly name = computed(() => this.model().name);
  readonly tags = signal<UsageTag[]>([]);
  readonly note = signal('');

  constructor() {
    effect(() => {
      this.name();
      const m = untracked(() => this.model());
      this.tags.set([...(m.usage?.tags ?? [])]);
      this.note.set(m.usage?.note ?? '');
    });
  }

  toggle(tag: UsageTag, on: boolean): void {
    this.tags.update((t) => (on ? [...t.filter((x) => x !== tag), tag] : t.filter((x) => x !== tag)));
  }

  save(): void {
    const order = USAGE.map((u) => u.key);
    const tags = [...this.tags()].sort((a, b) => order.indexOf(a) - order.indexOf(b));
    this.changed.emit({ tags, note: this.note().trim() || null });
  }
}
