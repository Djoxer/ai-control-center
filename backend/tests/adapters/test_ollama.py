import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from control_center.adapters.common import SAMPLES_DIR
from control_center.adapters.ollama import FakeOllama, HttpOllama, OllamaUnavailable, parse_ps

BASE = "http://ai-box:11434"


def raw(name="m:latest", size=100, size_vram=100, **extra) -> dict:
    return {"name": name, "model": name, "digest": "abc", "size": size, "size_vram": size_vram,
            "details": {"family": "qwen3", "parameter_size": "9.0B", "quantization_level": "Q4_K_M"}, **extra}


def test_parse_recorded_sample():
    data = json.loads((SAMPLES_DIR / "normal" / "ollama-ps.json").read_text(encoding="utf-8"))
    models = parse_ps(data)
    assert [m.name for m in models] == ["qwen3.5-9b-64k-code:latest", "nomic-embed-text:latest"]
    m = models[0]
    assert m.size == m.size_vram and m.context_length == 65536 and m.quantization == "Q4_K_M"
    # Go timestamp with nanoseconds and +02:00 offset
    assert m.expires_at == datetime(2026, 10, 6, 10, 4, 0, 123456, tzinfo=timezone.utc)


@pytest.mark.parametrize("payload", [{"models": []}, {"models": None}, {}])
def test_parse_empty_variants(payload):
    assert parse_ps(payload) == []


def test_parse_tolerates_missing_and_odd_fields():
    m = parse_ps({"models": [{"name": "x", "size": 0, "expires_at": "0001-01-01T00:00:00Z"}, "garbage"]})[0]
    assert (m.size, m.size_vram, m.context_length, m.expires_at, m.family) == (0, 0, None, None, None)


@pytest.mark.parametrize("payload", [[], "x", {"models": "nope"}])
def test_parse_rejects_wrong_shape(payload):
    with pytest.raises(OllamaUnavailable):
        parse_ps(payload)


def run(coro):
    return asyncio.run(coro)


async def _with_client(fn):
    async with httpx.AsyncClient(timeout=1) as client:
        return await fn(HttpOllama(client, BASE + "/"))       # trailing slash must not double up


@respx.mock
def test_http_running_and_version():
    respx.get(f"{BASE}/api/ps").respond(json={"models": [raw(size=100, size_vram=84)]})
    respx.get(f"{BASE}/api/version").respond(json={"version": "0.12.6"})
    models = run(_with_client(lambda o: o.running()))
    assert models[0].size_vram == 84
    assert run(_with_client(lambda o: o.version())) == "0.12.6"


@pytest.mark.parametrize("route", [
    lambda r: r.mock(side_effect=httpx.ConnectError("refused")),
    lambda r: r.respond(500),
    lambda r: r.respond(200, text="<html>not json</html>"),
])
@respx.mock
def test_http_failures_become_unavailable(route):
    route(respx.get(f"{BASE}/api/ps"))
    with pytest.raises(OllamaUnavailable, match="/api/ps"):
        run(_with_client(lambda o: o.running()))


def test_fake_shifts_expiry_to_now(tmp_path):
    captured = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    (tmp_path / "meta.json").write_text(json.dumps({"capturedAt": captured.isoformat()}))
    (tmp_path / "ollama-ps.json").write_text(json.dumps({"models": [
        raw("soon", expires_at=(captured + timedelta(minutes=4)).isoformat()),
        raw("pinned", expires_at="2318-01-01T00:00:00Z"),
    ]}))
    soon, pinned = run(FakeOllama(tmp_path).running())
    left = soon.expires_at - datetime.now(timezone.utc)
    assert timedelta(minutes=3, seconds=50) < left <= timedelta(minutes=4)
    assert pinned.expires_at.year == 2318                     # "forever" stays forever


def test_fake_without_files_is_offline(tmp_path):
    with pytest.raises(OllamaUnavailable, match="ollama-ps.json missing"):
        run(FakeOllama(tmp_path).running())
    with pytest.raises(OllamaUnavailable):
        run(FakeOllama(SAMPLES_DIR / "ollama-down").version())


# ---- /api/tags and /api/show (catalog) ------------------------------------------------------------

from control_center.adapters.ollama import (  # noqa: E402  (grouped with the tests that use them)
    OllamaModelMissing,
    parse_parameters,
    parse_show,
    parse_tags,
    system_placeholder,
    weights_digest,
)

BLOB = "a" * 64


def test_parse_tags_sample_and_order():
    data = json.loads((SAMPLES_DIR / "normal" / "ollama-tags.json").read_text(encoding="utf-8"))
    models = parse_tags(data)
    names = [m.name for m in models]
    assert names == sorted(names)                              # Ollama sorts by date, we by name
    coder = next(m for m in models if m.name.startswith("qwen3.5-9b-64k-code"))
    assert coder.parent_model == "qwen3.5-9b-64k:latest" and coder.quantization == "Q4_K_M"
    assert coder.modified_at is not None and coder.modified_at.tzinfo is not None
    base = next(m for m in models if m.name == "qwen3.5:9b")
    assert base.parent_model is None                           # "" means: no parent


