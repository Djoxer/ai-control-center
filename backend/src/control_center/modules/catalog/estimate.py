"""Context length, VRAM estimate and the verdict "fits on the GPU?" - pure functions, no I/O.

Two numbers meet here, like a moving van and a garage:
- the NEED of a model on Ollama's own scale (the size /api/ps reports): weights + KV cache + compute
  buffers. Measured if the model ran with this context, otherwise estimated - and the estimate is
  calibrated as soon as any model with the same weights was measured once.
- the BUDGET of the card: total VRAM minus what other programs occupy (measured while Ollama had
  nothing loaded) minus the reserve Ollama keeps free itself.
Need above budget = Ollama splits the model between GPU and CPU = the Blackwell crash case.

KV cache per token and layer = KV heads x (key length + value length) x bytes per element.
Architectures that keep KV only in some layers (sliding window, hybrid/recurrent, MLA) are handled
where the metadata says so; everything unusual lowers the confidence instead of guessing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from control_center.modules.catalog.collector import ModelRecord
from control_center.modules.catalog.settings import CatalogSettings

GIB = 1024 ** 3

ContextSource = Literal["model", "server", "fallback", "request"]
Confidence = Literal["normal", "low"]
Placement = Literal["gpu", "split", "cpu", "unknown"]
VerdictState = Literal["fits", "tight", "split", "cpu", "unknown"]
Basis = Literal["measured", "estimated", "none"]
OtherSource = Literal["measured", "assumed"]

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
                      cfg: CatalogSettings, requested: int | None = None) -> ContextResult:
    """requested = a context a client (or the test) asks for; it beats the model's own value."""
    own = _int(rec.param("num_ctx"))
    own = own if own and own > 0 else None
    trained = _int(rec.info("context_length"))
    if requested:
        value, source = requested, "request"
    elif own:
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


# ---- KV cache -------------------------------------------------------------------------------------

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
class KvPlan:
    """How a model stores its KV cache - computed once from the GGUF metadata, then priced per context."""
    layers: list[tuple[int, int]]           # per layer: (elements per token, sliding window or 0)
    summary: str                            # "8 Schichten × 4 KV-Köpfe × 256+256"
    notes: list[str] = field(default_factory=list)
    confidence: Confidence = "normal"

    def bytes_at(self, tokens: int, per_element: float) -> int:
        total = 0.0
        for elements, window in self.layers:
            total += elements * (min(tokens, window) if window else tokens) * per_element
        return round(total)


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


