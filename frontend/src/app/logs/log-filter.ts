import { LogEntry } from '../api/models/log-entry';
import { LogsEntries$Params } from '../api/fn/logs/logs-entries';

export type Level = NonNullable<LogsEntries$Params['level']>;
export const LEVELS: Level[] = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];
const RANK: Record<string, number> = { TRACE: 5, DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50 };

export interface LogFilter {
  minLevel: Level | null;
  loggerPrefix: string;      // '' = all loggers
  exclude: string[];         // logger prefixes to hide
  query: string;             // '' = no text search
}

/**
 * Same rules as EntryFilter.matches() in the backend (reader.py).
 * Used for live lines, which arrive unfiltered over SSE. Keep both in sync.
 */
export function matchesFilter(e: LogEntry, f: LogFilter): boolean {
  if (f.minLevel) {
    // lines without a level (raw text) are hidden by a level filter, like in the backend
    if (!e.level || (RANK[e.level] ?? 0) < RANK[f.minLevel]) return false;
  }
  const logger = e.logger ?? '';
  if (f.loggerPrefix && !logger.startsWith(f.loggerPrefix)) return false;
  if (f.exclude.some((p) => p && logger.startsWith(p))) return false;
  if (f.query) {
    const hay = `${e.msg}\n${e.exc ?? ''}\n${logger}`.toLowerCase();
    if (!hay.includes(f.query.toLowerCase())) return false;
  }
  return true;
}
