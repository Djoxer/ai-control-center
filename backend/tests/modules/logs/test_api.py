import logging

import pytest
from fastapi.testclient import TestClient

from control_center.main import create_app


@pytest.fixture
def client(settings, tmp_path):
    ollama_dir = tmp_path / "ollama"
    ollama_dir.mkdir()
    (ollama_dir / "server.log").write_text(
        'time=2026-10-06T12:00:00Z level=INFO source=routes.go:1 msg="Listening on [::]:11434"\n'
        "ggml_cuda_init: found 1 CUDA devices\n", encoding="utf-8")
    # [modules.logs] as it would come from the toml
    mods = type(settings.modules).model_validate({"logs": {"sources": [
        {"key": "ollama", "title": "Ollama", "format": "text", "paths": [str(ollama_dir / "server*.log")]},
        {"key": "missing", "title": "Missing", "paths": [str(tmp_path / "nope" / "*.log")]},
    ]}})
    s = settings.model_copy(update={"modules": mods})
    with TestClient(create_app(s)) as c:                 # real modules package
        yield c


def test_logs_module_is_running(client):
    states = {m["key"]: m["state"] for m in client.get("/api/v1/meta/modules").json()}
    assert states["logs"] == "running"


def test_sources(client):
    src = {s["key"]: s for s in client.get("/api/v1/logs/sources").json()}
    assert set(src) == {"control-center", "ollama", "missing"}
    assert src["ollama"]["available"] is True and src["ollama"]["files"][0]["sizeBytes"] > 0
    assert src["missing"]["available"] is False and src["missing"]["files"] == []


def test_own_log_is_readable(client):
    logging.getLogger("control_center.modules.logs").warning("needle-123")
    for h in logging.getLogger("control_center").handlers:
        h.flush()
    page = client.get("/api/v1/logs/entries", params={"source": "control-center", "q": "needle-123"}).json()
    assert page["entries"][0]["msg"] == "needle-123"
    assert page["entries"][0]["level"] == "WARNING"
    assert page["nextCursor"] is None


def test_ollama_entries_and_level_filter(client):
    page = client.get("/api/v1/logs/entries", params={"source": "ollama"}).json()
    assert [e["msg"] for e in page["entries"]] == ["ggml_cuda_init: found 1 CUDA devices", "Listening on [::]:11434"]
    page = client.get("/api/v1/logs/entries", params={"source": "ollama", "level": "INFO"}).json()
    assert len(page["entries"]) == 1


def test_errors(client):
    assert client.get("/api/v1/logs/entries", params={"source": "nope"}).status_code == 404
    assert client.get("/api/v1/logs/entries", params={"source": "ollama", "cursor": "x"}).status_code == 400
    assert client.get("/api/v1/logs/entries", params={"source": "ollama", "level": "LOUD"}).status_code == 422


def test_invalid_module_config_fails_only_this_module(settings):
    mods = type(settings.modules).model_validate({"logs": {"tail_interval_s": -1}})
    with TestClient(create_app(settings.model_copy(update={"modules": mods}))) as c:
        health = c.get("/api/v1/health").json()
        logs = next(m for m in health["modules"] if m["key"] == "logs")
        assert logs["state"] == "failed" and "tail_interval_s" in logs["error"]
        assert c.get("/api/v1/logs/sources").status_code == 503
