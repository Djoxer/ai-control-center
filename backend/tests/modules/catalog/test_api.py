"""Catalog end to end: real app, fake adapters (scenario normal), real SQLite in tmp_path."""
import time

import pytest
from fastapi.testclient import TestClient

from control_center.main import create_app

CROSS_SITE = {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"}
SECOND_PC = {"server_context_length": 65536, "kv_cache_type": "q8_0", "flash_attention": True}


@pytest.fixture
def make(settings):
    opened = []

    def _make(scenario="normal", **catalog):
        mods = type(settings.modules).model_validate({"catalog": {**SECOND_PC, **catalog}})
        adapters = settings.adapters.model_copy(update={"fake_scenario": scenario})
        client = TestClient(create_app(settings.model_copy(update={"modules": mods, "adapters": adapters})))
        client.__enter__()
        opened.append(client)
        return client

    yield _make
    for c in reversed(opened):
        c.__exit__(None, None, None)


def ready(client, loaded: str | None = None, timeout=5.0):
    """The first refresh and the first /api/ps look run in the background right after the start."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        ov = client.get("/api/v1/catalog/overview").json()
        done = ov["refreshedAt"] or ov["ollama"]["error"]
        if done and (loaded is None or any(m["name"] == loaded and m["loaded"] for m in ov["models"])):
            return ov
        time.sleep(0.02)
    raise AssertionError("catalog never refreshed")


def by_name(ov):
    return {m["name"]: m for m in ov["models"]}


def test_overview_of_the_normal_scenario(make):
    ov = ready(make(), loaded="qwen3.5-9b-64k-code:latest")
    assert ov["ollama"] == {"online": True, "version": "0.12.6", "error": None, "simulated": True}
    assert [(g["origin"], g["installed"]) for g in ov["groups"]] == [
        ("deepseek-coder-v2:16b", False), ("gpt-oss:20b", True), ("nomic-embed-text:latest", True),
        ("qwen2.5-coder:14b-instruct-q4_K_M", True), ("qwen3.5:9b", True)]
    assert ov["groups"][-1]["members"] == ["qwen3.5:9b", "qwen3.5-9b-64k:latest", "qwen3.5-9b-64k-code:latest",
                                           "qwen35-24k:latest"]
    assert [m["name"] for m in ov["models"]] == [n for g in ov["groups"] for n in g["members"]]   # tree order
    m = by_name(ov)
    code = m["qwen3.5-9b-64k-code:latest"]
    assert code["loaded"] and code["depth"] == 2 and code["parent"]["resolved"] == "qwen3.5-9b-64k:latest"
    assert code["changes"] == ["temperature 0.2", "System-Prompt (170 Zeichen)"]
    assert code["verdict"]["basis"] == "measured" and code["observations"][0]["current"]
    assert m["qwen35-24k:latest"]["parent"] == {"declared": None, "resolved": "qwen3.5:9b", "via": "weights",
                                                "installed": False}
    coder = m["qwen2.5-coder:14b-instruct-q4_K_M"]
    assert coder["context"] == {"effective": 32768, "source": "server", "own": None, "server": 65536,
                                "trained": 32768, "clamped": True, "parallel": 1}
    assert coder["verdict"]["basis"] == "estimated" and coder["estimate"]["kvType"] == "q8_0"
    assert m["gpt-oss:20b"]["verdict"]["state"] == "tight"
    assert ov["hardware"]["key"] == "NVIDIA GeForce RTX 5070 Ti · 16303 MiB"
    assert ov["server"]["source"] == "config" and ov["assumptions"]["tightRatio"] == 0.9


def test_offload_scenario_shows_the_measured_split(make):
    ov = ready(make("offload"), loaded="qwen2.5-coder:14b-instruct-q4_K_M")
    coder = by_name(ov)["qwen2.5-coder:14b-instruct-q4_K_M"]
    assert coder["loaded"] and (coder["verdict"]["state"], coder["verdict"]["basis"]) == ("split", "measured")


def test_refresh_all_one_and_unknown(make):
    c = make()
    ready(c)
    r = c.post("/api/v1/catalog/refresh", json={})
    assert r.status_code == 200 and len(r.json()["models"]) == 8
    r = c.post("/api/v1/catalog/refresh", json={"name": "gpt-oss:20b"})
    assert r.status_code == 200
    r = c.post("/api/v1/catalog/refresh", json={"name": "gibt-es-nicht:1"})
    assert r.status_code == 404 and r.json()["detail"] == "Modell gibt-es-nicht:1 ist nicht installiert"


def test_refresh_is_guarded_against_cross_site_requests(make):
    c = make()
    assert c.post("/api/v1/catalog/refresh", json={}, headers=CROSS_SITE).status_code == 403
    assert c.post("/api/v1/catalog/refresh", json={}, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_ollama_down(make):
    c = make("ollama-down")
    ov = ready(c)
    assert ov["ollama"]["online"] is False and "ollama-tags.json missing" in ov["ollama"]["error"]
    assert ov["models"] == [] and ov["groups"] == []
    r = c.post("/api/v1/catalog/refresh", json={})
    assert r.status_code == 503 and r.json()["detail"].startswith("Ollama nicht erreichbar")


def test_inventory_survives_a_restart_with_ollama_down(make):
    ready(make())
    ov = ready(make("ollama-down"))                     # same data_dir, Ollama gone
    assert ov["ollama"]["online"] is False and len(ov["models"]) == 8


def test_preflight_endpoint(make):
    c = make()
    ready(c)
    r = c.get("/api/v1/catalog/preflight", params={"name": "qwen35-24k:latest", "num_ctx": 8192})
    assert r.status_code == 200
    p = r.json()
    assert p["allowed"] and p["context"] == {"effective": 8192, "source": "request", "own": 24576, "server": 65536,
                                             "trained": 262144, "clamped": False, "parallel": 1}
    assert p["willUnload"] == ["nomic-embed-text:latest", "qwen3.5-9b-64k-code:latest"]
    assert c.get("/api/v1/catalog/preflight", params={"name": "nope:1"}).status_code == 404
    assert c.get("/api/v1/catalog/preflight", params={"name": "x", "num_ctx": 10}).status_code == 422


def wait_bench(client, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        runs = client.get("/api/v1/catalog/bench").json()
        if runs and runs[0]["state"] not in ("queued", "running"):
            return runs[0]
        time.sleep(0.02)
    raise AssertionError("test run did not finish")


def test_bench_endpoints_in_the_simulation(make):
    c = make()
    ready(c)
    r = c.post("/api/v1/catalog/bench", json={"name": "qwen35-24k:latest"})
    assert r.status_code == 202, r.text
    assert r.json()["numCtx"] == 24576
    done = wait_bench(c)
    assert done["state"] == "done" and done["result"]["evalTps"] == 60.0 and "Simulation" in done["result"]["note"]
    assert done["result"]["gpuBeforeBytes"] is None and done["result"]["sizeBytes"] is None   # nothing faked as measured
    assert c.get("/api/v1/catalog/bench", params={"name": "gpt-oss:20b"}).json() == []
    ov = c.get("/api/v1/catalog/overview").json()
    model = by_name(ov)["qwen35-24k:latest"]
    assert model["benches"][0]["id"] == done["id"] and model["testable"]
    # the scenario's card holds loaded models: a simulated run must not take that for "other programs"
    assert ov["budget"]["otherSource"] == "assumed"
    # gpt-oss at 64k is "knapp": only with confirm; the embedding model never
    r = c.post("/api/v1/catalog/bench", json={"name": "gpt-oss:20b"})
    assert r.status_code == 409 and "Knapp" in r.json()["detail"]
    r = c.post("/api/v1/catalog/bench", json={"name": "nomic-embed-text:latest", "confirm": True})
    assert r.status_code == 409 and "Einbettungsmodelle" in r.json()["detail"]
    assert c.post("/api/v1/catalog/bench", json={"name": "nope:1"}).status_code == 404


def test_bench_is_guarded_against_cross_site_requests(make):
    c = make()
    ready(c)
    r = c.post("/api/v1/catalog/bench", json={"name": "qwen35-24k:latest"}, headers=CROSS_SITE)
    assert r.status_code == 403
    assert c.get("/api/v1/catalog/bench").json() == []


def test_real_catalog_scenario(make):
    """The AI box as recorded on 08.10.: 12 models, budget from its empty card (1424 MiB other programs).
    The empty card counts after two /api/ps looks in a row - the shortest interval keeps the test short."""
    c = make("real-catalog", observe_interval_s=2)
    end = time.monotonic() + 8
    while True:
        ov = c.get("/api/v1/catalog/overview").json()
        if ov["refreshedAt"] and ov["budget"]["otherSource"] == "measured":
            break
        assert time.monotonic() < end, ov["budget"]
        time.sleep(0.02)
    assert len(ov["models"]) == 12 and len(ov["groups"]) == 6
    assert ov["budget"]["otherBytes"] == 1424 * 1024 ** 2
    m = by_name(ov)
    assert m["gpt-oss:20b"]["verdict"]["state"] == "tight"
    assert m["deepseek-coder-v2:16b"]["verdict"]["state"] == "split"           # old GGUF, full KV at 64k
    assert m["deepseek-coder-24k:latest"]["verdict"]["state"] == "fits"
    assert m["coder14:latest"]["parent"]["via"] == "copy"


def test_disabled_module_has_no_routes(settings):
    mods = type(settings.modules).model_validate({"disabled": ["catalog"]})
    with TestClient(create_app(settings.model_copy(update={"modules": mods}))) as c:
        assert c.get("/api/v1/catalog/overview").status_code == 404
        mods = {m["key"]: m["state"] for m in c.get("/api/v1/health").json()["modules"]}
        assert mods["catalog"] == "disabled"
