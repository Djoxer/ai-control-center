import { Component, computed, effect, input, output, signal, untracked } from '@angular/core';

import { Candidate } from '../api/models/candidate';
import { LibraryInfo } from '../api/models/library-info';
import { ui } from '../ui/tokens';
import { CandidateCard } from './candidate-card';

/**
 * Candidate view of the catalog: a name as for "ollama pull", checked against the registry BEFORE the
 * download, and the list of checked candidates (newest first). The page owns the requests; the field is
 * cleared once a check went through.
 */
@Component({
  selector: 'app-catalog-candidates',
  imports: [CandidateCard],
  host: { class: 'block space-y-4' },
  template: `
    <section [class]="ui.card" class="p-4" aria-labelledby="candidate-form-title">
      <h2 id="candidate-form-title" [class]="ui.sectionTitle">Kandidat prüfen
        <span [class]="ui.titleDetail">· vor dem Download</span></h2>
      <p class="mt-2 text-sm text-gray-300">Der Katalog liest aus der Registry nur Manifest, Konfiguration und den
        Anfang der Gewichtsdatei (die GGUF-Metadaten) – geladen wird nichts. Gerechnet wird mit derselben Formel und
        demselben GPU-Budget wie für installierte Modelle.</p>
      <form class="mt-3 flex flex-wrap items-end gap-2" (submit)="$event.preventDefault(); submit()">
        <label class="min-w-0 flex-1 basis-64">
          <span [class]="ui.fieldLabel">Modell</span>
          <input type="text" maxlength="300" [class]="ui.field" class="mt-1 w-full font-mono" [value]="text()"
                 placeholder="qwen3-coder:30b" autocomplete="off" spellcheck="false" aria-describedby="candidate-hint"
                 (input)="text.set($any($event.target).value)" />
        </label>
        <button type="submit" [class]="ui.button" [disabled]="checking() !== null || !text().trim()">
          {{ checking() !== null ? 'Prüft …' : 'Prüfen' }}</button>
      </form>
      <p id="candidate-hint" [class]="ui.meta" class="mt-2">Name wie bei <code>ollama pull</code>: qwen3-coder:30b,
        gemma3:12b, nutzer/modell:tag, hf.co/organisation/repo:Q4_K_M – oder die Adresse der Modellseite.
        Erlaubte Registries: {{ hosts() }}.
        @if (library()?.simulated) { <span class="text-amber-200">Simulation: Antworten aus library-samples.</span> }</p>
      @if (error(); as err) {
        <p [class]="ui.errorBox" class="mt-3 px-3 py-2 text-sm" role="alert">{{ err }}</p>
      }
    </section>

    @if (!candidates().length) {
      <p [class]="ui.empty" class="py-10">Noch kein Kandidat geprüft.</p>
    }
    @for (c of candidates(); track c.name) {
      <app-catalog-candidate-card [candidate]="c" [available]="available()" [busy]="checking() === c.name"
                                  (recheck)="check.emit(c.name)" (forget)="forget.emit(c.name)" />
    }
  `,
})
export class Candidates {
  readonly candidates = input.required<Candidate[]>();
  readonly library = input<LibraryInfo | null>(null);
  readonly available = input<number | null>(null);      // budget of the card (bytes)
  readonly checking = input<string | null>(null);       // what is being checked right now (as typed)
  readonly error = input<string | null>(null);
  readonly check = output<string>();
  readonly forget = output<string>();

  protected readonly ui = ui;
  readonly text = signal('');
  readonly hosts = computed(() => (this.library()?.hosts ?? []).join(', ') || '—');

  constructor() {
    // a check that ended without an error empties the field for the next name
    let before: string | null = null;
    effect(() => {
      const now = this.checking();
      const failed = this.error() !== null;
      if (before !== null && now === null && !failed && untracked(() => this.text().trim()) === before) this.text.set('');
      before = now;
    });
  }

  submit(): void {
    const name = this.text().trim();
    if (name && this.checking() === null) this.check.emit(name);
  }
}
