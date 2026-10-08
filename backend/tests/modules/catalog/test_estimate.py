"""Context length, VRAM estimate, calibration, budget and verdict - the numbers the offload protection uses."""
import asyncio
from datetime import datetime, timezone

import pytest

from control_center.adapters.common import SAMPLES_DIR
from control_center.adapters.ollama import FakeOllama
from control_center.modules.catalog.collector import record_from
from control_center.modules.catalog.estimate import (
    GIB, Budget, ContextResult, Point, Seen, effective_context, effective_kv_type, estimate_vram, kv_plan,
    placement, verdict,
)
from control_center.modules.catalog.settings import CatalogSettings

from .factories import QWEN2, rec

CFG = CatalogSettings()
MIB = 1024 ** 2
# the AI box on 08.10.: 16303 MiB card, 1424 MiB used by other programs with Ollama empty, Ollama's reserve
BOX = Budget(total_bytes=16303 * MIB, other_bytes=1424 * MIB, other_source="measured", reserve_bytes=round(0.45 * GIB))


def ctx(effective, parallel=1, **kw):
    return ContextResult(effective=effective, source="model", own=None, server=None, trained=None, clamped=False,
                         parallel=parallel, **kw)


def real(name):
    """A model exactly as the AI box reported it (scenario real-catalog, recorded 08.10.)."""
    fake = FakeOllama(SAMPLES_DIR / "real-catalog")
    tag = next(t for t in asyncio.run(fake.tags()) if t.name == name)
    return record_from(tag, asyncio.run(fake.show(name)), datetime.now(timezone.utc))


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


def test_requested_context_beats_everything_but_is_clamped_too():
    own = rec("m", params={"num_ctx": ["8192"]}, info=QWEN2)
    r = effective_context(own, 65536, 1, CFG, requested=16384)
    assert (r.effective, r.source, r.own) == (16384, "request", 8192)
    assert effective_context(own, 65536, 1, CFG, requested=131072).effective == 32768


def test_context_clamp_can_be_switched_off_and_junk_is_ignored():
    plain = rec("m", info=QWEN2, params={"num_ctx": ["nope"]})
    c = effective_context(plain, 65536, 2, CFG.model_copy(update={"clamp_to_trained": False}))
    assert (c.effective, c.source, c.own, c.clamped, c.parallel) == (65536, "server", None, False, 2)


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


# ---- formula ----------------------------------------------------------------------------------------

def test_estimate_standard_transformer_exact():
    coder = rec("coder", info=QWEN2, size=8_988_124_069)
    est = estimate_vram(coder, ctx(32768), "q8_0", CFG)
    # 48 layers x 8 KV heads x (128 + 128) x 34/32 bytes x 32768 tokens
    assert est.kv_bytes == 48 * 8 * 256 * 34 * 32768 // 32 == 3_422_552_064
    assert est.need_bytes == est.formula_bytes == 8_988_124_069 + 3_422_552_064 + round(0.4 * GIB)
    assert est.calibrated is None and est.confidence == "normal"
    assert est.notes[0] == "48 Schichten × 8 KV-Köpfe × 128+128 × q8_0"


def test_parallel_slots_multiply_the_kv_cache():
    coder = rec("coder", info=QWEN2)
    one = estimate_vram(coder, ctx(8192), "f16", CFG).kv_bytes
    two = estimate_vram(coder, ctx(8192, parallel=2), "f16", CFG)
    assert two.kv_bytes == 2 * one and any("2 parallele" in n for n in two.notes)


def test_real_qwen35_hybrid_with_per_layer_heads():
    """Qwen3.5 9B as Ollama ships it: 8 of 32 layers with 4 KV heads of 256+256, plus a vision encoder."""
    m = real("qwen3.5-9b-64k-code:latest")
    est = estimate_vram(m, ctx(65536), "q8_0", CFG)
    assert est.kv_bytes == 8 * 4 * 512 * 34 * 65536 // 32 == 1_140_850_688
    assert est.notes[0] == "8 Schichten × 4 KV-Köpfe × 256+256 × q8_0"
    assert any("Hybrid" in n for n in est.notes) and any("Bild-Encoder" in n for n in est.notes)