def kv_plan(rec: ModelRecord) -> KvPlan | None:
    """None = the metadata does not allow a formula (blocks, heads or head size missing)."""
    notes: list[str] = []
    blocks = _int(rec.info("block_count"))
    heads = _layer_heads(rec, blocks, notes) if blocks else None
    if not blocks or heads is None:
        return None
    head_count = _int(rec.info("attention.head_count")) or 0
    width = _int(rec.info("embedding_length")) or 0
    key_len = _int(rec.info("attention.key_length")) or (width // head_count if head_count else 0)
    val_len = _int(rec.info("attention.value_length")) or key_len
    if not key_len:
        return None
    confidence: Confidence = "normal"
    arch = rec.architecture or ""

    interval = _int(rec.info("full_attention_interval"))
    if interval and interval > 1:
        # hybrid models (Qwen3-Next, Qwen3.5): attention only in every n-th layer, the rest is recurrent
        for i in range(blocks):
            if (i + 1) % interval:
                heads[i] = 0
        notes.append(f"Hybrid: KV-Cache nur in jeder {interval}. Schicht")
    elif any(".ssm." in k for k in rec.model_info):
        notes.append("Hybrid-/SSM-Architektur ohne Schichtplan – KV für alle Schichten gerechnet (zu hoch)")
        confidence = "low"

    per_token = [h * (key_len + val_len) for h in heads]
    summary_heads, summary_k, summary_v = max(heads, default=0), key_len, val_len
    mla_rank = _int(rec.info("attention.kv_lora_rank"))
    if mla_rank is not None:
        if rec.info("attention.key_length_mla") is not None:
            # newer GGUF: llama.cpp keeps only the compressed latent + the rope part per token and layer
            rope = _int(rec.info("rope.dimension_count")) or 64
            per_token = [mla_rank + rope if h else 0 for h in heads]
            summary_heads, summary_k, summary_v = 1, mla_rank + rope, 0
            notes.append("MLA: komprimierter KV-Cache (latent + RoPE)")
        else:
            notes.append("Älteres GGUF ohne MLA-Angaben: voller KV-Cache angenommen – erst eine Messung zeigt, "
                         "ob Ollama hier komprimiert")
            confidence = "low"

    windows = [0] * blocks
    window = _int(rec.info("attention.sliding_window"))
    if window:
        every = SWA_EVERY.get(arch)
        if every:
            for i in range(blocks):
                if (i + 1) % every:                       # sliding-window layer: only the window is cached
                    windows[i] = window
            notes.append(f"Sliding Window {_num(window)} Token in {blocks - blocks // every} von {blocks} Schichten")
        else:
            notes.append("Sliding Window mit unbekanntem Schichtplan – volle Länge gerechnet (zu hoch)")
            confidence = "low"

    attn_layers = sum(1 for e in per_token if e)
    summary = f"{attn_layers} Schichten × {summary_heads} KV-Köpfe × {summary_k}+{summary_v}"
    return KvPlan(layers=list(zip(per_token, windows)), summary=summary, notes=notes, confidence=confidence)


# ---- estimate -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Point:
    """One measurement of a model with the same weights: Ollama's size at a context length."""
    num_ctx: int
    size_bytes: int


@dataclass
class Estimate:
    weights_bytes: int                             # file size (includes vision encoders Ollama may skip)
    kv_bytes: int | None
    graph_bytes: int
    kv_type: str
    tokens: int                                    # context x parallel slots
    notes: list[str] = field(default_factory=list)
    confidence: Confidence = "normal"
    calibrated: str | None = None                  # German: from which measurements, None = formula only
    need_bytes: int | None = None                  # Ollama's scale: calibrated if possible, else the formula

    @property
    def formula_bytes(self) -> int | None:
        if self.kv_bytes is None:
            return None
        return self.weights_bytes + self.kv_bytes + self.graph_bytes


VISION_NOTE = ("Bild-Encoder: steckt in der Dateigröße und damit in der Formel – Ollama zählt ihn später nicht "
               "mit, die Karte belegt ihn trotzdem")


def _embedding(rec: ModelRecord) -> bool:
    return "embedding" in rec.capabilities and "completion" not in rec.capabilities


def estimate_vram(rec: ModelRecord, ctx: ContextResult, kv_type: str, cfg: CatalogSettings,
                  kv_note: str | None = None, points: list[Point] | None = None) -> Estimate:
    """Formula first, then calibration against measurements of models with the same weights (points)."""
    tokens = ctx.effective * ctx.parallel
    per_element = KV_BYTES[kv_type]
    est = Estimate(weights_bytes=rec.size, kv_bytes=None, graph_bytes=round(cfg.graph_reserve_gib * GIB),
                   kv_type=kv_type, tokens=tokens)
    if kv_note:
        est.notes.append(kv_note)
    if ctx.parallel > 1:
        est.notes.append(f"{ctx.parallel} parallele Anfragen – KV-Cache {ctx.parallel}-fach")
    plan: KvPlan | None
    if _embedding(rec):
        est.kv_bytes = 0
        est.notes.append("Einbettungsmodell: kein KV-Cache")
        plan = KvPlan(layers=[], summary="")
    else:
        plan = kv_plan(rec)
        if plan is None:
            est.notes.append("Modelldaten unvollständig (Schichten, Köpfe oder Kopfgröße fehlen) – keine Formel")
            est.confidence = "low"
        else:
            est.kv_bytes = plan.bytes_at(tokens, per_element)
            est.notes.insert(0, f"{plan.summary} × {kv_type}")
            est.notes.extend(plan.notes)
            est.confidence = plan.confidence
            if "vision" in rec.capabilities:
                est.notes.append(VISION_NOTE)
    est.need_bytes = est.formula_bytes
    _calibrate(est, plan, points or [], ctx.parallel, per_element)
    return est


def _calibrate(est: Estimate, plan: KvPlan | None, points: list[Point], parallel: int, per_element: float) -> None:
    """Replace the formula by measurements of the same weights where there are some.

    Two contexts measured: a straight line through both (constant part + real cost per token).
    One context measured: its constant part (size minus the formula's KV cache) + the formula's KV cache.
    """
    usable = sorted({p.num_ctx: p for p in points if p.num_ctx > 0 and p.size_bytes > 0}.values(),
                    key=lambda p: p.num_ctx)
    if not usable:
        return
    lo, hi = usable[0], usable[-1]
    target = est.tokens
    if hi.num_ctx > lo.num_ctx:
        slope = (hi.size_bytes - lo.size_bytes) / ((hi.num_ctx - lo.num_ctx) * parallel)
        if slope >= 0:
            base = lo.size_bytes - slope * lo.num_ctx * parallel
            est.need_bytes = round(base + slope * target)
            est.calibrated = f"aus 2 Messungen ({_num(lo.num_ctx)} und {_num(hi.num_ctx)} Token)"
            est.confidence = "normal"
            return
    # one measurement (or an implausible line): keep the measured constant part, price the context by formula
    nearest = min(usable, key=lambda p: abs(p.num_ctx * parallel - target))
    if plan is None:
        if nearest.num_ctx * parallel == target:
            est.need_bytes = nearest.size_bytes
            est.calibrated = f"gleich der Messung bei {_num(nearest.num_ctx)} Token"
        return
    measured_kv = plan.bytes_at(nearest.num_ctx * parallel, per_element)
    est.need_bytes = nearest.size_bytes - measured_kv + (est.kv_bytes or 0)
    est.calibrated = f"an 1 Messung ({_num(nearest.num_ctx)} Token) kalibriert"


# ---- budget and verdict ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Budget:
    """What the card offers Ollama: total minus other programs minus Ollama's own reserve."""
    total_bytes: int | None
    other_bytes: int
    other_source: OtherSource                       # measured while nothing was loaded, or the setting
    reserve_bytes: int

    @property
    def available_bytes(self) -> int | None:
        if not self.total_bytes:
            return None
        return max(0, self.total_bytes - self.other_bytes - self.reserve_bytes)


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
    need_bytes: int | None                  # Ollama's scale (same number as the dashboard)
    available_bytes: int | None             # card minus other programs minus Ollama's reserve
    message: str
    extra_bytes: int = 0                    # the card holds this beyond Ollama's count and the reserve (test run)


def extra_beyond_reserve(overhead_bytes: int | None, budget: Budget) -> int:
    """What a runner holds beyond Ollama's count (measured by a test run) and beyond the reserve that covers
    the usual CUDA context. Text models on the AI box: 0.2 GiB -> 0. qwen3.5 (vision): 1.2 GiB -> 0.75 GiB."""
    if not overhead_bytes:
        return 0
    return max(0, overhead_bytes - budget.reserve_bytes)


def verdict(est: Estimate | None, seen: Seen | None, budget: Budget, cfg: CatalogSettings,
            embedding: bool = False, extra_bytes: int = 0) -> Verdict:
    """Ollama splits a model when ITS count does not fit (need > available); the card overflows when the
    count plus what Ollama does not count does not fit. Both end badly on this card, so both are compared:
    need + extra against the budget."""
    avail = budget.available_bytes
    extra = max(0, extra_bytes)
    plus = f" + {gib(extra)} außerhalb Ollamas Zählung" if extra else ""

    def v(state: VerdictState, basis: Basis, need: int | None, message: str) -> Verdict:
        return Verdict(state, basis, need, avail, message, extra)

    if seen is not None:
        where = placement(seen.size_bytes, seen.vram_bytes)
        need = seen.size_bytes                          # the whole model, also the part that went to the CPU
        if where == "split":
            pct = int(seen.vram_bytes / seen.size_bytes * 100)
            return v("split", "measured", need, f"Teil-Offload beobachtet: nur {pct} % auf der GPU – Absturzgefahr.")
        if where == "cpu":
            return v("cpu", "measured", need, "Lief komplett auf der CPU (beobachtet).")
        if avail and need + extra > cfg.tight_ratio * avail and not embedding:
            return v("tight", "measured", need,
                     f"Lief komplett auf der GPU, aber knapp: {gib(need)}{plus} von {gib(avail)} verfügbar.")
        return v("fits", "measured", need, "Läuft komplett auf der GPU (beobachtet).")
    need = est.need_bytes if est is not None else None
    if need is None:
        return v("unknown", "none", None, "Keine Schätzung möglich – Modelldaten fehlen.")
    if not avail:
        return Verdict("unknown", "estimated", need, None, f"≈ {gib(need)}{plus} Bedarf – GPU-Größe unbekannt.", extra)
    if need + extra <= cfg.tight_ratio * avail or embedding:
        return v("fits", "estimated", need,
                 f"Sollte komplett auf die GPU passen: ≈ {gib(need)}{plus} von {gib(avail)} verfügbar.")
    if need + extra <= avail:
        return v("tight", "estimated", need,
                 f"Knapp: ≈ {gib(need)}{plus} von {gib(avail)} verfügbar – mehr Last durch andere Programme kippt es.")
    if need <= avail:                                   # Ollama would load it fully - the card cannot hold it
        return v("split", "estimated", need,
                 f"≈ {gib(need)}{plus} von {gib(avail)} verfügbar – Ollama lädt es komplett, aber die Karte reicht "
                 f"nicht: Absturz- oder Bremsgefahr. num_ctx verkleinern.")
    return v("split", "estimated", need,
             f"≈ {gib(need)}{plus} von {gib(avail)} verfügbar – Teil-Offload zu erwarten, Absturzgefahr. "
             f"num_ctx verkleinern.")
