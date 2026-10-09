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


def test_usage_endpoint(make):
    c = make()
    ready(c)
    r = c.post("/api/v1/catalog/usage", json={"name": "qwen3.5-9b-64k-code:latest", "tags": ["opencode"],
                                              "note": "Standard in OpenCode"})
    assert r.status_code == 200
    m = by_name(r.json())["qwen3.5-9b-64k-code:latest"]
    assert m["usage"]["tags"] == ["opencode"] and m["usage"]["note"] == "Standard in OpenCode"
    assert by_name(c.get("/api/v1/catalog/overview").json())["qwen3.5-9b-64k-code:latest"]["usage"]["tags"] == ["opencode"]
    assert c.post("/api/v1/catalog/usage", json={"name": "nope:1", "tags": []}).status_code == 404
    assert c.post("/api/v1/catalog/usage", json={"name": "qwen3.5:9b", "tags": ["chef"]}).status_code == 422
    assert c.post("/api/v1/catalog/usage", json={"name": "qwen3.5:9b", "note": "x" * 201}).status_code == 422
    r = c.post("/api/v1/catalog/usage", json={"name": "qwen3.5:9b", "tags": ["test"]}, headers=CROSS_SITE)
    assert r.status_code == 403


def test_tool_check_in_the_simulation_of_the_ai_box(make):
    """real-catalog: coder14 writes its tool calls as text (as seen with Continue), qwen3.5 calls them properly."""
    c = make("real-catalog")
    ready(c)
    assert c.post("/api/v1/catalog/bench", json={"name": "coder14:latest"}).status_code == 202
    coder = wait_bench(c)["result"]["tools"]
    assert (coder["passed"], coder["simulated"]) == (0, True) and "nur als Text" in coder["cases"][0]["detail"]
    assert c.post("/api/v1/catalog/bench", json={"name": "qwen3.5-9b-64k-code:latest"}).status_code == 202
    assert wait_bench(c)["result"]["tools"]["passed"] == 3
    m = by_name(c.get("/api/v1/catalog/overview").json())
    code = m["qwen3.5-9b-64k-code:latest"]["opencode"]
    assert code["state"] == "fits" and code["toolsSimulated"] and code["configKey"] == "qwen3.5-9b-64k-code"
    assert m["coder14:latest"]["opencode"]["state"] == "no"
    assert m["qwen2.5-coder:14b-instruct-q4_K_M"]["opencode"]["state"] == "no"      # same digest as coder14
    assert any("Trainiert auf 32.768" in r for r in m["qwen2.5-coder:14b-instruct-q4_K_M"]["opencode"]["reasons"])
    assert m["nomic-embed-text:latest"]["opencode"] is None


def test_candidate_endpoints(make):
    c = make()
    ready(c)
    url = "/api/v1/catalog/candidates"
    r = c.post(url, json={"name": "https://ollama.com/library/qwen3-coder:30b"})
    assert r.status_code == 200 and r.json()["name"] == "qwen3-coder:30b"
    cand = r.json()["overview"]["candidates"][0]
    assert (cand["name"], cand["simulated"], cand["pull"]) == ("qwen3-coder:30b", True, "ollama pull qwen3-coder:30b")
    assert cand["verdict"]["state"] == "split" and cand["fitsUpTo"] is None and cand["architecture"] == "qwen3moe"
    assert cand["weightsBytes"] == 18_556_688_736 and cand["capabilities"] == ["completion", "tools"]
    assert r.json()["overview"]["library"] == {"simulated": True, "hosts": ["registry.ollama.ai", "hf.co"]}
    # normal scenario: qwen3.5:9b is installed with other weights and Ollama 0.12.6 is too old for the sample
    q = c.post(url, json={"name": "qwen3.5:9b"}).json()["overview"]["candidates"][0]
    assert q["installed"] and q["requiresOk"] is False and q["notes"][0].startswith("Braucht Ollama ≥ 0.17.1")
    assert [x["name"] for x in c.get("/api/v1/catalog/overview").json()["candidates"]] == ["qwen3.5:9b",
                                                                                          "qwen3-coder:30b"]
    g = c.post(url, json={"name": "gemma4"}).json()["overview"]["candidates"][0]          # gemma4:latest
    assert g["estimate"]["confidence"] == "normal" and g["verdict"]["needBytes"] < 7.6 * 1024 ** 3   # 09.10.: 12,1
    assert g["fitsUpTo"] == 131072 and {s["state"] for s in g["steps"]} == {"fits"}
    assert c.delete(url, params={"name": "gemma4:latest"}).status_code == 200
    assert c.post(url, json={"name": "nope:1b"}).status_code == 404
    bad = c.post(url, json={"name": "evil.example/x/y"})
    assert bad.status_code == 400 and "nicht freigegeben" in bad.json()["detail"]
    assert c.post(url, json={"name": ""}).status_code == 422
    assert c.post(url, json={"name": "gemma3:12b"}, headers=CROSS_SITE).status_code == 403
    assert c.delete(url, params={"name": "qwen3.5:9b"}, headers=CROSS_SITE).status_code == 403
    gone = c.delete(url, params={"name": "qwen3.5:9b"})
    assert gone.status_code == 200 and [x["name"] for x in gone.json()["candidates"]] == ["qwen3-coder:30b"]
    assert c.delete(url, params={"name": "qwen3.5:9b"}).status_code == 404


def test_candidate_check_when_the_registry_is_down(make, monkeypatch):
    from control_center.adapters.library import FakeLibrary, LibraryUnavailable

    async def down(self, ref, timeout):
        raise LibraryUnavailable("registry.ollama.ai nicht erreichbar: ConnectError")

    monkeypatch.setattr(FakeLibrary, "manifest", down)
    c = make()
    ready(c)
    r = c.post("/api/v1/catalog/candidates", json={"name": "gemma3:12b"})
    assert r.status_code == 502 and r.json()["detail"] == (
        "Registry nicht lesbar: registry.ollama.ai nicht erreichbar: ConnectError")


def test_candidate_of_the_ai_box_with_the_same_weights(make):
    """real-catalog: the sample qwen3.5:9b carries the weights digest of the installed one."""
    c = make("real-catalog")
    ready(c)
    q = c.post("/api/v1/catalog/candidates", json={"name": "qwen3.5:9b"}).json()["overview"]["candidates"][0]
    assert q["installed"] and q["sameWeights"] == ["qwen3.5-9b-64k-code:latest", "qwen3.5-9b-64k:latest",
                                                   "qwen3.5:9b", "qwen35-24k:latest"]
    assert q["notes"][0] == "Schon installiert – mit genau diesen Gewichten."
    assert q["downloadBytes"] == 6_594_474_711 and q["projectorBytes"] == 921_704_832
