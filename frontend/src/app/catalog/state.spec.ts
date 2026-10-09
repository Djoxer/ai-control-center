import { HttpErrorResponse } from '@angular/common/http';

import { benchStatus, candidate, catalogModel, catalogOverview, opencodeFit } from '../testing/catalog-overview';
import {
  candidateFacts, candidateFit, downloadText, modelInfoRows, readText, stepViews, tokensShort,
  PHASE_ORDER, USAGE, benchStateView, benchSummary, budgetFacts, capabilityChips, contextExplain, contextSource,
  extraText, fitView, gib, inUse, matchesUsage, toolsSummary, usageChips, usageLabel,
  groupViews, isBenchRunning, isNewerBench, isNewerOverview, message, observationText, originWord, parameterList,
  phaseLabel, relationText, serverFacts, serverSourceText, shortDigest, verdictOrigin, verdictView, vramHint,
  vramPercent, vramText,
} from './state';

const GIB = 1024 ** 3;

describe('catalog state helpers', () => {
  it('names every verdict in words and tones', () => {
    const v = catalogModel().verdict;
    expect(verdictView({ ...v, state: 'split' })).toEqual({ label: 'Teil-Offload', tone: 'critical' });
    expect(verdictView({ ...v, state: 'tight' }).tone).toBe('warning');
    expect(verdictView({ ...v, state: 'fits' }).label).toBe('passt');
    expect(verdictView({ ...v, state: 'nonsense' as never }).label).toBe('unklar');
  });

  it('compares the need on Ollama scale with what the card leaves Ollama', () => {
    const v = { ...catalogModel().verdict, needBytes: 10 * GIB, availableBytes: 12.5 * GIB };
    expect(vramText(v)).toBe('≈ 10,0 GiB');
    expect(vramText({ ...v, basis: 'measured' })).toBe('10,0 GiB');
    expect(vramPercent(v)).toBeCloseTo(80);
    expect(vramHint(v)).toBe('10,0 GiB Bedarf von 12,5 GiB, die die Karte Ollama lässt');
    expect(vramHint({ ...v, availableBytes: null })).toBe(v.message);
    expect(vramText({ ...v, needBytes: null })).toBe('—');
    expect(vramPercent({ ...v, availableBytes: null })).toBeNull();
    // the runner holds 0,8 GiB beyond Ollama's count (vision encoder): the bar and the hint count it
    const vis = { ...v, extraBytes: 0.8 * GIB };
    expect(vramPercent(vis)).toBeCloseTo(86.4);
    expect(vramText(vis)).toBe('≈ 10,0 GiB');                   // the figure stays Ollama's count ...
    expect(extraText(vis)).toBe('+0,8');                         // ... the rest is shown next to it
    expect(extraText(v)).toBe('');
    expect(vramHint(vis)).toBe('10,0 GiB Bedarf + 0,8 GiB außerhalb Ollamas Zählung von 12,5 GiB, die die Karte Ollama lässt');
    expect(verdictOrigin({ ...v, basis: 'none' })).toBeNull();
    expect(gib(null)).toBe('—');
  });

  it('says whether a figure was measured, calibrated or only estimated', () => {
    const m = catalogModel();
    expect(originWord(m)).toBe('geschätzt');
    expect(originWord({ ...m, estimate: { ...m.estimate!, calibrated: 'an 1 Messung (65.536 Token) kalibriert' } }))
      .toBe('kalibriert');
    expect(originWord({ ...m, verdict: { ...m.verdict, basis: 'measured' } })).toBe('gemessen');
    expect(originWord({ ...m, verdict: { ...m.verdict, basis: 'none' } })).toBe('');
  });

  it('lists the GPU budget with the source of the other programs', () => {
    const now = new Date('2026-10-08T12:00:00');
    const b = catalogOverview().budget;
    const facts = budgetFacts({ ...b, otherMeasuredAt: '2026-10-08T08:30:00' }, now).map((f) => `${f.label}=${f.value}`);
    expect(facts).toEqual(['Karte=16,0 GiB', 'andere Programme=1,4 GiB (gemessen 08:30)', 'Reserve Ollama=0,45 GiB',
      'verfügbar=14,2 GiB']);
    const assumed = budgetFacts({ ...b, totalBytes: null, availableBytes: null, otherSource: 'assumed' }, now);
    expect(assumed.map((f) => f.label)).toEqual(['andere Programme', 'Reserve Ollama']);
    expect(assumed[0].value).toBe('1,4 GiB (angenommen)');
  });

  it('explains where the context comes from', () => {
    const c = catalogModel().context;
    expect(contextSource(c)).toBe('Server-Standard');
    expect(contextSource({ ...c, clamped: true })).toBe('Server-Standard, gekürzt');
    const text = contextExplain({ ...c, source: 'server', server: 65536, clamped: true, trained: 32768, parallel: 2 });
    expect(text).toContain('Server-Standard 65.536');
    expect(text).toContain('auf 32.768 Token');
    expect(text).toContain('2 parallele');
    expect(contextExplain({ ...c, source: 'fallback', effective: 4096 })).toContain('angenommen: 4.096');
    expect(contextExplain({ ...c, source: 'model', own: 8192 })).toContain('num_ctx 8.192');
    expect(contextSource({ ...c, source: 'request' })).toBe('gewählt');
    expect(contextExplain({ ...c, source: 'request', effective: 8192 })).toContain('Gewählt: 8.192 Token');
  });

  it('turns capabilities into chips without the obvious one', () => {
    expect(capabilityChips(['completion', 'tools', 'insert', 'something'])).toEqual(['Tools', 'Autocomplete', 'something']);
    expect(capabilityChips(undefined)).toEqual([]);
  });

  it('describes relations that indentation alone cannot show', () => {
    const m = catalogModel();
    expect(relationText(m)).toBeNull();
    expect(relationText({ ...m, parent: { declared: null, resolved: 'base', via: 'weights', installed: false } }))
      .toBe('gleiche Gewichte wie base');
    expect(relationText({ ...m, parent: { declared: 'gone:1', resolved: null, via: null, installed: false } }))
      .toBe('erstellt aus gone:1 (nicht installiert)');
    expect(relationText({ ...m, parent: { declared: null, resolved: 'coder:14b', via: 'copy', installed: false } }))
      .toBe('Kopie von coder:14b');
  });

  it('describes test runs: phases, state, summary', () => {
    const done = benchStatus();
    expect(isBenchRunning(done)).toBe(false);
    expect(isBenchRunning(benchStatus({ state: 'queued' }))).toBe(true);
    expect(isBenchRunning(null)).toBe(false);
    expect(benchSummary(done)).toBe('64,0 tok/s · Laden 4,5 s · 8.192 Token');
    expect(benchSummary(benchStatus({ state: 'running', phase: 'load', result: null })))
      .toBe('Testlauf: Laden und antworten …');
    expect(benchSummary(benchStatus({ state: 'running', phase: null, result: null }))).toBe('Testlauf: startet …');
    expect(benchSummary(benchStatus({ state: 'failed', error: 'x' }))).toBe('Testlauf fehlgeschlagen');
    expect(benchSummary(benchStatus({ result: { ...done.result!, actualCtx: 4096, evalTps: null, loadS: null } })))
      .toBe('4.096 Token');
    expect(benchStateView(benchStatus({ state: 'cancelled' })).tone).toBe('warning');
    expect(benchStateView(benchStatus({ state: 'failed' })).tone).toBe('critical');
    expect(PHASE_ORDER).toEqual(['unload', 'baseline', 'load', 'measure', 'tools', 'cleanup']);
    expect(phaseLabel(null)).toBe('');
  });

  it('turns usage tags into chips, hand-set first, without doubles', () => {
    const u = { tags: ['opencode' as const, 'remove' as const], note: 'x',
      derived: [{ tag: 'rag' as const, source: 'RAG-Modul (embedding_model)' }, { tag: 'opencode' as const, source: 'y' }] };
    expect(usageChips(u)).toEqual([
      { key: 'opencode', label: 'OpenCode', title: 'vom Team gesetzt' },
      { key: 'remove', label: 'Löschkandidat', title: 'vom Team gesetzt' },
      { key: 'rag', label: 'RAG', title: 'laut RAG-Modul (embedding_model)' }]);
    expect(usageChips(null)).toEqual([]);
    expect(inUse({ tags: ['remove'] })).toBe(false);                 // a deletion candidate is not "in use"
    expect(inUse({ tags: [], derived: [{ tag: 'rag', source: 'x' }] })).toBe(true);
    const m = catalogModel({ usage: { tags: ['openwebui'] } });
    expect([matchesUsage(m, 'all'), matchesUsage(m, 'used'), matchesUsage(m, 'unused')]).toEqual([true, true, false]);
    expect(usageLabel('test')).toBe('Test');
    expect(USAGE.map((x) => x.key)).toEqual(['opencode', 'openwebui', 'rag', 'test', 'remove']);
  });

  it('summarizes tool calls and the OpenCode fit', () => {
    expect(toolsSummary(null)).toBe('');
    expect(toolsSummary({ passed: 2, total: 3 })).toBe('2/3 Tool-Calls');
    expect(toolsSummary({ passed: 3, total: 3, simulated: true })).toBe('3/3 Tool-Calls (Simulation)');
    expect(toolsSummary({ passed: 0, total: 3, skipped: 'Ollama lehnt ab' })).toBe('keine Tools');
    expect(benchSummary(benchStatus({ result: { ...benchStatus().result!, tools: { passed: 3, total: 3 } } })))
      .toBe('64,0 tok/s · Laden 4,5 s · 8.192 Token · 3/3 Tool-Calls');
    expect(fitView(null)).toBeNull();
    expect(fitView(opencodeFit({ state: 'unknown' }))).toBeNull();      // nothing to say yet
    expect(fitView(opencodeFit())!.label).toBe('OpenCode ✓');
    expect(fitView(opencodeFit({ state: 'maybe' }))!.classes).toContain('amber');
    expect(fitView(opencodeFit({ state: 'no' }))!.label).toBe('OpenCode ✗');
  });

  it('lets the newest test run status win', () => {
    const a = benchStatus({ revision: 5 });
    expect(isNewerBench(benchStatus({ revision: 6 }), a)).toBe(true);
    expect(isNewerBench(benchStatus({ revision: 4 }), a)).toBe(false);
    const later = benchStatus({ id: 'b2', revision: 1, createdAt: '2026-10-08T10:00:00Z' });
    expect(isNewerBench(later, a)).toBe(true);
    expect(isNewerBench(a, later)).toBe(false);
    expect(isNewerBench(a, null)).toBe(true);
  });

  it('builds groups in the order the backend sent', () => {
    const ov = catalogOverview();
    const views = groupViews(ov);
    expect(views.length).toBe(1);
    expect(views[0].models.map((m) => m.name)).toEqual(['qwen3.5:9b', 'qwen3.5-9b-64k:latest']);
    expect(groupViews({ ...ov, groups: [{ origin: 'x', installed: true, members: ['gibt-es-nicht'] }] })[0].models)
      .toEqual([]);
  });

  it('lets the newest overview win, also after a backend restart', () => {
    const old = catalogOverview({ asOf: '2026-10-08T09:00:00Z', revision: 40 });
    const restarted = catalogOverview({ asOf: '2026-10-08T09:05:00Z', revision: 1 });
    expect(isNewerOverview(restarted, old)).toBe(true);
    expect(isNewerOverview(old, restarted)).toBe(false);
    expect(isNewerOverview(old, null)).toBe(true);
  });

  it('summarizes server defaults and their source', () => {
    const s = catalogOverview().server;
    expect(serverFacts(s).map((f) => `${f.label}=${f.value}`)).toEqual([
      'Standard-Kontext=65.536', 'KV-Cache=q8_0', 'Flash Attention=an', 'parallel=1', 'max. geladen=1']);
    expect(serverFacts({ ...s, contextLength: null, flashAttention: null, kvCacheType: null, numParallel: null,
      maxLoadedModels: null })).toEqual([]);
    expect(serverSourceText(s)).toBe('aus server.log');
    expect(serverSourceText({ ...s, source: 'mixed' })).toBe('aus server.log, teils überschrieben');
    expect(serverSourceText({ ...s, source: 'config' })).toBe('aus control-center.toml');
    expect(serverSourceText({ ...s, source: 'unknown' })).toBe('unbekannt');
  });

  it('formats observations, digests and parameters', () => {
    const o = catalogOverview().models[1].observations![0];
    expect(observationText({ ...o, gpuRatio: 0.849 })).toBe('65.536 Token · 11,3 GiB · 84 % GPU');
    expect(observationText({ ...o, numCtx: null, gpuRatio: null })).toBe('Kontext unbekannt · 11,3 GiB');
    expect(shortDigest('sha256:0123456789abcdef')).toBe('0123456789ab');
    expect(shortDigest(null)).toBe('—');
    expect(parameterList({ stop: ['a', 'b'], temperature: ['0.2'], num_ctx: ['8192'] })).toEqual([
      { label: 'num_ctx', value: '8192' }, { label: 'temperature', value: '0.2' }, { label: 'stop', value: 'a · b' }]);
  });

  it('turns request errors into German text', () => {
    expect(message(new HttpErrorResponse({ status: 0 }))).toBe('Backend nicht erreichbar');
    expect(message(new HttpErrorResponse({ status: 503, error: { detail: 'Ollama nicht erreichbar: x' } })))
      .toBe('Ollama nicht erreichbar: x');
    expect(message(new HttpErrorResponse({ status: 503, error: { detail: 'Katalog-Modul läuft nicht' } })))
      .toContain('Katalog-Modul läuft nicht');
    expect(message(new HttpErrorResponse({ status: 500 }))).toBe('HTTP 500');
    expect(message(new Error('boom'))).toBe('boom');
  });

  it('names context steps short, with a mark and the need in the tooltip', () => {
    expect([2048, 40960, 262144, 1000].map(tokensShort)).toEqual(['2k', '40k', '256k', '1.000']);
    const views = stepViews([
      { tokens: 8192, needBytes: 9 * GIB, extraBytes: GIB, state: 'fits' },
      { tokens: 49152, needBytes: 13 * GIB, extraBytes: 0, state: 'tight' },
      { tokens: 65536, needBytes: null, extraBytes: 0, state: 'split' },
      { tokens: 131072, needBytes: 20 * GIB, extraBytes: 0, state: 'unknown' },
    ]);
    expect(views.map((v) => v.label)).toEqual(['8k ✓', '48k !', '64k ✗', '128k ?']);
    expect(views[0].title).toBe('8.192 Token: ≈ 10,0 GiB – passt');            // extra included
    expect(views[2].title).toBe('65.536 Token: Bedarf unbekannt – Teil-Offload');
    expect(views[1].classes).toContain('border-amber-400/40');
    expect(stepViews(null)).toEqual([]);
  });

  it('says up to which context a candidate fits', () => {
    expect(candidateFit(candidate(), 14 * GIB)).toBe(
      'Passt bis 32.768 Token, knapp bis 49.152 – darüber kippt es in den Teil-Offload.');
    expect(candidateFit(candidate({ loadsUpTo: 32768 }), 14 * GIB)).toBe(
      'Passt bis 32.768 Token – darüber kippt es in den Teil-Offload.');
    expect(candidateFit(candidate({ fitsUpTo: 131072, loadsUpTo: 131072 }), 14 * GIB)).toBe(
      'Passt bei jedem Kontext bis 131.072 Token.');
    expect(candidateFit(candidate({ fitsUpTo: null, loadsUpTo: 2048 }), 14 * GIB)).toBe(
      'Nur knapp, bis 2.048 Token – andere Last auf der Karte kippt es.');
    expect(candidateFit(candidate({ fitsUpTo: null, loadsUpTo: null, weightsBytes: 17.3 * GIB }), 14 * GIB)).toBe(
      'Passt bei keinem Kontext: schon die Gewichte (17,3 GiB) sind größer als das Budget (14,0 GiB).');
    expect(candidateFit(candidate({ fitsUpTo: null, loadsUpTo: null }), 14 * GIB)).toBe(
      'Passt bei keinem Kontext komplett auf die Karte.');
    expect(candidateFit(candidate({ steps: [{ tokens: 2048, state: 'unknown' }] }), null)).toContain('Keine Prognose');
    expect(candidateFit(candidate({ steps: [] }), 14 * GIB)).toContain('Keine Prognose');
    expect(candidateFit(candidate({ error: 'Cloud-Modell: …' }), 14 * GIB)).toBe('Cloud-Modell: …');
  });

  it('describes download, facts and how much was read', () => {
    expect(downloadText(candidate())).toBe('8,6 GiB');
    expect(downloadText(candidate({ downloadBytes: 6.1 * GIB, projectorBytes: 0.86 * GIB }))).toBe(
      '6,1 GiB · davon Bild-Encoder 0,9 GiB');
    expect(downloadText(candidate({ weightsBytes: 0 }))).toBe('—');
    expect(candidateFacts(candidate())).toBe('qwen3 · 14.8B · Q4_K_M');
    expect(candidateFacts(candidate({ architecture: null, parameterSize: null }))).toBe('qwen3 · Q4_K_M');
    expect([0, 79_872, 1024 ** 2, 8.5 * 1024 ** 2].map(readText)).toEqual(['0 KB', '78 KB', '1,0 MiB', '8,5 MiB']);
  });

  it('lists the GGUF metadata sorted, long lists shortened', () => {
    expect(modelInfoRows({ 'qwen35.block_count': 32, 'general.architecture': 'qwen35',
      'qwen35.attention.head_count_kv': [0, 0, 0, 4, 0, 0, 0, 4, 0, 0], 'x.flags': [true, false] })).toEqual([
      { label: 'general.architecture', value: 'qwen35' },
      { label: 'qwen35.attention.head_count_kv', value: '[0, 0, 0, 4, 0, 0, 0, 4, … 10 Werte]' },
      { label: 'qwen35.block_count', value: '32' },
      { label: 'x.flags', value: '[true, false]' },
    ]);
    expect(modelInfoRows(null)).toEqual([]);
  });
});