def test_real_qwen35_calibrated_by_the_measurement_of_07_10():
    """real-normal (07.10.): this very model at 65,536 tokens = 7,172,322,753 bytes laut Ollama.

    The formula says ~7.6 GiB (the file contains the vision encoder). One measurement fixes the constant part,
    and the relatives with the same weights (24k variant) inherit it.
    """
    measured = [Point(65536, 7_172_322_753)]
    same_ctx = estimate_vram(real("qwen3.5-9b-64k-code:latest"), ctx(65536), "q8_0", CFG, points=measured)
    assert same_ctx.need_bytes == 7_172_322_753 and "1 Messung" in same_ctx.calibrated
    relative = estimate_vram(real("qwen35-24k:latest"), ctx(24576), "q8_0", CFG, points=measured)
    kv_24k = 8 * 4 * 512 * 34 * 24576 // 32
    assert relative.need_bytes == 7_172_322_753 - 1_140_850_688 + kv_24k
    assert relative.formula_bytes - relative.need_bytes > 0.8 * GIB      # the formula alone was too high


def test_two_measurements_give_the_real_cost_per_token():
    coder = rec("coder", info=QWEN2, size=8_000_000_000)
    pts = [Point(8192, 9_000_000_000), Point(32768, 11_000_000_000)]    # 81,380 bytes per token, measured
    est = estimate_vram(coder, ctx(16384), "q8_0", CFG, points=pts)
    assert est.need_bytes == round(9_000_000_000 + (16384 - 8192) * 2_000_000_000 / (32768 - 8192))
    assert est.calibrated == "aus 2 Messungen (8.192 und 32.768 Token)" and est.confidence == "normal"
    # a falling line is nonsense (other settings at the time): fall back to the nearest single measurement
    odd = estimate_vram(coder, ctx(16384), "q8_0", CFG, points=[Point(8192, 9e9), Point(32768, 8e9)])
    assert "1 Messung (8.192 Token)" in odd.calibrated


def test_calibration_without_formula_only_at_the_measured_context():
    blind = rec("x", info={"general.architecture": "x"})
    exact = estimate_vram(blind, ctx(4096), "f16", CFG, points=[Point(4096, 5000)])
    assert exact.need_bytes == 5000 and exact.calibrated == "gleich der Messung bei 4.096 Token"
    assert estimate_vram(blind, ctx(8192), "f16", CFG, points=[Point(4096, 5000)]).need_bytes is None


def test_real_deepseek_old_gguf_has_full_kv_and_says_so():
    m = real("deepseek-coder-v2:16b")
    est = estimate_vram(m, ctx(65536), "q8_0", CFG)
    assert est.kv_bytes == 27 * 16 * 320 * 34 * 65536 // 32
    assert est.confidence == "low" and any("Älteres GGUF ohne MLA" in n for n in est.notes)


def test_newer_mla_gguf_keeps_only_the_latent():
    info = {"general.architecture": "deepseek2", "deepseek2.block_count": 2, "deepseek2.attention.head_count": 16,
            "deepseek2.attention.head_count_kv": 16, "deepseek2.attention.key_length": 192,
            "deepseek2.attention.value_length": 128, "deepseek2.attention.kv_lora_rank": 512,
            "deepseek2.attention.key_length_mla": 192, "deepseek2.rope.dimension_count": 64}
    est = estimate_vram(rec("m", info=info), ctx(10), "f16", CFG)
    assert est.kv_bytes == 2 * (512 + 64) * 2 * 10 and est.confidence == "normal"


def test_real_gpt_oss_sliding_window():
    est = estimate_vram(real("gpt-oss:20b"), ctx(65536), "q8_0", CFG)
    assert est.kv_bytes == 12 * 8 * 128 * 34 * 65536 // 32 + 12 * 8 * 128 * 34 * 128 // 32
    assert any("Sliding Window 128 Token in 12 von 24" in n for n in est.notes)
    plan = kv_plan(real("gpt-oss:20b"))
    assert plan.bytes_at(100, 2.0) == 24 * 8 * 128 * 2 * 100       # below the window every layer is "full"


def test_unknown_sliding_window_layout_and_ssm_without_plan_are_low_confidence():
    swa = {"general.architecture": "newarch", "newarch.block_count": 2, "newarch.attention.head_count": 2,
           "newarch.embedding_length": 128, "newarch.attention.sliding_window": 16}
    assert estimate_vram(rec("m", info=swa), ctx(1000), "f16", CFG).confidence == "low"
    ssm = {"general.architecture": "mamba-ish", "mamba-ish.block_count": 4, "mamba-ish.attention.head_count": 4,
           "mamba-ish.embedding_length": 64, "mamba-ish.ssm.inner_size": 128}
    est = estimate_vram(rec("m", info=ssm), ctx(100), "f16", CFG)
    assert est.confidence == "low" and est.kv_bytes is not None


