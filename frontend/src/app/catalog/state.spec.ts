import { HttpErrorResponse } from '@angular/common/http';

import { benchStatus, catalogModel, catalogOverview } from '../testing/catalog-overview';
import {
  PHASE_ORDER, benchStateView, benchSummary, budgetFacts, capabilityChips, contextExplain, contextSource, gib,
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
    expect(PHASE_ORDER).toEqual(['unload', 'baseline', 'load', 'measure', 'cleanup']);
    expect(phaseLabel(null)).toBe('');
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
});
