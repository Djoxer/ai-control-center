import { LogEntry } from '../api/models/log-entry';
import { LogFilter, matchesFilter } from './log-filter';

const entry = (over: Partial<LogEntry> = {}): LogEntry => ({
  source: 's', file: 'f', ts: null, level: 'INFO', logger: 'control_center.core', msg: 'hello', ...over,
});
const none: LogFilter = { minLevel: null, loggerPrefix: '', exclude: [], query: '' };

describe('matchesFilter', () => {
  it('passes everything without filters', () => {
    expect(matchesFilter(entry({ level: null, logger: null }), none)).toBe(true);
  });

  it('applies the minimum level and hides lines without a level', () => {
    const f = { ...none, minLevel: 'WARNING' as const };
    expect(matchesFilter(entry({ level: 'ERROR' }), f)).toBe(true);
    expect(matchesFilter(entry({ level: 'INFO' }), f)).toBe(false);
    expect(matchesFilter(entry({ level: null }), f)).toBe(false);
  });

  it('filters by logger prefix and exclude list', () => {
    expect(matchesFilter(entry(), { ...none, loggerPrefix: 'control_center' })).toBe(true);
    expect(matchesFilter(entry({ logger: 'uvicorn.access' }), { ...none, exclude: ['uvicorn.access'] })).toBe(false);
    expect(matchesFilter(entry({ logger: null }), { ...none, exclude: ['uvicorn.access'] })).toBe(true);
  });

  it('searches case-insensitively in msg, exc and logger', () => {
    expect(matchesFilter(entry({ exc: 'Traceback: CUDA error' }), { ...none, query: 'cuda' })).toBe(true);
    expect(matchesFilter(entry(), { ...none, query: 'core' })).toBe(true);
    expect(matchesFilter(entry(), { ...none, query: 'nope' })).toBe(false);
  });
});
