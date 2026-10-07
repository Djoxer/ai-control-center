import { mcpStatus as status } from '../testing/mcp-status';
import { externalOwner, isNewer, paramText, stateView, uptimeSeconds } from './state';

describe('MCP state helpers', () => {
  it('words every state, the color is never alone', () => {
    expect(stateView(status()).label).toBe('Gestoppt');
    expect(stateView(status({ state: 'starting' })).label).toBe('Startet …');
    expect(stateView(status({ state: 'stopping' })).label).toBe('Stoppt …');
    expect(stateView(status({ state: 'crashed', exitCode: 1 }))).toEqual(
      { label: 'Abgestürzt', dot: 'bg-red-400', tone: 'critical' });
    expect(stateView(status({ state: 'crashed', exitCode: 0 })).label).toBe('Beendet');
  });

  it('marks a running server with a closed port as warning', () => {
    const open = status({ state: 'running', port: { port: 1, open: true, managed: true } });
    const closed = status({ state: 'running', port: { port: 1, open: false, managed: false } });
    const noPort = status({ state: 'running', port: null, url: null });
    expect(stateView(open)).toEqual({ label: 'Läuft', dot: 'bg-emerald-400', tone: 'normal' });
    expect(stateView(closed).tone).toBe('warning');
    expect(stateView(noPort).label).toBe('Läuft');                 // nothing to check, nothing to warn
  });

  it('names a foreign process on our port, but not our own', () => {
    const foreign = { port: 8000, open: true, managed: false, pid: 4711, process: 'python.exe' };
    expect(externalOwner(status({ port: foreign }))).toBe('PID 4711 (python.exe)');
    expect(externalOwner(status({ port: { ...foreign, process: null } }))).toBe('PID 4711');
    expect(externalOwner(status({ port: { ...foreign, pid: null } }))).toBe('einem unbekannten Prozess');
    expect(externalOwner(status({ port: { ...foreign, managed: true } }))).toBeNull();
    expect(externalOwner(status({ state: 'starting', port: foreign }))).toBeNull();   // ours, check lags
    expect(externalOwner(status({ port: { ...foreign, open: false } }))).toBeNull();
  });

  it('measures the uptime on the server clock', () => {
    const s = status({ state: 'running', startedAt: '2026-10-07T12:00:00Z' });
    expect(uptimeSeconds(s, Date.parse('2026-10-07T12:01:30Z'))).toBe(90);
    expect(uptimeSeconds(status({ startedAt: '2026-10-07T12:00:00Z' }), Date.now())).toBeNull();  // stopped
    expect(uptimeSeconds(status({ state: 'running', startedAt: 'kaputt' }), 0)).toBeNull();
  });

  it('writes parameters compactly', () => {
    expect(paramText({ name: 'query', type: 'string', required: true })).toBe('query*: string');
    expect(paramText({ name: 'limit', type: 'integer', required: false, default: '8' })).toBe('limit: integer = 8');
  });

  it('keeps the newest revision', () => {
    expect(isNewer(status({ revision: 2 }), status({ revision: 3 }))).toBe(false);
    expect(isNewer(status({ revision: 3 }), status({ revision: 3 }))).toBe(true);  // same = HTTP echo of the SSE state
    expect(isNewer(status(), undefined)).toBe(true);
  });
});
