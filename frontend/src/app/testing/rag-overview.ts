import { JobStatus } from '../api/models/job-status';
import { RagOverview } from '../api/models/rag-overview';
import { SourceInfo } from '../api/models/source-info';
import { SourceReport } from '../api/models/source-report';

/** A finished report of one source; override what a test is about. */
export const ragReport = (over: Partial<SourceReport> = {}): SourceReport => ({
  collection: 'bent_php', title: 'Bent PHP', state: 'done', files: 124, done: 124, indexed: 118, truncated: 31,
  empty: 2, secret: 1, skipped: 0, removed: 3, created: false, missingDirs: [],
  truncatedFiles: [{ file: 'src/Big.php', detail: '19651 Bytes' }],
  secretHits: [{ file: 'src/Config.php', line: 3, rule: 'assignment', allowed: false }],
  skippedFiles: [], listsCut: false, startedAt: '2026-10-08T09:00:00Z', finishedAt: '2026-10-08T09:01:10Z',
  durationS: 70, embedS: 61, ...over,
});

/** A configured source with an existing collection and no run yet. */
export const ragSource = (over: Partial<SourceInfo> = {}): SourceInfo => ({
  collection: 'bent_php', title: 'Bent PHP', path: 'C:/rag/repos/bent/bent-php-api', pathExists: true,
  includes: [{ dir: 'src', ext: ['.php'] }, { dir: 'db/migrations', ext: ['.sql'] }],
  excludeNames: ['.env', '.htpasswd'], excludeDirs: ['vendor', 'node_modules', '.git', 'var'], secretAllow: [],
  stats: { name: 'bent_php', status: 'green', points: 124, vectorSize: 768, distance: 'Cosine', source: 'Bent PHP' },
  lastRun: null, ...over,
});

/** A job as the backend publishes it on rag.job. */
export const ragJob = (over: Partial<JobStatus> = {}): JobStatus => ({
  id: 'job1', revision: 1, asOf: '2026-10-08T09:00:00Z', state: 'running', createdAt: '2026-10-08T09:00:00Z',
  startedAt: '2026-10-08T09:00:00Z', cancelRequested: false, model: 'nomic-embed-text', maxChars: 6000,
  current: { collection: 'bent_php', phase: 'embed', file: 'src/Note.php', done: 40, total: 124 },
  sources: [ragReport({ state: 'running', indexed: 0, finishedAt: null, durationS: null })], ...over,
});

/** Overview of a local Qdrant with one source and one foreign collection. */
export const ragOverview = (over: Partial<RagOverview> = {}): RagOverview => ({
  asOf: '2026-10-08T09:00:00Z',
  store: { mode: 'qdrant', url: 'http://127.0.0.1:6333', reachable: true, version: '1.19.2', local: true, port: 6333 },
  embedder: { mode: 'ollama', url: 'http://127.0.0.1:11434', model: 'nomic-embed-text' },
  writes: { allowed: true }, maxChars: 6000,
  sources: [ragSource()],
  collections: [
    { name: 'bent', status: 'green', points: 300, vectorSize: 768, distance: 'Cosine', source: null },
    { name: 'bent_php', status: 'green', points: 124, vectorSize: 768, distance: 'Cosine', source: 'Bent PHP' },
  ],
  job: null, ...over,
});
