import { BenchStatus } from '../api/models/bench-status';
import { Candidate } from '../api/models/candidate';
import { CatalogModel } from '../api/models/catalog-model';
import { CatalogOverview } from '../api/models/catalog-overview';
import { OpencodeFit } from '../api/models/opencode-fit';
import { Preflight } from '../api/models/preflight';

const GIB = 1024 ** 3;

/** An installed model that fits; override what a test is about. */
export const catalogModel = (over: Partial<CatalogModel> = {}): CatalogModel => ({
  name: 'qwen3.5:9b', digest: 'a1b2c3d4e5f6a7b8', sizeBytes: 6 * GIB, family: 'qwen3', parameterSize: '9.0B',
  quantization: 'Q4_K_M', format: 'gguf', architecture: 'qwen3', capabilities: ['completion', 'tools'],
  parameters: { temperature: ['0.7'] }, systemChars: 0, weightsDigest: 'ffee', origin: 'qwen3.5:9b', depth: 0,
  parent: { declared: null, resolved: null, via: null, installed: false }, changes: [],
  context: { effective: 65536, source: 'server', own: null, server: 65536, trained: 262144, clamped: false, parallel: 1 },
  estimate: {
    weightsBytes: 6 * GIB, kvBytes: 5 * GIB, graphBytes: 0.4 * GIB, formulaBytes: 11.4 * GIB, needBytes: 11.4 * GIB,
    calibrated: null, kvType: 'q8_0', tokens: 65536, notes: ['36 Schichten × 8 KV-Köpfe × 128+128 × q8_0'],
    confidence: 'normal',
  },
  observations: [],
  verdict: { state: 'fits', basis: 'estimated', needBytes: 11.4 * GIB, availableBytes: 14 * GIB,
    message: 'Sollte komplett auf die GPU passen: ≈ 11,4 GiB von 14,0 GiB.' },
  benches: [], testable: true, usage: { tags: [], note: null, derived: [] }, opencode: null,
  loaded: false, firstSeen: '2026-10-08T09:00:00Z', modifiedAt: '2026-09-12T07:14:03Z', showError: null,
  ...over,
});

/** A finished test run of qwen3.5:9b at 8k context. */
export const benchStatus = (over: Partial<BenchStatus> = {}): BenchStatus => ({
  id: 'b1', revision: 6, asOf: '2026-10-08T09:05:00Z', name: 'qwen3.5:9b', digest: 'a1b2c3d4e5f6a7b8', numCtx: 8192,
  state: 'done', phase: null, createdAt: '2026-10-08T09:04:00Z', startedAt: '2026-10-08T09:04:00Z',
  finishedAt: '2026-10-08T09:05:00Z', unloaded: [], error: null,
  result: {
    requestedCtx: 8192, actualCtx: 8192, hardware: 'RTX 5070 Ti · 16303 MiB', kvType: 'q8_0', ollamaVersion: '0.35.0',
    loadS: 4.5, promptTokens: 58, promptTps: 600, evalTokens: 128, evalTps: 64, totalS: 6.7,
    sizeBytes: 7.1 * GIB, vramBytes: 7.1 * GIB, placement: 'gpu', gpuBeforeBytes: 1.4 * GIB,
    gpuAfterBytes: 8.8 * GIB, runnerOverheadBytes: 0.3 * GIB, note: null,
  },
  ...over,
});

/** OpenCode suitability of a model whose three tool calls passed. */
export const opencodeFit = (over: Partial<OpencodeFit> = {}): OpencodeFit => ({
  state: 'fits', reasons: ['Geeignet für OpenCode', 'Tool-Calls: 3/3 strukturiert.', 'Kontext 65.536 Token – reicht für OpenCode.'],
  toolsPassed: 3, toolsTotal: 3, toolsAt: '2026-10-09T08:00:00Z', toolsSimulated: false, context: 65536, minContext: 65536,
  atMin: null, evalTps: 125.1, configKey: 'qwen3.5:9b',
  block: '"qwen3.5:9b": {\n  "name": "qwen3.5:9b",\n  "limit": { "context": 65536, "output": 8192 }\n}',
  ...over,
});

/** Preflight answer for a model that fits. */
export const preflight = (over: Partial<Preflight> = {}): Preflight => ({
  name: 'qwen3.5:9b',
  context: { effective: 65536, source: 'server', own: null, server: 65536, trained: 262144, clamped: false, parallel: 1 },
  estimate: catalogModel().estimate,
  verdict: catalogModel().verdict,
  allowed: true, needsConfirm: false, reason: null, willUnload: [],
  suggestions: [2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144],
  ...over,
});