def test_per_layer_head_list_and_mismatched_length():
    info = {"general.architecture": "lfm2", "lfm2.block_count": 4, "lfm2.attention.head_count": 8,
            "lfm2.attention.head_count_kv": [0, 8, 0, 8], "lfm2.attention.key_length": 64}
    assert estimate_vram(rec("m", info=info), ctx(10), "f16", CFG).kv_bytes == 2 * 8 * 128 * 2 * 10
    info["lfm2.attention.head_count_kv"] = [0, 8]
    est = estimate_vram(rec("m", info=info), ctx(10), "f16", CFG)
    assert est.kv_bytes == 3 * 8 * 128 * 2 * 10 and any("passen nicht" in n for n in est.notes)


def test_embedding_model_has_no_kv_cache():
    est = estimate_vram(real("nomic-embed-text:latest"), ctx(2048), "q8_0", CFG)
    assert est.kv_bytes == 0 and est.need_bytes == 274_302_450 + round(0.4 * GIB)


def test_missing_metadata_gives_no_number():
    est = estimate_vram(rec("m", info={"general.architecture": "x"}), ctx(10), "f16", CFG)
    assert est.kv_bytes is None and est.need_bytes is None and est.confidence == "low"
    assert estimate_vram(rec("m", info={"general.architecture": "x", "x.block_count": 2}), ctx(10), "f16",
                         CFG).kv_bytes is None


# ---- budget and verdict -----------------------------------------------------------------------------

def test_budget_of_the_ai_box():
    assert BOX.available_bytes == (16303 - 1424) * MIB - round(0.45 * GIB)       # ~14.08 GiB
    assert Budget(None, 0, "assumed", 0).available_bytes is None
    assert Budget(100, 200, "measured", 0).available_bytes == 0


def _est(need_gib):
    e = estimate_vram(rec("m", caps=["embedding"], info={}, size=0), ctx(1), "f16", CFG)
    e.need_bytes = round(need_gib * GIB)
    return e


@pytest.mark.parametrize("need_gib, state", [(10, "fits"), (12.8, "tight"), (14.05, "tight"), (14.2, "split")])
def test_verdict_from_the_estimate_against_the_budget(need_gib, state):
    v = verdict(_est(need_gib), None, BOX, CFG)
    assert (v.state, v.basis, v.available_bytes) == (state, "estimated", BOX.available_bytes)
    if state == "split":
        assert "num_ctx verkleinern" in v.message


def test_real_gpt_oss_is_right_at_the_edge():
    """gpt-oss:20b crashed on the box (split). The estimate at 64k lands at 99.8 % of the budget."""
    est = estimate_vram(real("gpt-oss:20b"), ctx(65536), "q8_0", CFG)
    v = verdict(est, None, BOX, CFG)
    assert v.state == "tight" and est.need_bytes / BOX.available_bytes > 0.99


def test_verdict_measured_beats_estimated():
    est = _est(10)
    split = verdict(est, Seen(size_bytes=100, vram_bytes=84), BOX, CFG)
    assert (split.state, split.basis) == ("split", "measured") and "84 %" in split.message
    assert verdict(est, Seen(100, 0), BOX, CFG).state == "cpu"
    full = verdict(_est(16.5), Seen(round(8 * GIB), round(8 * GIB)), BOX, CFG)
    assert (full.state, full.basis, full.need_bytes) == ("fits", "measured", round(8 * GIB))
    tight = verdict(est, Seen(round(13.5 * GIB), round(13.5 * GIB)), BOX, CFG)
    assert tight.state == "tight"


def test_verdict_without_numbers_or_gpu():
    assert verdict(None, None, BOX, CFG).state == "unknown"
    v = verdict(_est(10), None, Budget(None, 0, "assumed", 0), CFG)
    assert (v.state, v.basis, v.need_bytes) == ("unknown", "estimated", round(10 * GIB))
    assert verdict(_est(30), None, BOX, CFG, embedding=True).state == "fits"


@pytest.mark.parametrize("size, vram, where", [(0, 0, "unknown"), (10, 0, "cpu"), (10, 9, "split"), (10, 10, "gpu")])
def test_placement(size, vram, where):
    assert placement(size, vram) == where
