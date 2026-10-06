import { LoadedModel } from '../api/models/loaded-model';
import {
  GIB, MIB, bytes, duration, expiry, modelMeta, ms, num, placementLabel, placementShares, share,
} from './format';

const model = (over: Partial<LoadedModel> = {}): LoadedModel => ({
  name: 'm', digest: 'd', family: 'qwen2', parameterSize: '14.8B', quantization: 'Q4_K_M',
  sizeBytes: 16 * GIB, vramBytes: 16 * GIB, gpuRatio: 1, placement: 'gpu', contextLength: 8192,
  expiresAt: '2026-10-06T12:04:00Z', pinned: false, unloading: false, ...over,
});

describe('dashboard format helpers', () => {
  it('formats numbers the German way with fixed digits', () => {
    expect(num(1355)).toBe('1.355');
    expect(num(12.26, 1)).toBe('12,3');
    expect(num(5, 1)).toBe('5,0');                       // fixed digits: values don't jump while updating
    expect(num(null)).toBe('—');
    expect(num(Number.NaN)).toBe('—');
  });

  it('picks MiB below 1 GiB and GiB above', () => {
    expect(bytes(577 * MIB)).toBe('577 MiB');
    expect(bytes(11.3 * GIB)).toBe('11,3 GiB');
    expect(bytes(null)).toBe('—');
  });

  it('computes shares only against a real total', () => {
    expect(share(1, 4)).toBe(25);
    expect(share(1, 0)).toBeNull();
    expect(share(null, 4)).toBeNull();
  });

  it('shows durations coarse and never negative', () => {
    expect(duration(35)).toBe('35 s');
    expect(duration(250)).toBe('4 min');
    expect(duration(6 * 3600 + 5 * 60)).toBe('6 h 05 min');
    expect(duration(3 * 86400 + 4 * 3600 + 59)).toBe('3 T 4 h');
    expect(duration(-3)).toBe('0 s');
  });

  it('says "< 1 ms" instead of 0 ms', () => {
    expect(ms(0.3)).toBe('< 1 ms');
    expect(ms(26.4)).toBe('26 ms');
    expect(ms(null)).toBe('—');
  });

  it('measures expiry against the snapshot time, not the browser clock', () => {
    expect(expiry(model(), '2026-10-06T12:00:00Z')).toBe('in 4 min');
    expect(expiry(model({ pinned: true, expiresAt: null }), '2026-10-06T12:00:00Z')).toBe('angepinnt');
    expect(expiry(model({ unloading: true }), '2026-10-06T12:00:00Z')).toBe('wird entladen');
    expect(expiry(model({ expiresAt: null }), '2026-10-06T12:00:00Z')).toBe('—');
  });

  it('splits the placement bar like the backend counts it', () => {
    expect(placementShares(model())).toEqual({ gpu: 100, cpu: 0 });
    expect(placementShares(model({ placement: 'split', gpuRatio: 0.8438 }))).toEqual({ gpu: 84, cpu: 16 });
    // 99.9 % is still a split: the CPU part must stay visible
    expect(placementShares(model({ placement: 'split', gpuRatio: 0.999 }))).toEqual({ gpu: 99, cpu: 1 });
    expect(placementShares(model({ placement: 'cpu', gpuRatio: 0 }))).toEqual({ gpu: 0, cpu: 100 });
    expect(placementShares(model({ placement: 'unknown', gpuRatio: null }))).toEqual({ gpu: 0, cpu: 0 });
    expect(placementLabel(model({ placement: 'split', gpuRatio: 0.8438 }))).toBe('84 % GPU · 16 % CPU');
    expect(placementLabel(model({ placement: 'unknown' }))).toBe('lädt …');
  });

  it('builds the model meta line from what Ollama reported', () => {
    expect(modelMeta(model())).toBe('qwen2 · 14.8B · Q4_K_M');
    expect(modelMeta(model({ family: null, quantization: null }))).toBe('14.8B');
  });
});
