"""OpenCode suitability: tools measured, context and VRAM computed, and the opencode.json entry."""
import json
from datetime import datetime, timezone

import pytest

from control_center.modules.catalog.estimate import ContextResult
from control_center.modules.catalog.estimate import Verdict as Judgement
from control_center.modules.catalog.schemas import ToolCase, ToolCheck, Verdict
from control_center.modules.catalog.settings import CatalogSettings
from control_center.modules.catalog.suitability import config_key, opencode_block, opencode_fit

CFG = CatalogSettings()
GIB = 1024 ** 3
NOW = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)


def ctx(effective, trained=262144):
    return ContextResult(effective=effective, source="model", own=effective, server=65536, trained=trained,
                         clamped=False, parallel=1)


def v(state="fits", need=7):
    return Judgement(state, "estimated", round(need * GIB), round(14 * GIB), "…")


def to_schema(j):
    return Verdict(state=j.state, basis=j.basis, need_bytes=j.need_bytes, available_bytes=j.available_bytes,
                   message=j.message)


def tools(passed=3, skipped=None, simulated=False):
    cases = [ToolCase(key=k, label=k, ok=i < passed, detail="ok" if i < passed else "nur als Text")
             for i, k in enumerate(["read", "choose", "types"])]
    return ToolCheck(passed=passed, total=3, skipped=skipped, simulated=simulated, cases=[] if skipped else cases)


def fit(name="qwen3.5-9b-64k-code:latest", c=None, now=None, at_min=None, t=None, tps=125.1):
    return opencode_fit(name, c or ctx(65536), now or v(), at_min, t, NOW if t else None, tps, CFG, to_schema)


def test_the_opencode_model_of_the_team_fits():
    f = fit(t=tools())
    assert f.state == "fits" and f.reasons[0] == "Geeignet für OpenCode"
    assert "3/3 strukturiert" in f.reasons[1] and "65.536 Token – reicht" in f.reasons[2]
    assert (f.tools_passed, f.tools_total, f.eval_tps, f.config_key) == (3, 3, 125.1, "qwen3.5-9b-64k-code")


def test_without_tools_in_the_template_no_test_is_needed():
    f = opencode_fit("deepseek-coder-24k:latest", ctx(65536), v(), None, None, None, None, CFG, to_schema,
                     tool_capability=False)
    assert f.state == "no" and "keine Tool-Unterstützung" in f.reasons[1]


def test_untested_tools_leave_it_open():
    f = fit()
    assert f.state == "unknown" and "noch nicht geprüft" in f.reasons[1] and f.tools_passed is None


@pytest.mark.parametrize("t, state, words", [
    (tools(passed=0), "no", "0/3 – nur als Text"),                       # qwen2.5-coder
    (tools(passed=2), "maybe", "nur 2/3"),
    (tools(skipped="Ollama lehnt Tools für dieses Modell ab."), "no", "lehnt Tools"),   # deepseek-coder-v2
])
def test_tool_results_decide(t, state, words):
    f = fit(t=t)
    assert f.state == state and any(words in r for r in f.reasons)


def test_a_small_context_asks_for_a_variant_when_it_would_fit():
    f = fit(c=ctx(24576), at_min=(ctx(65536), v("fits", 7.5)), t=tools())
    assert f.state == "maybe" and f.at_min.state == "fits"
    assert any("Variante mit num_ctx 65536 anlegen, passt laut Schätzung (≈ 7,5 GiB)" in r for r in f.reasons)
    assert '"context": 24576' in f.block                                    # the block says what IS there


@pytest.mark.parametrize("at_min, words", [
    ((ctx(32768, trained=32768), v("fits", 12)), "Trainiert auf 32.768 Token"),   # qwen2.5-coder: clamped
    ((ctx(65536), v("split", 17.2)), "Teil-Offload (≈ 17,2 GiB)"),                 # deepseek-coder-v2 at 64k
])
def test_a_context_that_cannot_reach_the_minimum_rules_it_out(at_min, words):
    f = fit(c=ctx(24576), at_min=at_min, t=tools())
    assert f.state == "no" and any(words in r for r in f.reasons)


def test_the_card_has_the_last_word():
    assert fit(now=v("split"), t=tools()).state == "no"
    tight = fit(now=v("tight"), t=tools())
    assert tight.state == "maybe" and any("VRAM knapp" in r for r in tight.reasons)


def test_simulated_tools_say_so():
    assert "(Simulation)" in fit(t=tools(simulated=True)).reasons[1]


def test_opencode_block_is_valid_json_with_a_sane_output_limit():
    block = opencode_block("qwen3.5-9b-64k-code:latest", 65536, CFG)
    entry = json.loads("{" + block + "}")
    assert entry == {"qwen3.5-9b-64k-code": {"name": "qwen3.5-9b-64k-code",
                                             "limit": {"context": 65536, "output": 8192}}}
    small = json.loads("{" + opencode_block("tiny:1b", 8192, CFG) + "}")
    assert small["tiny:1b"]["limit"] == {"context": 8192, "output": 2048}       # never more than a quarter
    assert config_key("hf.co/org/model:Q4_K_M") == "hf.co/org/model:Q4_K_M"
