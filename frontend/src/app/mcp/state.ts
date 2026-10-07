// Pure helpers for the MCP page: state wording, port owner, uptime, tool parameters.
// No Angular in here -> trivially unit-testable (see state.spec.ts).

import { McpServerStatus } from '../api/models/mcp-server-status';
import { McpToolParam } from '../api/models/mcp-tool-param';
import { Tone } from '../ui/tokens';

export interface StateView {
  label: string;     // German word next to the dot - the color is never the only signal
  dot: string;       // background class of the status dot
  tone: Tone;        // pill tone
}

export function stateView(s: McpServerStatus): StateView {
  switch (s.state) {
    case 'running':
      // runs, but nobody can reach it: show that instead of a calm green
      return s.port && s.port.open === false
        ? { label: 'Läuft – Port zu', dot: 'bg-amber-400', tone: 'warning' }
        : { label: 'Läuft', dot: 'bg-emerald-400', tone: 'normal' };
    case 'starting':
      return { label: 'Startet …', dot: 'bg-sky-400 animate-pulse', tone: 'normal' };
    case 'stopping':
      return { label: 'Stoppt …', dot: 'bg-sky-400 animate-pulse', tone: 'normal' };
    case 'crashed':
      return { label: s.exitCode === 0 ? 'Beendet' : 'Abgestürzt', dot: 'bg-red-400', tone: 'critical' };
    default:
      return { label: 'Gestoppt', dot: 'bg-gray-500', tone: 'normal' };
  }
}

/** Someone else listens on our port: "PID 4711 (python.exe)", or null when that is not the case. */
export function externalOwner(s: McpServerStatus): string | null {
  const p = s.port;
  if (!p || !p.open || p.managed) return null;
  if (s.state === 'starting' || s.state === 'running') return null;   // ours, the check just lags behind
  if (!p.pid) return 'einem unbekannten Prozess';
  return p.process ? `PID ${p.pid} (${p.process})` : `PID ${p.pid}`;
}

/** Seconds since start, measured on the server clock (serverNowMs = browser now minus clock skew). */
export function uptimeSeconds(s: McpServerStatus, serverNowMs: number): number | null {
  if (!s.startedAt || (s.state !== 'running' && s.state !== 'starting')) return null;
  const started = Date.parse(s.startedAt);
  return Number.isNaN(started) ? null : Math.max(0, (serverNowMs - started) / 1000);
}

/** "query*: string", "limit: integer = 8" - the star marks a required parameter. */
export function paramText(p: McpToolParam): string {
  const def = p.default !== null && p.default !== undefined ? ` = ${p.default}` : '';
  return `${p.name}${p.required ? '*' : ''}: ${p.type}${def}`;
}

/** Newest wins: an HTTP answer that arrives after a newer SSE update must not roll the card back. */
export function isNewer(next: McpServerStatus, current: McpServerStatus | undefined): boolean {
  return !current || next.revision >= current.revision;
}
