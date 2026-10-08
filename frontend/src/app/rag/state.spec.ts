import { ragJob, ragReport } from '../testing/rag-overview';
import {
  collectionStatus,
  includeText,
  isNewerJob,
  isRunning,
  jobStateView,
  mcpText,
  progressPercent,
  qdrantDashboardUrl,
  reportFacts,
  when,
} from './state';

describe('rag state helpers', () => {
  it('words the job states, cancel included', () => {
    expect(jobStateView(ragJob()).label).toBe('Läuft …');
    expect(jobStateView(ragJob({ cancelRequested: true })).label).toBe('Bricht ab …');
    expect(jobStateView(ragJob({ state: 'done' })).tone).toBe('normal');
    expect(jobStateView(ragJob({ state: 'failed' }))).toEqual(expect.objectContaining({ label: 'Mit Fehlern', tone: 'critical' }));
    expect(isRunning(ragJob({ state: 'queued' }))).toBe(true);
    expect(isRunning(ragJob({ state: 'cancelled' }))).toBe(false);
    expect(isRunning(null)).toBe(false);
  });

  it('computes progress, unknown while scanning, full while cleaning up', () => {
    expect(progressPercent({ collection: 'x', phase: 'embed', done: 31, total: 124 })).toBe(25);
    expect(progressPercent({ collection: 'x', phase: 'scan', done: 0, total: 0 })).toBeNull();
    expect(progressPercent({ collection: 'x', phase: 'cleanup', done: 0, total: 0 })).toBe(100);
    expect(progressPercent(null)).toBeNull();
  });

  it('keeps the newest job: revision within a job, creation time across jobs', () => {
    const old = ragJob({ revision: 5 });
    expect(isNewerJob(ragJob({ revision: 4 }), old)).toBe(false);
    expect(isNewerJob(ragJob({ revision: 6 }), old)).toBe(true);
    const next = ragJob({ id: 'job2', revision: 1, createdAt: '2026-10-08T10:00:00Z' });
    expect(isNewerJob(next, old)).toBe(true);
    expect(isNewerJob(old, next)).toBe(false);                     // late echo of the previous job
    expect(isNewerJob(old, null)).toBe(true);
  });

  it('lists the report counters and flags the ones that need a look', () => {
    const facts = reportFacts(ragReport({ skipped: 2 }));
    expect(facts.map((f) => f.label)).toEqual(
      ['Dateien', 'Indexiert', 'Abgeschnitten', 'Leer', 'Secret-Filter', 'Übersprungen', 'Entfernt']);
    expect(facts.find((f) => f.label === 'Abgeschnitten')).toEqual(expect.objectContaining({ value: '31', tone: 'warning' }));
    expect(facts.find((f) => f.label === 'Übersprungen')?.tone).toBe('critical');
    expect(reportFacts(ragReport({ truncated: 0 })).find((f) => f.label === 'Abgeschnitten')?.tone).toBe('normal');
  });

  it('formats includes, collection states and times', () => {
    expect(includeText({ dir: 'src', ext: ['.php', '.sql'] })).toBe('src → .php, .sql');
    expect(collectionStatus({ name: 'x', status: 'red' }).label).toBe('Fehler');
    expect(collectionStatus({ name: 'x' }).label).toBe('unbekannt');
    const now = new Date(2026, 9, 8, 15, 0);
    expect(when(new Date(2026, 9, 8, 9, 5).toISOString(), now)).toBe('09:05');
    expect(when(new Date(2026, 9, 7, 9, 5).toISOString(), now)).toBe('07.10. 09:05');
    expect(when(null)).toBe('—');
  });

  it('builds the text exactly like mcp_server.py', () => {
    const hits = [{ id: '1', score: 0.8, filename: 'src/A.php', text: '<?php A' },
                  { id: '2', score: 0.5, filename: 'src/B.php', text: '<?php B' }];
    expect(mcpText(hits)).toBe('Datei: src/A.php\n<?php A\n\n---\n\nDatei: src/B.php\n<?php B');
    expect(mcpText([])).toBe('Keine Treffer gefunden.');
  });

  it('points the Qdrant link at the host the browser used for a local Qdrant', () => {
    expect(qdrantDashboardUrl('http://127.0.0.1:6333', 'ai-box')).toBe('http://ai-box:6333/dashboard');
    expect(qdrantDashboardUrl('http://localhost:6333', 'ai-box')).toBe('http://ai-box:6333/dashboard');
    expect(qdrantDashboardUrl('http://10.0.0.5:6333', 'localhost')).toBe('http://10.0.0.5:6333/dashboard');
    expect(qdrantDashboardUrl(null, 'x')).toBeNull();
    expect(qdrantDashboardUrl('kein url', 'x')).toBeNull();
  });
});
