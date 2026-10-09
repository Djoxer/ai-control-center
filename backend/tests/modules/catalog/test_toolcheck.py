"""Tool-call check: what counts as a structured tool call an agent can execute, and the fake's replay."""
import asyncio

import pytest

from control_center.adapters.common import SAMPLES_DIR
from control_center.adapters.ollama import ChatResult, FakeOllama, OllamaRequestFailed, ToolCall
from control_center.modules.catalog.toolcheck import CASES, NO_TOOLS, evaluate, messages

READ, CHOOSE, TYPES = CASES


def answer(*calls, content="", done="stop", thinking=0, tokens=30):
    return ChatResult(content=content, thinking_chars=thinking, tool_calls=tuple(calls), total_s=1.0,
                      eval_tokens=tokens, eval_s=0.3, done_reason=done)


def test_three_cases_with_tools_an_agent_would_send():
    assert [c.key for c in CASES] == ["read", "choose", "types"]
    assert [len(c.tools) for c in CASES] == [1, 2, 1]                       # "choose" must pick one of two
    msgs = messages(READ)
    assert [m["role"] for m in msgs] == ["system", "user"] and "src/app/app.config.ts" in msgs[1]["content"]
    search = TYPES.tools[0]["function"]["parameters"]
    assert search["properties"]["max_results"]["type"] == "integer" and search["required"] == [
        "pattern", "path", "max_results"]


@pytest.mark.parametrize("case, call", [
    (READ, ToolCall("read_file", {"path": "src/app/app.config.ts"})),
    (READ, ToolCall("read_file", {"path": ".\\src\\app\\app.config.ts"})),    # Windows spelling counts
    (CHOOSE, ToolCall("run_command", {"command": "cd frontend && npm test"})),
    (TYPES, ToolCall("search", {"pattern": "TODO", "path": "./src/", "max_results": 5})),
])
def test_structured_calls_pass(case, call):
    ok, detail = evaluate(case, answer(call))
    assert ok and detail.startswith(call.name + "(")


@pytest.mark.parametrize("case, result, words", [
    # qwen2.5-coder on the AI box: the call as a JSON block in the text
    (READ, answer(content='```json\n{"name": "read_file", "arguments": {"path": "x"}}\n```'), "nur als Text"),
    (READ, answer(content="Die Datei enthält die App-Konfiguration."), "Kein Tool-Call"),
    (READ, answer(done="length", thinking=5000, tokens=2048), "Abgebrochen nach 2048 Token ohne Tool-Call (denkt"),
    (CHOOSE, answer(ToolCall("read_file", {"path": "package.json"})), "Falsches Werkzeug: read_file statt run_command"),
    (CHOOSE, answer(ToolCall("run_command", {"cmd": "npm test"})), "command fehlt"),
    (TYPES, answer(ToolCall("search", {"pattern": "TODO", "path": "src", "max_results": "5"})), "(Text statt Zahl)"),
    (TYPES, answer(ToolCall("search", {"pattern": "TODO", "path": "src", "max_results": True})), "max_results = True"),
    (TYPES, answer(ToolCall("search", {"pattern": "FIXME", "path": "lib", "max_results": 50})),
     "pattern = 'FIXME', path = 'lib', max_results = 50"),
    (READ, answer(ToolCall("read_file", {"_raw": "{path: x"})), "kein gültiges JSON"),
])
def test_what_an_agent_cannot_use_fails_with_a_reason(case, result, words):
    ok, detail = evaluate(case, result)
    assert not ok and words in detail


def chat(fake, name, case):
    return asyncio.run(fake.chat(name, messages(case), list(case.tools), {}, "5m", 10))


def test_fake_replays_recorded_answers_per_model():
    fake = FakeOllama(SAMPLES_DIR / "real-catalog")
    good = [evaluate(c, chat(fake, "qwen3.5-9b-64k-code:latest", c))[0] for c in CASES]
    assert good == [True, True, True]
    text = [evaluate(c, chat(fake, "coder14:latest", c))[1] for c in CASES]
    assert all("nur als Text" in t for t in text)                              # as observed with Continue
    with pytest.raises(OllamaRequestFailed, match=NO_TOOLS):                  # no tools in the template
        chat(fake, "deepseek-coder-v2:16b", READ)


def test_every_scenario_with_models_has_answers_for_every_case():
    for scenario in ("normal", "offload", "idle", "real-catalog"):
        fake = FakeOllama(SAMPLES_DIR / scenario)
        assert all(chat(fake, "qwen3.5-9b-64k-code:latest", c).tool_calls for c in CASES), scenario