@pytest.mark.parametrize("payload", [{"models": []}, {"models": None}, {}])
def test_parse_tags_empty_variants(payload):
    assert parse_tags(payload) == []


def test_parse_tags_skips_garbage_and_nameless():
    assert [m.name for m in parse_tags({"models": ["x", {"size": 3}, {"model": "only-model:1"}]})] == ["only-model:1"]


@pytest.mark.parametrize("payload", [[], "x", {"models": "nope"}])
def test_parse_tags_rejects_wrong_shape(payload):
    with pytest.raises(OllamaUnavailable):
        parse_tags(payload)


def test_parse_parameters_quotes_repeats_and_junk():
    text = ('num_ctx                        65536\n'
            'stop                           "<|im_start|>"\n'
            'stop                           "say \\"hi\\""\n'
            'temperature                    0.2\n'
            'lonely\n\n')
    assert parse_parameters(text) == {"num_ctx": ("65536",), "stop": ("<|im_start|>", 'say "hi"'),
                                      "temperature": ("0.2",)}
    assert parse_parameters(None) == {}


@pytest.mark.parametrize("modelfile, expected", [
    (f"# FROM x:latest\nFROM C:\\Users\\someone\\.ollama\\models\\blobs\\sha256-{BLOB}\nTEMPLATE x", BLOB),
    (f"FROM /usr/share/ollama/.ollama/models/blobs/sha256:{BLOB.upper()}", BLOB),
    (f'FROM "D:\\models\\blobs\\sha256-{BLOB}"', BLOB),
    (f"FROM <blobs>/sha256-{BLOB}", BLOB),                    # what capture_samples writes
    ("FROM qwen3:8b", None),                                   # a model name, not a blob
    (None, None),
])
def test_weights_digest(modelfile, expected):
    assert weights_digest(modelfile) == expected


def test_parse_show_reduces_and_keeps_lengths():
    payload = {"details": {"parent_model": "", "family": "qwen3", "families": ["qwen3"], "format": "gguf"},
               "parameters": "num_ctx 4096", "template": "{{ .Prompt }}", "system": "Be brief.",
               "modelfile": f"FROM /x/blobs/sha256-{BLOB}", "model_info": {"general.architecture": "qwen3"},
               "capabilities": ["completion", "tools"], "license": "MIT " * 1000}
    d = parse_show("m:latest", payload)
    assert (d.parent_model, d.family, d.parameters, d.system_chars) == (None, "qwen3", {"num_ctx": ("4096",)}, 9)
    assert d.weights_digest == BLOB and d.capabilities == ("completion", "tools")
    assert d.template_hash and d.system_hash and d.template_hash != d.system_hash
    assert not hasattr(d, "license")                           # big texts are not carried around
    # a recorded sample keeps only the length of the system prompt
    assert parse_show("m", {**payload, "system": system_placeholder(412)}).system_chars == 412
    empty = parse_show("m", {})
    assert (empty.parameters, empty.model_info, empty.capabilities, empty.system_chars) == ({}, {}, (), 0)
    with pytest.raises(OllamaUnavailable):
        parse_show("m", ["nope"])


@respx.mock
def test_http_tags_and_show():
    respx.get(f"{BASE}/api/tags").respond(json={"models": [{"name": "b:1", "size": 5}, {"name": "a:1"}]})
    show = respx.post(f"{BASE}/api/show").respond(json={"details": {"family": "qwen3"}, "parameters": "num_ctx 8192"})
    assert [m.name for m in run(_with_client(lambda o: o.tags()))] == ["a:1", "b:1"]
    d = run(_with_client(lambda o: o.show("a:1", timeout=5)))
    assert d.parameters == {"num_ctx": ("8192",)} and json.loads(show.calls[0].request.content) == {"model": "a:1"}


@respx.mock
def test_http_show_404_is_missing_model_not_ollama_down():
    respx.post(f"{BASE}/api/show").respond(404, json={"error": "model 'x' not found"})
    with pytest.raises(OllamaModelMissing):
        run(_with_client(lambda o: o.show("x")))
    respx.post(f"{BASE}/api/show").respond(500)
    with pytest.raises(OllamaUnavailable, match="/api/show x"):
        run(_with_client(lambda o: o.show("x")))


def test_fake_tags_and_show(tmp_path):
    fake = FakeOllama(SAMPLES_DIR / "normal")
    names = [m.name for m in run(fake.tags())]
    for name in names:                                          # every installed model has a recorded show
        assert run(fake.show(name)).name == name
    with pytest.raises(OllamaModelMissing):
        run(fake.show("nope:latest"))
    with pytest.raises(OllamaUnavailable, match="ollama-tags.json missing"):
        run(FakeOllama(tmp_path).tags())
