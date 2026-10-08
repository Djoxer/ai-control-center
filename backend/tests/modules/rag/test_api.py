"""RAG module end to end: real app, memory store, fake embedder (no Qdrant, no Ollama needed)."""
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_center.main import create_app

CROSS_SITE = {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"}


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    write(root, "src/Note.php", "<?php class Note { function save() {} }")
    write(root, "src/Gallery.php", "<?php class GalleryImage { function upload() {} }")
    write(root, "src/Config.php", "<?php return ['password' => 'Sup3rGeheim!'];")
    return root


@pytest.fixture
def make(settings, repo):
    """make(**rag_options) -> entered TestClient with one source 'bent_php' on the repo fixture."""
    opened = []

    def _make(**options):
        section = {"store": "memory", "embedder": "fake", "progress_interval_s": 0,
                   "sources": [{"collection": "bent_php", "title": "Bent PHP", "path": str(repo),
                                "includes": [{"dir": "src", "ext": [".php"]}]}], **options}
        mods = type(settings.modules).model_validate({"rag": section})
        client = TestClient(create_app(settings.model_copy(update={"modules": mods})))
        client.__enter__()
        opened.append(client)
        return client

    yield _make
    for c in reversed(opened):
        c.__exit__(None, None, None)


def wait_job(client, job_id, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        j = client.get(f"/api/v1/rag/jobs/{job_id}").json()
        if j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.02)
    raise AssertionError(f"job still running: {j}")


def reindex(client, **body):
    r = client.post("/api/v1/rag/jobs", json=body)
    assert r.status_code == 202, r.text
    return wait_job(client, r.json()["id"])


# ---- overview and jobs ----------------------------------------------------------------------------

def test_overview_before_and_after_a_run(make):
    c = make()
    ov = c.get("/api/v1/rag/overview").json()
    assert ov["store"] == {"mode": "memory", "url": None, "reachable": True, "version": "memory", "error": None,
                           "local": True, "port": None}
    assert ov["writes"] == {"allowed": True, "reason": None} and ov["maxChars"] == 6000
    src = ov["sources"][0]
    assert src["pathExists"] and src["stats"] is None and src["lastRun"] is None and ov["job"] is None

    job = reindex(c)
    assert job["state"] == "done" and job["model"] == "fake-hash-256"
    r = job["sources"][0]
    assert (r["files"], r["indexed"], r["secret"]) == (3, 2, 1)
    assert r["secretHits"] == [{"file": "src/Config.php", "line": 1, "rule": "assignment", "allowed": False}]

    ov = c.get("/api/v1/rag/overview").json()
    assert ov["sources"][0]["stats"]["points"] == 2 and ov["sources"][0]["lastRun"]["indexed"] == 2
    assert ov["collections"] == [{"name": "bent_php", "status": "green", "points": 2, "indexedVectors": 2,
                                  "segments": 1, "vectorSize": 256, "distance": "Cosine", "source": "Bent PHP"}]
    assert ov["job"]["id"] == job["id"]


def test_job_requests_that_are_refused(make):
    c = make()
    assert c.post("/api/v1/rag/jobs", json={"collections": ["nope"]}).status_code == 404
    assert c.get("/api/v1/rag/jobs/nope").status_code == 404
    assert c.post("/api/v1/rag/jobs", json={}, headers=CROSS_SITE).status_code == 403
    assert make(sources=[]).post("/api/v1/rag/jobs", json={}).status_code == 409


def test_only_one_job_at_a_time_and_cancel(make):
    c = make(fake_delay_s=0.05)
    first = c.post("/api/v1/rag/jobs", json={}).json()
    second = c.post("/api/v1/rag/jobs", json={})
    assert second.status_code == 409 and "schon eine Indexierung" in second.json()["detail"]
    assert c.delete("/api/v1/rag/collections/bent_php", params={"confirm": "bent_php"}).status_code in (404, 409)
    cancelled = c.post(f"/api/v1/rag/jobs/{first['id']}/cancel").json()
    assert cancelled["cancelRequested"] is True
    final = wait_job(c, first["id"])
    assert final["state"] == "cancelled" and final["sources"][0]["removed"] == 0
    # cancelling a finished job changes nothing
    assert c.post(f"/api/v1/rag/jobs/{first['id']}/cancel").json()["state"] == "cancelled"


def test_jobs_survive_a_restart(make, settings):
    c = make()
    job = reindex(c)
    c.__exit__(None, None, None)
    stored = json.loads((settings.data_dir / "rag" / "jobs.json").read_text(encoding="utf-8"))
    assert [j["id"] for j in stored] == [job["id"]]
    c2 = make()
    assert [j["id"] for j in c2.get("/api/v1/rag/jobs").json()] == [job["id"]]
    assert c2.get("/api/v1/rag/overview").json()["sources"][0]["lastRun"]["indexed"] == 2


def test_job_log_is_a_log_source(make, settings):
    c = make()
    reindex(c)
    sources = {s["key"]: s for s in c.get("/api/v1/logs/sources").json()}
    assert sources["rag"]["title"] == "RAG-Indexierung"
    lines = [json.loads(line) for line in (settings.log_dir / "rag.log").read_text(encoding="utf-8").splitlines()]
    assert any("secret filter skipped src/Config.php" in e["msg"] for e in lines)
    assert all("Sup3rGeheim" not in e["msg"] for e in lines)


# ---- write protection -----------------------------------------------------------------------------

def test_remote_qdrant_is_read_only_unless_allowed(make):
    # TEST-NET address: certainly not this machine, nothing answers (short timeout)
    c = make(store="qdrant", qdrant_url="http://192.0.2.10:6333", qdrant_timeout_s=0.2)
    ov = c.get("/api/v1/rag/overview").json()
    assert ov["store"]["reachable"] is False and ov["store"]["local"] is False and ov["store"]["port"] == 6333
    assert ov["writes"]["allowed"] is False and "allow_remote_writes" in ov["writes"]["reason"]
    r = c.post("/api/v1/rag/jobs", json={})
    assert r.status_code == 409 and "Schreibschutz" in r.json()["detail"]
    r = c.delete("/api/v1/rag/collections/bent_php", params={"confirm": "bent_php"})
    assert r.status_code == 409 and "Schreibschutz" in r.json()["detail"]

    c2 = make(store="qdrant", qdrant_url="http://192.0.2.10:6333", qdrant_timeout_s=0.2, allow_remote_writes=True)
    assert c2.get("/api/v1/rag/overview").json()["writes"]["allowed"] is True


def test_qdrant_url_uses_ai_host(make, settings):
    c = make(store="qdrant", qdrant_timeout_s=0.2)          # default http://{ai_host}:6333, ai_host 127.0.0.1
    ov = c.get("/api/v1/rag/overview").json()
    assert ov["store"]["url"] == "http://127.0.0.1:6333" and ov["store"]["local"] is True


# ---- delete and search ----------------------------------------------------------------------------

def test_delete_needs_the_name_twice(make):
    c = make()
    reindex(c)
    assert c.delete("/api/v1/rag/collections/bent_php").status_code == 422          # confirm missing
    assert c.delete("/api/v1/rag/collections/bent_php", params={"confirm": "x"}).status_code == 400
    assert c.delete("/api/v1/rag/collections/bent_php", params={"confirm": "bent_php"},
                    headers=CROSS_SITE).status_code == 403
    assert c.delete("/api/v1/rag/collections/bent_php", params={"confirm": "bent_php"}).status_code == 204
    assert c.delete("/api/v1/rag/collections/bent_php", params={"confirm": "bent_php"}).status_code == 404
    assert c.get("/api/v1/rag/overview").json()["collections"] == []


def test_search_finds_the_matching_file(make):
    c = make()
    reindex(c)
    r = c.post("/api/v1/rag/search", json={"collection": "bent_php", "query": "GalleryImage upload", "limit": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "fake-hash-256" and len(body["hits"]) == 2
    assert body["hits"][0]["filename"] == "src/Gallery.php" and body["hits"][0]["truncated"] is False
    assert body["hits"][0]["score"] >= body["hits"][1]["score"]


def test_search_errors(make):
    c = make()
    assert c.post("/api/v1/rag/search", json={"collection": "bent_php", "query": "x"}).status_code == 404
    reindex(c)
    assert c.post("/api/v1/rag/search", json={"collection": "bent_php", "query": "x", "limit": 51}).status_code == 422
    assert c.post("/api/v1/rag/search", json={"collection": "bent_php", "query": ""}).status_code == 422
    assert c.post("/api/v1/rag/search", json={"collection": "bent_php", "query": "x"},
                  headers=CROSS_SITE).status_code == 403


def test_invalid_config_fails_only_this_module(settings):
    mods = type(settings.modules).model_validate({"rag": {"sources": [
        {"collection": "a", "title": "A", "path": "x", "includes": [{"dir": "src", "ext": [".php"]}]},
        {"collection": "a", "title": "B", "path": "y", "includes": [{"dir": "src", "ext": [".php"]}]},
    ]}})
    with TestClient(create_app(settings.model_copy(update={"modules": mods}))) as c:
        states = {m["key"]: m for m in c.get("/api/v1/meta/modules").json()}
        assert states["rag"]["state"] == "failed" and "unique" in states["rag"]["error"]
        assert states["dashboard"]["state"] == "running"
        r = c.get("/api/v1/rag/overview")                                  # router mounted, service missing
        assert r.status_code == 503 and r.json()["detail"] == "RAG-Modul läuft nicht"


def test_no_sources_no_log_source(make):
    c = make(sources=[])
    assert "rag" not in {s["key"] for s in c.get("/api/v1/logs/sources").json()}
    assert c.get("/api/v1/rag/overview").json()["sources"] == []
