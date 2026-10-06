// Pure formatting helpers for the dashboard: German number format, units, durations.
// No Angular in here -> trivially unit-testable (see format.spec.ts).

import { LoadedModel } from '../api/models/loaded-model';

export const MIB = 1024 ** 2;
export const GIB = 1024 ** 3;

const formatters = new Map<number, Intl.NumberFormat>();

/** 1355 -> "1.355", (12.26, 1) -> "12,3". Same digits always, so values don't jump while updating. */
export function num(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  let f = formatters.get(digits);
  if (!f) {
    f = new Intl.NumberFormat('de-DE', { minimumFractionDigits: digits, maximumFractionDigits: digits });
    formatters.set(digits, f);
  }
  return f.format(value);
}

/** Bytes as GiB, or MiB below 1 GiB: 577 MiB, 11,3 GiB. */
export function bytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return value < GIB ? `${num(value / MIB)} MiB` : `${num(value / GIB, 1)} GiB`;
}

/** Latency: "< 1 ms" instead of a misleading "0 ms" (local fakes and loopback are that fast). */
export function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return value < 1 ? '< 1 ms' : `${num(value)} ms`;
}

/** Share in percent (0..100), null when the total is unknown or zero. */
export function share(part: number | null | undefined, total: number | null | undefined): number | null {
  if (part === null || part === undefined || !total) return null;
  return (part / total) * 100;
}

/** 35 s, 4 min, 6 h 52 min, 3 T 4 h - coarse on purpose: the value updates every 2 s anyway. */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—';
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')} min`;
  return `${Math.floor(s / 86400)} T ${Math.floor((s % 86400) / 3600)} h`;
}

/** "14:12:10" in the viewer's local time. */
export function clockTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/**
 * When does Ollama unload the model? Measured against the snapshot's own timestamp (server clock),
 * not the browser clock: a dev PC that is 30 s off would otherwise show wrong countdowns.
 */
export function expiry(m: LoadedModel, snapshotTs: string): string {
  if (m.pinned) return 'angepinnt';
  if (m.unloading) return 'wird entladen';
  if (!m.expiresAt) return '—';
  const left = (Date.parse(m.expiresAt) - Date.parse(snapshotTs)) / 1000;
  return Number.isNaN(left) ? '—' : `in ${duration(left)}`;
}

/** Bar segments in percent. Floor for the GPU part, like the backend warning: 99.9 % is still a split. */
export function placementShares(m: LoadedModel): { gpu: number; cpu: number } {
  switch (m.placement) {
    case 'gpu': return { gpu: 100, cpu: 0 };
    case 'cpu': return { gpu: 0, cpu: 100 };
    case 'split': {
      const gpu = Math.min(99, Math.floor((m.gpuRatio ?? 0) * 100));
      return { gpu, cpu: 100 - gpu };
    }
    default: return { gpu: 0, cpu: 0 };     // 'unknown': still loading, nothing to draw
  }
}

export function placementLabel(m: LoadedModel): string {
  const { gpu, cpu } = placementShares(m);
  switch (m.placement) {
    case 'gpu': return '100 % GPU';
    case 'cpu': return 'nur CPU';
    case 'split': return `${gpu} % GPU · ${cpu} % CPU`;
    default: return 'lädt …';
  }
}

/** "qwen3 · 9.0B · Q4_K_M" - only the parts Ollama reported. */
export function modelMeta(m: LoadedModel): string {
  return [m.family, m.parameterSize, m.quantization].filter(Boolean).join(' · ');
}

/** NVML clock event reasons -> German; unknown codes are shown as they come. */
export const THROTTLE_LABELS: Record<string, string> = {
  idle: 'Leerlauf',
  app_clocks: 'App-Takt',
  power_cap: 'Leistungslimit',
  hw_slowdown: 'Hardware-Bremse',
  sync_boost: 'Sync-Boost',
  sw_thermal: 'Temperatur (Treiber)',
  hw_thermal: 'Temperatur (Hardware)',
  power_brake: 'Netzteil-Bremse',
  display_clocks: 'Display-Takt',
};
/** Same set the backend warns about; power_cap under full load and idle clocks are normal. */
export const THROTTLE_SERIOUS = new Set(['hw_slowdown', 'sw_thermal', 'hw_thermal', 'power_brake']);

export const SOURCE_LABELS: Record<string, string> = {
  ollama: 'Ollama', ollama_version: 'Ollama-Version', gpu: 'GPU', host: 'Host', disks: 'Laufwerke',
  history: 'Verlauf (Datenbank)', ollama_log: 'Ollama-Log',
};
