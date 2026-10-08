"""Context length, VRAM estimate and verdict - the numbers the offload protection will build on."""
import pytest

from control_center.modules.catalog.estimate import (
    GIB, ContextResult, Seen, effective_context, effective_kv_type, estimate_vram, placement, verdict,
)
from control_center.modules.catalog.settings import CatalogSettings

from .factories import QWEN2, rec

CFG = CatalogSettings()
GPU_16 = 16303 * 1024 * 1024                         # RTX 5070 Ti as NVML reports it


def ctx(effective, parallel=1, **kw):
    return ContextResult(effective=effective, source="model", own=None, server=None, trained=None, clamped=False,
                         parallel=parallel, **kw)


# ---- context ----------------------------------------------------------------------------------------

def test_context_sources_in_order():
    own = rec("m", params={"num_ctx": ["8192"]}, info=QWEN2)
    assert effective_context(own, 65536, 1, CFG).source == "model"
    plain = rec("m", info=QWEN2)
    c = effective_context(plain, 65536, 1, CFG)
    # server default 64k, but the model was trained on 32k: Ollama cuts it down
    assert (c.effective, c.source, c.clamped, c.trained, c.server) == (32768, "server", True, 32768, 65536)
    f = effective_context(rec("m", info={}), None, None, CFG)
    assert (f.effective, f.source, f.clamped, f.parallel) == (4096, "fallback", False, 1)


def test_context_clamp_can_be_switched_off_and_junk_is_ignored():
    plain = rec("m", info=QWEN2, params={"num_ctx": ["nope"]})
    c = effective_context(plain, 65536, 2, CFG.model_copy(update={"clamp_to_trained": False}))
    assert (c.effective, c.source, c.own, c.clamped, c.parallel) == (65536, "server", None, False, 2)


# ---- KV type ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("kv, fa, expected, noted", [
    ("q8_0", True, "q8_0", False),
    ("q8_0", False, "f16", True),                    # quantized KV needs flash attention
    ("q8_0", None, "q8_0", True),                    # unknown FA: assume it, but say so
    (None, None, "f16", False),
    ("q5_1", True, "f16", True),                     # unknown type
])
def test_effective_kv_type(kv, fa, expected, noted):
    t, note = effective_kv_type(kv, fa)
    assert t == expected and (note is not None) == noted


# ---- estimate ---------------------------------------------------------------------------------------

def test_estimate_standard_transformer_exact():
    coder = rec("coder", info=QWEN2, size=8_988_124_069)
    est = estimate_vram(coder, ctx(32768), "q8_0", CFG)
    # 48 layers x 8 KV heads x (128 + 128) x 34/32 bytes x 32768 tokens
    assert est.kv_bytes == 48 * 8 * 256 * 34 * 32768 // 32 == 3_422_552_064
    assert est.total_bytes == 8_988_124_069 + 3_422_552_064 + round(0.4 * GIB) + round(1.2 * GIB)
    assert est.confidence == "normal" and est.notes[0] == "48 Schichten × 8 KV-Köpfe × 128+128 × q8_0"


def test_parallel_slots_multiply_the_kv_cache():
    coder = rec("coder", info=QWEN2)
    one = estimate_vram(coder, ctx(8192), "f16", CFG).kv_bytes
    two = estimate_vram(coder, ctx(8192, parallel=2), "f16", CFG)
    assert two.kv_bytes == 2 * one and any("2 parallele" in n for n in two.notes)


def test_hybrid_interval_keeps_kv_in_every_nth_layer_only():
    info = {"general.architecture": "qwen3next", "qwen3next.block_count": 48, "qwen3next.attention.head_count": 16,
            "qwen3next.attention.head_count_kv": 2, "qwen3next.attention.key_length": 256,
            "qwen3next.full_attention_interval": 4, "qwen3next.ssm.state_size": 128}
    est = estimate_vram(rec("next", info=info), ctx(1000), "f16", CFG)
    assert est.kv_bytes == 12 * 2 * 512 * 2 * 1000 and est.confidence == "normal"
    assert est.notes[0].startswith("12 Schichten")


def test_ssm_without_layer_plan_is_low_confidence():
    info = {"general.architecture": "mamba-ish", "mamba-ish.block_count": 4, "mamba-ish.attention.head_count": 4,
            "mamba-ish.embedding_length": 64, "mamba-ish.ssm.inner_size": 128}
    est = estimate_vram(rec("m", info=info), ctx(100), "f16", CFG)
    assert est.confidence == "low" and est.kv_bytes is not None


def test_per_layer_head_list_and_mismatched_length():
    info = {"general.architecture": "lfm2", "lfm2.block_count": 4, "lfm2.attention.head_count": 8,
            "lfm2.attention.head_count_kv": [0, 8, 0, 8], "lfm2.attention.key_length": 64}
    assert estimate_vram(rec("m", info=info), ctx(10), "f16", CFG).kv_bytes == 2 * 8 * 128 * 2 * 10
    info["lfm2.attention.head_count_kv"] = [0, 8]
    est = estimate_vram(rec("m", info=info), ctx(10), "f16", CFG)
    assert est.kv_bytes == 3 * 8 * 128 * 2 * 10 and any("passen nicht" in n for n in est.notes)


