"""Context length, VRAM estimate and the verdict "fits on the GPU?" - pure functions, no I/O.

The estimate is a packing list, not a scale: weights (file size) + KV cache (formula from the GGUF
metadata) + a reserve for compute buffers + what the driver takes on top of Ollama's own count.
A real observation from /api/ps (the scale) always beats it; the estimate is what we can say
BEFORE a model is loaded - the job of the offload protection later on.

KV cache per token and layer = KV heads x (key length + value length) x bytes per element.
Architectures that keep KV only in some layers (sliding window, hybrid/recurrent, MLA) are
handled where the metadata says so; everything unusual lowers the confidence instead of guessing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from control_center.modules.catalog.collector import ModelRecord
from control_center.modules.catalog.settings import CatalogSettings

GIB = 1024 ** 3

ContextSource = Literal["model", "server", "fallback"]
Confidence = Literal["normal", "low"]
Placement = Literal["gpu", "split", "cpu", "unknown"]
VerdictState = Literal["fits", "tight", "split", "cpu", "unknown"]
Basis = Literal["measured", "estimated", "none"]

# bytes per stored element; q8_0/q4_0 blocks carry a 2-byte scale per 32 values
KV_BYTES = {"f16": 2.0, "q8_0": 34 / 32, "q4_0": 18 / 32}
# sliding-window layouts: one layer with full attention in every n layers, the others see only the window
SWA_EVERY = {"gptoss": 2, "gemma2": 2, "gemma3": 6, "cohere2": 4}


def _num(value: float, digits: int = 0) -> str:
    text = f"{value:,.{digits}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def gib(value: float) -> str:
    return f"{_num(value / GIB, 1)} GiB"


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


# ---- context --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ContextResult:
    effective: int
    source: ContextSource
    own: int | None                 # num_ctx PARAMETER of the model
    server: int | None              # OLLAMA_CONTEXT_LENGTH
    trained: int | None             # <arch>.context_length from the GGUF
    clamped: bool                   # effective was cut down to `trained`
    parallel: int                   # OLLAMA_NUM_PARALLEL: every slot gets its own KV cache


def effective_context(rec: ModelRecord, server_ctx: int | None, parallel: int | None,
                      cfg: CatalogSettings) -> ContextResult:
    own = _int(rec.param("num_ctx"))
    own = own if own and own > 0 else None
    trained = _int(rec.info("context_length"))
    if own:
        value, source = own, "model"
    elif server_ctx:
        value, source = server_ctx, "server"
    else:
        value, source = cfg.fallback_context_length, "fallback"
    clamped = bool(cfg.clamp_to_trained and trained and value > trained)
    if clamped:
        value = trained                                    # type: ignore[assignment]
    return ContextResult(effective=value, source=source, own=own, server=server_ctx, trained=trained,
                         clamped=clamped, parallel=max(1, parallel or 1))


# ---- estimate -------------------------------------------------------------------------------------

def effective_kv_type(kv_type: str | None, flash_attention: bool | None) -> tuple[str, str | None]:
    """KV quantization only works with flash attention; Ollama silently falls back to f16 otherwise."""
    kv = (kv_type or "f16").lower()
    if kv not in KV_BYTES:
        return "f16", f"KV-Typ „{kv}“ unbekannt – mit f16 gerechnet"
    if kv != "f16" and flash_attention is False:
        return "f16", f"KV-Cache {kv} wirkt nur mit Flash Attention – Ollama nimmt f16"
    if kv != "f16" and flash_attention is None:
        return kv, f"KV-Cache {kv} angenommen (Flash Attention unbekannt)"
    return kv, None


@dataclass
class Estimate:
    weights_bytes: int
    kv_bytes: int | None
    graph_bytes: int
    driver_bytes: int
    kv_type: str
    tokens: int                                    # context x parallel slots
    notes: list[str] = field(default_factory=list)
    confidence: Confidence = "normal"

    @property
    def total_bytes(self) -> int | None:
        if self.kv_bytes is None:
            return None
        return self.weights_bytes + self.kv_bytes + self.graph_bytes + self.driver_bytes


def _layer_heads(rec: ModelRecord, blocks: int, notes: list[str]) -> list[int] | None:
    """KV heads per layer. A list in the GGUF = per layer (0 = layer without attention)."""
    raw = rec.info("attention.head_count_kv")
    if isinstance(raw, list):
        heads = [_int(v) or 0 for v in raw]
        if len(heads) != blocks:
            notes.append("KV-Köpfe pro Schicht passen nicht zur Schichtzahl")
            heads = (heads + [max(heads, default=0)] * blocks)[:blocks]
        return heads
    kv = _int(raw)
    if kv is None:
        kv = _int(rec.info("attention.head_count"))
        if kv is None:
            return None
        notes.append("KV-Köpfe fehlen – wie volle Aufmerksamkeit gerechnet (obere Grenze)")
    return [kv] * blocks


def estimate_vram(rec: ModelRecord, ctx: ContextResult, kv_type: str, cfg: CatalogSettings,
                  kv_note: str | None = None) -> Estimate:
    tokens = ctx.effective * ctx.parallel
    est = Estimate(weights_bytes=rec.size, kv_bytes=None, graph_bytes=round(cfg.graph_reserve_gib * GIB),
                   driver_bytes=round(cfg.driver_overhead_gib * GIB), kv_type=kv_type, tokens=tokens)
    if kv_note:
        est.notes.append(kv_note)
    if ctx.parallel > 1:
        est.notes.append(f"{ctx.parallel} parallele Anfragen – KV-Cache {ctx.parallel}-fach")
    if "embedding" in rec.capabilities and "completion" not in rec.capabilities:
        est.kv_bytes = 0
        est.notes.append("Einbettungsmodell: kein KV-Cache")
        return est

    blocks = _int(rec.info("block_count"))
    heads = _layer_heads(rec, blocks, est.notes) if blocks else None
    if not blocks or heads is None:
        est.notes.append("Modelldaten unvollständig (Schichten/Köpfe fehlen) – keine Schätzung")
        est.confidence = "low"
        return est
    head_count = _int(rec.info("attention.head_count")) or 0
    width = _int(rec.info("embedding_length")) or 0
    key_len = _int(rec.info("attention.key_length")) or (width // head_count if head_count else 0)
    val_len = _int(rec.info("attention.value_length")) or key_len
    if not key_len:
        est.notes.append("Kopfgröße unbekannt – keine Schätzung")
        est.confidence = "low"
        return est

    # which layers hold a KV cache, and for how many tokens
    tokens_per_layer = [tokens] * blocks
    arch = rec.architecture or ""
    interval = _int(rec.info("full_attention_interval"))
    if interval and interval > 1:
        # hybrid models (e.g. Qwen3-Next): attention only in every n-th layer, the rest is recurrent
        for i in range(blocks):
            if (i + 1) % interval:
                heads[i] = 0
        est.notes.append(f"Hybrid: KV-Cache nur in jeder {interval}. Schicht")
    elif any(".ssm." in k for k in rec.model_info):
        est.notes.append("Hybrid-/SSM-Architektur ohne Schichtplan – KV für alle Schichten gerechnet (zu hoch)")
        est.confidence = "low"
    window = _int(rec.info("attention.sliding_window"))
    if window and window < tokens:
        every = SWA_EVERY.get(arch)
        if every:
            for i in range(blocks):
                if (i + 1) % every:                       # sliding-window layer: only the window is cached
                    tokens_per_layer[i] = window
            est.notes.append(f"Sliding Window {_num(window)} Token in {blocks - blocks // every} von {blocks} Schichten")
        else:
            est.notes.append("Sliding Window mit unbekanntem Schichtplan – volle Länge gerechnet (zu hoch)")
            est.confidence = "low"
    if rec.info("attention.kv_lora_rank") is not None:
        est.notes.append("MLA-Architektur – tatsächlicher KV-Cache vermutlich deutlich kleiner")
        est.confidence = "low"

    per_element = KV_BYTES[kv_type]
    kv = sum(h * (key_len + val_len) * t * per_element for h, t in zip(heads, tokens_per_layer))
    est.kv_bytes = round(kv)
    attn_layers = sum(1 for h in heads if h)
    kv_heads = max(heads) if heads else 0
    est.notes.insert(0, f"{attn_layers} Schichten × {kv_heads} KV-Köpfe × {key_len}+{val_len} × {kv_type}")
    return est


# ---- verdict --------------------------------------------------------------------------------------

def placement(size: int, size_vram: int) -> Placement:
    """Same rule as the dashboard (offload_threshold 1.0): anything below 100 % on the GPU is a split."""
    if size <= 0:
        return "unknown"
    if size_vram <= 0:
        return "cpu"
    return "gpu" if size_vram >= size else "split"


@dataclass(frozen=True)
class Seen:
    """One observation the verdict can rely on (same digest, same context, same GPU)."""
    size_bytes: int
    vram_bytes: int


@dataclass(frozen=True)
class Verdict:
    state: VerdictState
    basis: Basis
    need_bytes: int | None                  # what Ollama counts (same scale as the dashboard)
    expected_bytes: int | None              # need + driver overhead = what the card must hold
    vram_total_bytes: int | None
    message: str


def verdict(est: Estimate | None, seen: Seen | None, vram_total: int | None, cfg: CatalogSettings,
            embedding: bool = False) -> Verdict:
    driver = round(cfg.driver_overhead_gib * GIB)
    if seen is not None:
        where = placement(seen.size_bytes, seen.vram_bytes)
        need = seen.size_bytes                          # the whole model, also the part that went to the CPU
        expected = need + driver
        if where == "split":
            pct = int(seen.vram_bytes / seen.size_bytes * 100)
            return Verdict("split", "measured", need, expected, vram_total,
                           f"Teil-Offload beobachtet: nur {pct} % auf der GPU – Absturzgefahr.")
        if where == "cpu":
            return Verdict("cpu", "measured", need, expected, vram_total, "Lief komplett auf der CPU (beobachtet).")
        if vram_total and expected > cfg.tight_ratio * vram_total and not embedding:
            return Verdict("tight", "measured", need, expected, vram_total,
                           f"Lief komplett auf der GPU, aber knapp: ≈ {gib(expected)} von {gib(vram_total)}.")
        return Verdict("fits", "measured", need, expected, vram_total, "Läuft komplett auf der GPU (beobachtet).")
    total = est.total_bytes if est is not None else None
    if est is None or total is None:
        return Verdict("unknown", "none", None, None, vram_total, "Keine Schätzung möglich – Modelldaten fehlen.")
    need = total - est.driver_bytes
    if not vram_total:
        return Verdict("unknown", "estimated", need, total, None, f"≈ {gib(total)} erwartet – GPU-Größe unbekannt.")
    if total <= cfg.tight_ratio * vram_total:
        return Verdict("fits", "estimated", need, total, vram_total,
                       f"Sollte komplett auf die GPU passen: ≈ {gib(total)} von {gib(vram_total)}.")
    if total <= vram_total:
        return Verdict("tight", "estimated", need, total, vram_total,
                       f"Knapp: ≈ {gib(total)} von {gib(vram_total)} – andere Programme auf der GPU kippen es.")
    return Verdict("split", "estimated", need, total, vram_total,
                   f"≈ {gib(total)} von {gib(vram_total)} – Teil-Offload zu erwarten, Absturzgefahr. "
                   f"num_ctx verkleinern.")
