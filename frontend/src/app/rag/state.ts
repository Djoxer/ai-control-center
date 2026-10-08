// Pure helpers for the RAG page: state wording, progress, report lines, MCP text format.
// No Angular in here -> trivially unit-testable (see state.spec.ts).

import { CollectionInfo } from '../api/models/collection-info';
import { IncludeInfo } from '../api/models/include-info';
import { JobProgress } from '../api/models/job-progress';
import { JobStatus } from '../api/models/job-status';
import { SearchHit } from '../api/models/search-hit';
import { SourceReport } from '../api/models/source-report';
import { num } from '../dashboard/format';
import { Tone } from '../ui/tokens';

export interface StateView {
  label: string;     // German word next to the dot - the color is never the only signal
  dot: string;       // background class of the status dot
  tone: Tone;        // pill tone
}

export function jobStateView(job: JobStatus): StateView {
  switch (job.state) {
    case 'queued':
    case 'running':
      return job.cancelRequested
        ? { label: 'Bricht ab …', dot: 'bg-amber-400 animate-pulse', tone: 'warning' }
        : { label: 'Läuft …', dot: 'bg-sky-400 animate-pulse', tone: 'normal' };
    case 'done':
      return { label: 'Fertig', dot: 'bg-emerald-400', tone: 'normal' };
    case 'cancelled':
      return { label: 'Abgebrochen', dot: 'bg-amber-400', tone: 'warning' };
    default:
      return { label: 'Mit Fehlern', dot: 'bg-red-400', tone: 'critical' };
  }
}

export function sourceStateView(state: SourceReport['state']): StateView {
  switch (state) {
    case 'running':
      return { label: 'läuft', dot: 'bg-sky-400 animate-pulse', tone: 'normal' };
    case 'done':
      return { label: 'fertig', dot: 'bg-emerald-400', tone: 'normal' };
    case 'failed':
      return { label: 'fehlgeschlagen', dot: 'bg-red-400', tone: 'critical' };
    case 'cancelled':
      return { label: 'abgebrochen', dot: 'bg-amber-400', tone: 'warning' };
    default:
      return { label: 'wartet', dot: 'bg-gray-500', tone: 'normal' };
  }
}

export function isRunning(job: JobStatus | null | undefined): boolean {
  return !!job && (job.state === 'queued' || job.state === 'running');
}

const PHASES: Record<JobProgress['phase'], string> = {
  scan: 'Dateien suchen',
  embed: 'Einbetten',
  cleanup: 'Aufräumen',
};

export function phaseLabel(p: JobProgress): string {
  return PHASES[p.phase] ?? p.phase;
}

/** 0..100, null while the total is unknown (scan phase) - the meter then shows an empty track. */
export function progressPercent(p: JobProgress | null | undefined): number | null {
  if (!p || !p.total) return p?.phase === 'cleanup' ? 100 : null;
  return Math.min(100, ((p.done ?? 0) / p.total) * 100);
}

/**
 * Newest wins. Same job: higher revision. Different job: the one created later (a new job replaces the
 * report of the previous one, a late SSE echo of the old job must not bring it back).
 */
export function isNewerJob(next: JobStatus, current: JobStatus | null | undefined): boolean {
  if (!current) return true;
  if (next.id === current.id) return next.revision >= current.revision;
  return Date.parse(next.createdAt) >= Date.parse(current.createdAt);
}

export interface Fact {
  label: string;
  value: string;
  tone: Tone;        // warning/critical only where it needs attention
  hint: string;      // tooltip: what the number means
}

/** The counters of one report, in the order the help page explains them. */
export function reportFacts(r: SourceReport): Fact[] {
  const n = (v: number | undefined) => num(v ?? 0);
  return [
    { label: 'Dateien', value: n(r.files), tone: 'normal', hint: 'passende Dateien in den Include-Ordnern' },
    { label: 'Indexiert', value: n(r.indexed), tone: 'normal', hint: 'als Punkt nach Qdrant geschrieben' },
    { label: 'Abgeschnitten', value: n(r.truncated), tone: r.truncated ? 'warning' : 'normal',
      hint: 'länger als die Zeichengrenze – nur der Anfang ist durchsuchbar' },
    { label: 'Leer', value: n(r.empty), tone: 'normal', hint: 'ohne Inhalt, übersprungen' },
    { label: 'Secret-Filter', value: n(r.secret), tone: r.secret ? 'warning' : 'normal',
      hint: 'sieht nach Zugangsdaten aus – nicht indexiert' },
    { label: 'Übersprungen', value: n(r.skipped), tone: r.skipped ? 'critical' : 'normal',
      hint: 'nicht lesbar oder Ollama-Fehler – der alte Punkt bleibt' },
    { label: 'Entfernt', value: n(r.removed), tone: 'normal',
      hint: 'beim Aufräumen gelöscht: weggefallene, gefilterte und alte Skript-Punkte' },
  ];
}

/** "src → .php, .sql" */
export function includeText(i: IncludeInfo): string {
  return `${i.dir} → ${i.ext.join(', ')}`;
}

/** Qdrant's optimizer state in words. */
export function collectionStatus(c: CollectionInfo): StateView {
  switch (c.status) {
    case 'green':
      return { label: 'bereit', dot: 'bg-emerald-400', tone: 'normal' };
    case 'yellow':
      return { label: 'optimiert', dot: 'bg-amber-400', tone: 'normal' };
    case 'grey':
      return { label: 'wartet', dot: 'bg-gray-500', tone: 'normal' };
    case 'red':
      return { label: 'Fehler', dot: 'bg-red-400', tone: 'critical' };
    default:
      return { label: 'unbekannt', dot: 'bg-gray-600', tone: 'normal' };
  }
}

/** Exactly what mcp_server.py returns to a client - to compare or paste into a chat. */
export function mcpText(hits: SearchHit[]): string {
  if (!hits.length) return 'Keine Treffer gefunden.';
  return hits.map((h) => `Datei: ${h.filename ?? ''}\n${h.text ?? ''}`).join('\n\n---\n\n');
}

/** Today: "14:12", earlier: "07.10. 14:12" - reports can be days old. */
export function when(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (v: number) => String(v).padStart(2, '0');
  const time = `${p(d.getHours())}:${p(d.getMinutes())}`;
  return d.toDateString() === now.toDateString() ? time : `${p(d.getDate())}.${p(d.getMonth() + 1)}. ${time}`;
}

/** Score with two decimals, German comma: 0,73 */
export function score(value: number): string {
  return num(value, 2);
}

/**
 * Link to Qdrant's own web UI. A Qdrant on "localhost" is localhost of the control center's machine,
 * so the browser must use the host it reached the control center with; a remote Qdrant keeps its host.
 */
export function qdrantDashboardUrl(url: string | null | undefined, browserHost: string): string | null {
  if (!url) return null;
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return null;
  }
  const loopback = ['localhost', '127.0.0.1', '[::1]', '::1'].includes(u.hostname);
  if (loopback) u.hostname = browserHost;
  u.pathname = '/dashboard';
  return u.toString();
}