/** Overview with one group: a base model and a derived 64k variant that is loaded and was measured. */
export const catalogOverview = (over: Partial<CatalogOverview> = {}): CatalogOverview => ({
  asOf: '2026-10-08T09:00:00Z', revision: 1, refreshedAt: '2026-10-08T09:00:00Z',
  ollama: { online: true, version: '0.35.0', error: null, simulated: false },
  server: { source: 'log', logFile: 'server.log', note: null, contextLength: 65536, kvCacheType: 'q8_0',
    flashAttention: true, numParallel: 1, maxLoadedModels: 1, keepAlive: '5m0s', overridden: [] },
  hardware: { key: 'RTX 5070 Ti · 16303 MiB', gpuName: 'RTX 5070 Ti', vramTotalBytes: 16 * GIB, note: null },
  budget: { totalBytes: 16 * GIB, otherBytes: 1.4 * GIB, otherSource: 'measured',
    otherMeasuredAt: '2026-10-08T08:30:00Z', reserveBytes: 0.45 * GIB, availableBytes: 14.15 * GIB },
  assumptions: { graphReserveBytes: 0.4 * GIB, tightRatio: 0.9, fallbackContextLength: 4096, clampToTrained: true },
  tests: { allowed: true, reason: null },
  bench: null,
  models: [
    catalogModel(),
    catalogModel({
      name: 'qwen3.5-9b-64k:latest', depth: 1, loaded: true, changes: ['num_ctx 65536'],
      parent: { declared: 'qwen3.5:9b', resolved: 'qwen3.5:9b', via: 'declared', installed: true },
      context: { effective: 65536, source: 'model', own: 65536, server: 65536, trained: 262144, clamped: false, parallel: 1 },
      observations: [{ numCtx: 65536, hardware: 'RTX 5070 Ti · 16303 MiB', sizeBytes: 11.3 * GIB, vramBytes: 11.3 * GIB,
        placement: 'gpu', gpuRatio: 1, firstSeen: '2026-10-08T08:00:00Z', lastSeen: '2026-10-08T09:00:00Z', loads: 3,
        current: true }],
      verdict: { state: 'fits', basis: 'measured', needBytes: 11.3 * GIB, availableBytes: 14.15 * GIB,
        message: 'Läuft komplett auf der GPU (beobachtet).' },
    }),
  ],
  groups: [{ origin: 'qwen3.5:9b', installed: true, members: ['qwen3.5:9b', 'qwen3.5-9b-64k:latest'] }],
  removed: [],
  candidates: [],
  library: { simulated: false, hosts: ['registry.ollama.ai', 'hf.co'] },
  ...over,
});

/** A model of the library, checked before the pull: fits up to 32k, "knapp" at 48k, splits from 64k on. */
export const candidate = (over: Partial<Candidate> = {}): Candidate => ({
  name: 'qwen3:14b', host: 'registry.ollama.ai', page: 'https://ollama.com/library/qwen3:14b',
  checkedAt: '2026-10-09T08:30:00Z', simulated: false, pull: 'ollama pull qwen3:14b',
  downloadBytes: 8.64 * GIB, weightsBytes: 8.64 * GIB, projectorBytes: 0, family: 'qwen3', parameterSize: '14.8B',
  quantization: 'Q4_K_M', architecture: 'qwen3', capabilities: ['completion', 'tools', 'thinking'],
  capabilityNotes: ['Werkzeuge: das Template nutzt .Tools.'], parameters: { temperature: ['0.6'] },
  requires: null, requiresOk: null,
  context: { effective: 65536, source: 'server', own: null, server: 65536, trained: 131072, clamped: false, parallel: 1 },
  estimate: { ...catalogModel().estimate!, weightsBytes: 8.64 * GIB, needBytes: 15.2 * GIB, formulaBytes: 15.2 * GIB },
  verdict: { state: 'split', basis: 'estimated', needBytes: 15.2 * GIB, availableBytes: 14.15 * GIB, extraBytes: 0,
    message: '≈ 15,2 GiB von 14,1 GiB verfügbar – Teil-Offload zu erwarten, Absturzgefahr. num_ctx verkleinern.' },
  steps: [
    { tokens: 8192, needBytes: 9.7 * GIB, extraBytes: 0, state: 'fits' },
    { tokens: 32768, needBytes: 12 * GIB, extraBytes: 0, state: 'fits' },
    { tokens: 49152, needBytes: 13.6 * GIB, extraBytes: 0, state: 'tight' },
    { tokens: 65536, needBytes: 15.2 * GIB, extraBytes: 0, state: 'split' },
    { tokens: 131072, needBytes: 21.6 * GIB, extraBytes: 0, state: 'split' },
  ],
  fitsUpTo: 32768, loadsUpTo: 49152,
  opencode: opencodeFit({ state: 'no', reasons: ['Nicht geeignet für OpenCode',
    'Tool-Calls erst nach dem Download messbar – ein Testlauf prüft sie dann mit.',
    'Kontext 65.536 Token – reicht für OpenCode.', 'Läuft beim wirksamen Kontext nicht komplett auf der GPU.'],
    toolsPassed: null, toolsTotal: null, toolsAt: null, evalTps: null }),
  installed: false, sameWeights: [], headerBytes: 79_872, headerComplete: true, notes: [], error: null,
  ...over,
});
