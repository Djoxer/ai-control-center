// Pure helpers for the catalog page: wording of verdicts, contexts, origins and server defaults.
// No Angular components in here -> trivially unit-testable (see state.spec.ts).

import { HttpErrorResponse } from '@angular/common/http';

import { BenchStatus } from '../api/models/bench-status';
import { BudgetInfo } from '../api/models/budget-info';
import { CatalogModel } from '../api/models/catalog-model';
import { CatalogOverview } from '../api/models/catalog-overview';
import { ContextInfo } from '../api/models/context-info';
import { ModelGroup } from '../api/models/model-group';
import { Observation } from '../api/models/observation';
import { ServerConfig } from '../api/models/server-config';
import { Verdict } from '../api/models/verdict';
import { GIB, num } from '../dashboard/format';
import { Origin, Tone } from '../ui/tokens';

export const OVERVIEW_TOPIC = 'catalog.overview';
export const BENCH_TOPIC = 'catalog.bench';

/** VRAM always in GiB with one decimal: "12,0 GiB" - sizes of models never need MiB precision. */
export function gib(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${num(value / GIB, 1)} GiB`;
}

export interface VerdictView {
  label: string;     // German word in the pill - the color is never the only signal
  tone: Tone;
}

const VERDICTS: Record<Verdict['state'], VerdictView> = {
  fits: { label: 'passt', tone: 'normal' },
  tight: { label: 'knapp', tone: 'warning' },
  split: { label: 'Teil-Offload', tone: 'critical' },
  cpu: { label: 'nur CPU', tone: 'warning' },
  unknown: { label: 'unklar', tone: 'normal' },
};

export function verdictView(v: Verdict): VerdictView {
  return VERDICTS[v.state] ?? VERDICTS.unknown;
}

/** Which color the VRAM figure gets: measured beats estimated; no number, no color. */
export function verdictOrigin(v: Verdict): Origin | null {
  return v.basis === 'measured' ? 'measured' : v.basis === 'estimated' ? 'estimated' : null;
}

/** Need plus what Ollama does not count, as share of what the card offers Ollama, 0..100+ (the meter clamps). */
export function vramPercent(v: Verdict): number | null {
  if (!v.needBytes || !v.availableBytes) return null;
  return ((v.needBytes + (v.extraBytes ?? 0)) / v.availableBytes) * 100;
}

/** "+0,8": what the card holds beyond Ollama's count and the reserve (measured by a test run), or ''. */
export function extraText(v: Verdict): string {
  return v.extraBytes ? `+${num(v.extraBytes / GIB, 1)}` : '';
}

/**
 * What the model needs on Ollama's scale - the same number the overview shows for a loaded model:
 * "≈ 10,8 GiB" for estimates, "11,3 GiB" for measurements.
 */
export function vramText(v: Verdict): string {
  if (!v.needBytes) return '—';
  return v.basis === 'estimated' ? `≈ ${gib(v.needBytes)}` : gib(v.needBytes);
}

/** Tooltip of the meter: what the bar compares. */
export function vramHint(v: Verdict): string {
  if (!v.needBytes || !v.availableBytes) return v.message;
  const extra = v.extraBytes ? ` + ${gib(v.extraBytes)} außerhalb Ollamas Zählung` : '';
  return `${gib(v.needBytes)} Bedarf${extra} von ${gib(v.availableBytes)}, die die Karte Ollama lässt`;
}

/** The word next to the VRAM figure: where it comes from. */
export function originWord(m: CatalogModel): string {
  if (m.verdict.basis === 'measured') return 'gemessen';
  if (m.verdict.basis === 'estimated') return m.estimate?.calibrated ? 'kalibriert' : 'geschätzt';
  return '';
}

const CONTEXT_SOURCE: Record<ContextInfo['source'], string> = {
  model: 'eigener Wert',
  server: 'Server-Standard',
  fallback: 'angenommen',
  request: 'gewählt',
};

/** Short line under the number: where the context comes from. */
export function contextSource(c: ContextInfo): string {
  const base = CONTEXT_SOURCE[c.source] ?? c.source;
  return c.clamped ? `${base}, gekürzt` : base;
}

/** The full derivation for the details dialog. */
export function contextExplain(c: ContextInfo): string {
  const parts: string[] = [];
  if (c.source === 'request') parts.push(`Gewählt: ${num(c.effective)} Token (wie ein Client es pro Anfrage verlangen kann).`);
  else if (c.source === 'model') parts.push(`Das Modell setzt selbst num_ctx ${num(c.own)}.`);
  else if (c.source === 'server') parts.push(`Kein eigener Wert – Ollama nimmt den Server-Standard ${num(c.server)}.`);
  else parts.push(`Weder Modell noch Server nennen einen Wert – angenommen: ${num(c.effective)}.`);
  if (c.clamped) parts.push(`Trainiert ist das Modell auf ${num(c.trained)} Token; Ollama kürzt darauf.`);
  if ((c.parallel ?? 1) > 1) parts.push(`${c.parallel} parallele Anfragen: der KV-Cache wird ${c.parallel}-fach angelegt.`);
  return parts.join(' ');
}

const CAPABILITIES: Record<string, string> = {
  tools: 'Tools',
  thinking: 'Denkt',
  vision: 'Bild',
  embedding: 'Einbettung',
  insert: 'Autocomplete',
};

/** Ollama capabilities as German chips; "completion" is what every chat model does - not worth a chip. */
export function capabilityChips(caps: string[] | null | undefined): string[] {
  return (caps ?? []).filter((c) => c !== 'completion').map((c) => CAPABILITIES[c] ?? c);
}

/** One line about where a model hangs in the tree, or null when the indentation says it all. */
export function relationText(m: CatalogModel): string | null {
  const p = m.parent;
  if (p.via === 'copy') return `Kopie von ${p.resolved}`;
  if (p.via === 'weights') return `gleiche Gewichte wie ${p.resolved}`;
  if (!p.resolved && p.declared) return `erstellt aus ${p.declared} (nicht installiert)`;
  return null;
}

export function shortDigest(digest: string | null | undefined): string {
  return digest ? digest.replace(/^sha256:/, '').slice(0, 12) : '—';
}

export interface GroupView {
  group: ModelGroup;
  models: CatalogModel[];          // tree order, as the backend sent it
}

/** Groups with their model objects; names the backend did not send (cannot happen) are skipped. */
export function groupViews(ov: CatalogOverview): GroupView[] {
  const byName = new Map(ov.models.map((m) => [m.name, m]));
  return ov.groups.map((group) => ({
    group,
    models: group.members.map((n) => byName.get(n)).filter((m): m is CatalogModel => !!m),
  }));
}

/**
 * Newest wins: SSE and GET answers can cross. asOf is the build time on the server - unlike the revision
 * it does not start at 0 again when the backend restarts.
 */
export function isNewerOverview(next: CatalogOverview, current: CatalogOverview | null): boolean {
  if (!current) return true;
  return Date.parse(next.asOf) >= Date.parse(current.asOf);
}

export interface Fact {
  label: string;
  value: string;
}

/** Ollama's defaults as short facts for the status line; unknown values are left out. */
export function serverFacts(s: ServerConfig): Fact[] {
  const out: Fact[] = [];
  if (s.contextLength) out.push({ label: 'Standard-Kontext', value: num(s.contextLength) });
  if (s.kvCacheType) out.push({ label: 'KV-Cache', value: s.kvCacheType });
  if (s.flashAttention !== null && s.flashAttention !== undefined) {
    out.push({ label: 'Flash Attention', value: s.flashAttention ? 'an' : 'aus' });
  }
  if (s.numParallel) out.push({ label: 'parallel', value: String(s.numParallel) });
  if (s.maxLoadedModels) out.push({ label: 'max. geladen', value: String(s.maxLoadedModels) });
  return out;
}

export function serverSourceText(s: ServerConfig): string {
  switch (s.source) {
    case 'log':
      return `aus ${s.logFile ?? 'server.log'}`;
    case 'config':
      return 'aus control-center.toml';
    case 'mixed':
      return `aus ${s.logFile ?? 'server.log'}, teils überschrieben`;
    default:
      return 'unbekannt';
  }
}

/** "65.536 Token · 11,3 GiB · 84 % GPU" */
export function observationText(o: Observation): string {
  const ctx = o.numCtx ? `${num(o.numCtx)} Token` : 'Kontext unbekannt';
  const pct = o.gpuRatio === null || o.gpuRatio === undefined ? '' : ` · ${Math.floor(o.gpuRatio * 100)} % GPU`;
  return `${ctx} · ${gib(o.sizeBytes)}${pct}`;
}

/** Today: "14:12", earlier: "07.10. 14:12". */
export function when(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (v: number) => String(v).padStart(2, '0');
  const time = `${p(d.getHours())}:${p(d.getMinutes())}`;
  return d.toDateString() === now.toDateString() ? time : `${p(d.getDate())}.${p(d.getMonth() + 1)}. ${time}`;
}

/** Parameters worth a glance in the row; the dialog lists all of them. */
export const KEY_PARAMETERS = ['num_ctx', 'temperature', 'top_p', 'top_k', 'repeat_penalty', 'num_predict'];

export function parameterList(params: Record<string, string[]> | null | undefined): Fact[] {
  return Object.entries(params ?? {})
    .sort(([a], [b]) => {
      const ia = KEY_PARAMETERS.indexOf(a), ib = KEY_PARAMETERS.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
    })
    .map(([label, values]) => ({ label, value: values.join(' · ') }));
}

/** Error of a request in German: the backend's detail when it sent one. */
export function message(e: unknown): string {
  if (e instanceof HttpErrorResponse) {
    if (e.status === 0) return 'Backend nicht erreichbar';
    const detail = (e.error as { detail?: unknown } | null)?.detail;
    if (detail === 'Katalog-Modul läuft nicht') return 'Das Katalog-Modul läuft nicht (Details unter ⋮ → Über und im Protokoll).';
    if (typeof detail === 'string') return detail;
    return `HTTP ${e.status}`;
  }
  return e instanceof Error ? e.message : String(e);
}

/** The card's budget for Ollama as short facts: total, other programs, Ollama's reserve, what is left. */
export function budgetFacts(b: BudgetInfo, now: Date = new Date()): Fact[] {
  const out: Fact[] = [];
  if (b.totalBytes) out.push({ label: 'Karte', value: gib(b.totalBytes) });
  const other = b.otherSource === 'measured'
    ? `${gib(b.otherBytes)} (gemessen ${when(b.otherMeasuredAt, now)})`
    : `${gib(b.otherBytes)} (angenommen)`;
  out.push({ label: 'andere Programme', value: other });
  out.push({ label: 'Reserve Ollama', value: `${num(b.reserveBytes / GIB, 2)} GiB` });   // 0,45: one decimal would lie
  if (b.availableBytes !== null && b.availableBytes !== undefined) out.push({ label: 'verfügbar', value: gib(b.availableBytes) });
  return out;
}

// ---- test runs ------------------------------------------------------------------------------------

export function isBenchRunning(b: BenchStatus | null | undefined): boolean {
  return !!b && (b.state === 'queued' || b.state === 'running');
}

/** Newest wins: same run -> higher revision; another run -> the one created later. */
export function isNewerBench(next: BenchStatus, current: BenchStatus | null | undefined): boolean {
  if (!current) return true;
  if (next.id === current.id) return (next.revision ?? 0) >= (current.revision ?? 0);
  return Date.parse(next.createdAt) >= Date.parse(current.createdAt);
}

const PHASES: Record<NonNullable<BenchStatus['phase']>, string> = {
  unload: 'Geladene Modelle entladen',
  baseline: 'Leere Karte messen',
  load: 'Laden und antworten',
  measure: 'Speicher messen',
  cleanup: 'Wieder entladen',
};
export const PHASE_ORDER = Object.keys(PHASES) as NonNullable<BenchStatus['phase']>[];

export function phaseLabel(p: BenchStatus['phase']): string {
  return p ? PHASES[p] ?? p : '';
}

export function benchStateView(b: BenchStatus): VerdictView {
  switch (b.state) {
    case 'queued':
    case 'running':
      return { label: 'läuft …', tone: 'normal' };
    case 'done':
      return { label: 'fertig', tone: 'normal' };
    case 'cancelled':
      return { label: 'abgebrochen', tone: 'warning' };
    default:
      return { label: 'fehlgeschlagen', tone: 'critical' };
  }
}

/** "64 tok/s · Laden 4,5 s" - or the error / state in words. */
export function benchSummary(b: BenchStatus): string {
  if (isBenchRunning(b)) return `Testlauf: ${phaseLabel(b.phase) || 'startet'} …`;
  if (b.state !== 'done' || !b.result) return `Testlauf ${benchStateView(b).label}`;
  const r = b.result;
  const parts = [];
  if (r.evalTps !== null && r.evalTps !== undefined) parts.push(`${num(r.evalTps, 1)} tok/s`);
  if (r.loadS !== null && r.loadS !== undefined) parts.push(`Laden ${num(r.loadS, 1)} s`);
  parts.push(`${num(r.actualCtx ?? r.requestedCtx)} Token`);
  return parts.join(' · ');
}
