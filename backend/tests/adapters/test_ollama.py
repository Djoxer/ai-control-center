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
