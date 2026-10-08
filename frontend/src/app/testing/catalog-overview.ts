import { CatalogModel } from '../api/models/catalog-model';
import { CatalogOverview } from '../api/models/catalog-overview';

const GIB = 1024 ** 3;

/** An installed model that fits; override what a test is about. */
export const catalogModel = (over: Partial<CatalogModel> = {}): CatalogModel => ({
  name: 'qwen3.5:9b', digest: 'a1b2c3d4e5f6a7b8', sizeBytes: 6 * GIB, family: 'qwen3', parameterSize: '9.0B',
  quantization: 'Q4_K_M', format: 'gguf', architecture: 'qwen3', capabilities: ['completion', 'tools'],
  parameters: { temperature: ['0.7'] }, systemChars: 0, weightsDigest: 'ffee', origin: 'qwen3.5:9b', depth: 0,
  parent: { declared: null, resolved: null, via: null, installed: false }, changes: [],
  context: { effective: 65536, source: 'server', own: null, server: 65536, trained: 262144, clamped: false, parallel: 1 },
  estimate: {
    weightsBytes: 6 * GIB, kvBytes: 5 * GIB, graphBytes: 0.4 * GIB, driverBytes: 1.2 * GIB, totalBytes: 12.6 * GIB,
    kvType: 'q8_0', tokens: 65536, notes: ['36 Schichten × 8 KV-Köpfe × 128+128 × q8_0'], confidence: 'normal',
  },
  observations: [],
  verdict: { state: 'fits', basis: 'estimated', needBytes: 11.4 * GIB, expectedBytes: 12.6 * GIB,
    vramTotalBytes: 16 * GIB, message: 'Sollte komplett auf die GPU passen: ≈ 12,6 GiB von 16,0 GiB.' },
  loaded: false, firstSeen: '2026-10-08T09:00:00Z', modifiedAt: '2026-09-12T07:14:03Z', showError: null,
  ...over,
});

/** Overview with one group: a base model and a derived 64k variant that is loaded and was measured. */
export const catalogOverview = (over: Partial<CatalogOverview> = {}): CatalogOverview => ({
  asOf: '2026-10-08T09:00:00Z', revision: 1, refreshedAt: '2026-10-08T09:00:00Z',
  ollama: { online: true, version: '0.35.0', error: null, simulated: false },
  server: { source: 'log', logFile: 'server.log', note: null, contextLength: 65536, kvCacheType: 'q8_0',
    flashAttention: true, numParallel: 1, maxLoadedModels: 1, keepAlive: '5m0s', overridden: [] },
  hardware: { key: 'RTX 5070 Ti · 16303 MiB', gpuName: 'RTX 5070 Ti', vramTotalBytes: 16 * GIB, note: null },
  assumptions: { graphReserveBytes: 0.4 * GIB, driverOverheadBytes: 1.2 * GIB, tightRatio: 0.9,
    fallbackContextLength: 4096, clampToTrained: true },
  models: [
    catalogModel(),
    catalogModel({
      name: 'qwen3.5-9b-64k:latest', depth: 1, loaded: true, changes: ['num_ctx 65536'],
      parent: { declared: 'qwen3.5:9b', resolved: 'qwen3.5:9b', via: 'declared', installed: true },
      context: { effective: 65536, source: 'model', own: 65536, server: 65536, trained: 262144, clamped: false, parallel: 1 },
      observations: [{ numCtx: 65536, hardware: 'RTX 5070 Ti · 16303 MiB', sizeBytes: 11.3 * GIB, vramBytes: 11.3 * GIB,
        placement: 'gpu', gpuRatio: 1, firstSeen: '2026-10-08T08:00:00Z', lastSeen: '2026-10-08T09:00:00Z', loads: 3,
        current: true }],
      verdict: { state: 'fits', basis: 'measured', needBytes: 11.3 * GIB, expectedBytes: 12.5 * GIB,
        vramTotalBytes: 16 * GIB, message: 'Läuft komplett auf der GPU (beobachtet).' },
    }),
  ],
  groups: [{ origin: 'qwen3.5:9b', installed: true, members: ['qwen3.5:9b', 'qwen3.5-9b-64k:latest'] }],
  removed: [],
  ...over,
});