def test_sliding_window_gpt_oss():
    info = {"general.architecture": "gptoss", "gptoss.block_count": 24, "gptoss.attention.head_count": 64,
            "gptoss.attention.head_count_kv": 8, "gptoss.attention.key_length": 64, "gptoss.attention.value_length": 64,
            "gptoss.attention.sliding_window": 128}
    est = estimate_vram(rec("oss", info=info), ctx(65536), "f16", CFG)
    assert est.kv_bytes == 12 * 8 * 128 * 2 * 65536 + 12 * 8 * 128 * 2 * 128
    assert est.confidence == "normal"
    small = estimate_vram(rec("oss", info=info), ctx(100), "f16", CFG)       # context below the window
    assert small.kv_bytes == 24 * 8 * 128 * 2 * 100


def test_unknown_sliding_window_layout_and_mla_are_low_confidence():
    swa = {"general.architecture": "newarch", "newarch.block_count": 2, "newarch.attention.head_count": 2,
           "newarch.embedding_length": 128, "newarch.attention.sliding_window": 16}
    assert estimate_vram(rec("m", info=swa), ctx(1000), "f16", CFG).confidence == "low"
    mla = {"general.architecture": "deepseek2", "deepseek2.block_count": 2, "deepseek2.attention.head_count": 2,
           "deepseek2.attention.head_count_kv": 2, "deepseek2.attention.key_length": 192,
           "deepseek2.attention.value_length": 128, "deepseek2.attention.kv_lora_rank": 512}
    est = estimate_vram(rec("m", info=mla), ctx(10), "f16", CFG)
    assert est.confidence == "low" and est.kv_bytes == 2 * 2 * 320 * 2 * 10


def test_embedding_model_has_no_kv_cache():
    est = estimate_vram(rec("nomic", caps=["embedding"], info={}), ctx(2048), "q8_0", CFG)
    assert est.kv_bytes == 0 and est.total_bytes == 1000 + round(0.4 * GIB) + round(1.2 * GIB)


def test_missing_metadata_gives_no_number():
    est = estimate_vram(rec("m", info={"general.architecture": "x"}), ctx(10), "f16", CFG)
    assert est.kv_bytes is None and est.total_bytes is None and est.confidence == "low"
    no_heads = {"general.architecture": "x", "x.block_count": 2}
    assert estimate_vram(rec("m", info=no_heads), ctx(10), "f16", CFG).kv_bytes is None
    no_width = {"general.architecture": "x", "x.block_count": 2, "x.attention.head_count_kv": 2}
    assert estimate_vram(rec("m", info=no_width), ctx(10), "f16", CFG).kv_bytes is None


# ---- verdict ----------------------------------------------------------------------------------------

def _est(total_gib):
    e = estimate_vram(rec("m", caps=["embedding"], info={}, size=0), ctx(1), "f16", CFG)
    e.weights_bytes = round(total_gib * GIB) - e.graph_bytes - e.driver_bytes
    return e


@pytest.mark.parametrize("total_gib, state", [(10, "fits"), (14.5, "tight"), (15.9, "tight"), (16.5, "split")])
def test_verdict_from_the_estimate(total_gib, state):
    v = verdict(_est(total_gib), None, GPU_16, CFG)
    assert (v.state, v.basis) == (state, "estimated")
    if state == "split":
        assert "num_ctx verkleinern" in v.message


def test_verdict_measured_beats_estimated():
    est = _est(10)                                               # estimate says: fits easily
    split = verdict(est, Seen(size_bytes=100, vram_bytes=84), GPU_16, CFG)
    assert (split.state, split.basis) == ("split", "measured") and "84 %" in split.message
    assert verdict(est, Seen(100, 0), GPU_16, CFG).state == "cpu"
    full = verdict(_est(16.5), Seen(round(8 * GIB), round(8 * GIB)), GPU_16, CFG)
    assert (full.state, full.basis) == ("fits", "measured")      # it ran - the estimate was too careful
    tight = verdict(est, Seen(round(14 * GIB), round(14 * GIB)), GPU_16, CFG)
    assert tight.state == "tight" and tight.need_bytes == round(14 * GIB)
    assert tight.expected_bytes == round(14 * GIB) + round(1.2 * GIB)      # the driver comes on top
    split_need = verdict(est, Seen(size_bytes=1000, vram_bytes=840), GPU_16, CFG)
    assert split_need.need_bytes == 1000                                   # the whole model, not just its GPU part


def test_verdict_without_numbers_or_gpu():
    assert verdict(None, None, GPU_16, CFG).state == "unknown"
    v = verdict(_est(10), None, None, CFG)
    assert (v.state, v.basis, v.expected_bytes) == ("unknown", "estimated", round(10 * GIB))
    assert v.need_bytes == round(10 * GIB) - round(1.2 * GIB)              # Ollama's scale: without the driver


@pytest.mark.parametrize("size, vram, where", [(0, 0, "unknown"), (10, 0, "cpu"), (10, 9, "split"), (10, 10, "gpu")])
def test_placement(size, vram, where):
    assert placement(size, vram) == where
