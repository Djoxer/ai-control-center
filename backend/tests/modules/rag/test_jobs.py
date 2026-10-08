"""Reindex logic with the memory store: stable IDs, cleanup rules, secret filter, failures, cancel."""
import asyncio
import logging
from pathlib import Path

import pytest

from control_center.modules.rag import jobs as jobs_module
from control_center.modules.rag.embedder import EmbedError, EmbedUnavailable, FakeEmbedder
from control_center.modules.rag.jobs import Indexer, Job, point_id
from control_center.modules.rag.settings import RagSettings
from control_center.modules.rag.store import MemoryStore, Point

LOG = logging.getLogger("test.rag")


class ScriptedEmbedder(FakeEmbedder):
    """Fake vectors, but chosen texts fail: {"marker": EmbedError(...)} - or a hook per call."""

    def __init__(self, fail: dict[str, Exception] | None = None, dim: int | None = None, hook=None) -> None:
        super().__init__()
        self.fail = fail or {}
        self.dim = dim
        self.hook = hook
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.hook:
            await self.hook(len(self.calls))
        for marker, exc in self.fail.items():
            if marker in text:
                raise exc
        vec = await super().embed(text)
        return vec[: self.dim] if self.dim else vec


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    write(root, "src/A.php", "<?php class A { function note() {} }")
    write(root, "src/B.php", "<?php class B { function gallery() {} }")
    write(root, "src/Long.php", "<?php " + "x" * 7000)
    write(root, "src/Empty.php", "   ")
    return root


def settings(repo: Path, **over) -> RagSettings:
    src = {"collection": "bent_php", "title": "PHP", "path": str(repo), "includes": [{"dir": "src", "ext": [".php"]}]}
    src.update(over.pop("source", {}))
    return RagSettings.model_validate({"sources": [src], "progress_interval_s": 0, **over})


async def run(cfg: RagSettings, store: MemoryStore, embedder=None, base: Path = Path("/"), job=None) -> Job:
    published: list[int] = []
    ix = Indexer(cfg, base, store, embedder or FakeEmbedder(), LOG, lambda j: published.append(j.status.revision))
    job = job or Job("t", cfg.sources, "fake", cfg.max_chars)
    await ix.run(job)
    return job


def ids(store: MemoryStore, name: str = "bent_php") -> set:
    return set(store.data[name].points)


def payload(store: MemoryStore, rel: str, name: str = "bent_php") -> dict:
    return store.data[name].points[point_id(name, rel)][1]


# ---- the normal run -------------------------------------------------------------------------------

def test_first_run_creates_the_collection_like_the_scripts(repo):
    store = MemoryStore()
    job = asyncio.run(run(settings(repo), store))
    r = job.status.sources[0]
    assert job.status.state == "done" and r.state == "done"
    assert (r.files, r.indexed, r.truncated, r.empty, r.secret, r.skipped, r.removed) == (4, 3, 1, 1, 0, 0, 0)
    assert r.created and store.data["bent_php"].distance == "Cosine"
    p = payload(store, "src/Long.php")
    assert set(p) >= {"filename", "text"} and p["filename"] == "src/Long.php"   # what mcp_server.py reads
    assert len(p["text"]) == 6000 and p["truncated"] is True
    assert [f.file for f in r.truncated_files] == ["src/Long.php"]


def test_second_run_overwrites_instead_of_adding(repo):
    store = MemoryStore()
    asyncio.run(run(settings(repo), store))
    first = ids(store)
    job = asyncio.run(run(settings(repo), store))
    assert ids(store) == first and len(first) == 3
    assert job.status.sources[0].created is False and job.status.sources[0].removed == 0


def test_point_ids_are_stable_and_per_collection():
    assert point_id("bent_php", "src/A.php") == point_id("bent_php", "src/A.php")
    assert point_id("bent_php", "src/A.php") != point_id("typo3", "src/A.php")


def test_cleanup_removes_deleted_files_and_the_old_script_points(repo):
    store = MemoryStore()
    asyncio.run(run(settings(repo), store))
    # what index_bent.py left behind: integer IDs 0..n
    asyncio.run(store.upsert("bent_php", [Point(0, [1.0] + [0.0] * 255, {"filename": "\\src\\A.php", "text": "old"}),
                                          Point(1, [0.0, 1.0] + [0.0] * 254, {"filename": "\\src\\B.php", "text": "old"})]))
    (repo / "src" / "B.php").unlink()
    job = asyncio.run(run(settings(repo), store))
    assert job.status.sources[0].removed == 3                       # two script points + B.php
    assert ids(store) == {point_id("bent_php", "src/A.php"), point_id("bent_php", "src/Long.php")}


# ---- secret filter --------------------------------------------------------------------------------

def test_secret_file_is_skipped_and_its_old_point_removed(repo):
    store = MemoryStore()
    asyncio.run(run(settings(repo), store))
    write(repo, "src/B.php", "<?php\nreturn ['password' => 'Sup3rGeheim!'];\n")
    write(repo, "src/.htaccess.php", "<?php ok")
    job = asyncio.run(run(settings(repo), store))
    r = job.status.sources[0]
    assert r.secret == 1 and point_id("bent_php", "src/B.php") not in ids(store)
    assert [(h.file, h.line, h.rule, h.allowed) for h in r.secret_hits] == [("src/B.php", 2, "assignment", False)]
    assert "Sup3rGeheim" not in job.status.model_dump_json()
    assert all("Sup3rGeheim" not in p[1]["text"] for p in store.data["bent_php"].points.values())


def test_secret_by_file_name_is_not_even_read(repo):
    write(repo, "src/secrets.php", "<?php harmless")
    job = asyncio.run(run(settings(repo), MemoryStore()))
    assert [(h.file, h.line, h.rule) for h in job.status.sources[0].secret_hits] == [("src/secrets.php", None, "name:secrets.*")]


def test_allowed_file_is_indexed_and_still_reported(repo):
    write(repo, "src/Demo.php", "<?php $pass = 'demo-pass-1234';")
    store = MemoryStore()
    job = asyncio.run(run(settings(repo, source={"secret_allow": ["src/Demo.php"]}), store))
    r = job.status.sources[0]
    assert r.secret == 0 and point_id("bent_php", "src/Demo.php") in ids(store)
    assert [(h.file, h.allowed) for h in r.secret_hits] == [("src/Demo.php", True)]


# ---- failures: nothing gets lost ------------------------------------------------------------------

def test_no_files_found_fails_and_deletes_nothing(repo, tmp_path):
    store = MemoryStore()
    asyncio.run(run(settings(repo), store))
    before = ids(store)
    for f in (repo / "src").iterdir():
        f.unlink()                                                  # e.g. the repo folder was emptied by mistake
    job = asyncio.run(run(settings(repo), store))
    r = job.status.sources[0]
    assert job.status.state == "failed" and r.state == "failed" and "Keine passenden Dateien" in r.error
    assert ids(store) == before


def test_missing_folder_fails_only_that_source(repo, tmp_path):
    cfg = RagSettings.model_validate({"sources": [
        {"collection": "gone", "title": "Gone", "path": str(tmp_path / "nope"), "includes": [{"dir": "src", "ext": [".php"]}]},
        {"collection": "bent_php", "title": "PHP", "path": str(repo), "includes": [{"dir": "src", "ext": [".php"]}]},
    ]})
    store = MemoryStore()
    job = asyncio.run(run(cfg, store))
    assert [r.state for r in job.status.sources] == ["failed", "done"] and job.status.state == "failed"
    assert "gone" not in store.data and len(ids(store)) == 3


def test_embedding_error_skips_the_file_but_keeps_its_old_point(repo):
    store = MemoryStore()
    asyncio.run(run(settings(repo), store))
    job = asyncio.run(run(settings(repo), store, ScriptedEmbedder({"gallery": EmbedError("Ollama-Fehler 500: boom")})))
    r = job.status.sources[0]
    assert r.state == "done" and r.skipped == 1 and r.removed == 0
    assert [(f.file, f.detail) for f in r.skipped_files] == [("src/B.php", "Ollama-Fehler 500: boom")]
    assert point_id("bent_php", "src/B.php") in ids(store)


def test_ollama_gone_stops_the_job_without_deleting(repo):
    cfg = RagSettings.model_validate({"sources": [
        {"collection": "bent_php", "title": "PHP", "path": str(repo), "includes": [{"dir": "src", "ext": [".php"]}]},
        {"collection": "second", "title": "Second", "path": str(repo), "includes": [{"dir": "src", "ext": [".php"]}]},
    ]})
    store = MemoryStore()
    asyncio.run(run(cfg, store))
    before = ids(store)
    job = asyncio.run(run(cfg, store, ScriptedEmbedder({"gallery": EmbedUnavailable("Ollama nicht erreichbar")})))
    assert job.status.state == "failed" and job.status.error == "Ollama nicht erreichbar"
    assert [r.state for r in job.status.sources] == ["failed", "cancelled"]
    assert ids(store) == before


def test_many_errors_in_a_row_stop_the_job(repo):
    for i in range(5):
        write(repo, f"src/F{i}.php", f"<?php broken {i}")
    job = asyncio.run(run(settings(repo, max_consecutive_errors=3), MemoryStore(),
                          ScriptedEmbedder({"broken": EmbedError("bad")})))
    assert job.status.state == "failed" and "3 Einbettungen nacheinander" in job.status.error


def test_wrong_vector_size_writes_nothing(repo):
    store = MemoryStore()
    asyncio.run(run(settings(repo), store))
    before = {k: v[1]["text"] for k, v in store.data["bent_php"].points.items()}
    job = asyncio.run(run(settings(repo), store, ScriptedEmbedder(dim=16)))
    r = job.status.sources[0]
    assert r.state == "failed" and "Vektorgröße passt nicht" in r.error and "256" in r.error
    assert {k: v[1]["text"] for k, v in store.data["bent_php"].points.items()} == before


# ---- cancel ---------------------------------------------------------------------------------------

def test_cancel_keeps_finished_work_and_deletes_nothing(repo):
    store = MemoryStore()
    asyncio.run(run(settings(repo), store))
    asyncio.run(store.upsert("bent_php", [Point(99, [1.0] + [0.0] * 255, {"filename": "old", "text": "old"})]))
    cfg = settings(repo)
    job = Job("c", cfg.sources, "fake", cfg.max_chars)

    async def cancel_after_first(n):
        if n == 1:
            job.status.cancel_requested = True

    asyncio.run(run(cfg, store, ScriptedEmbedder(hook=cancel_after_first), job=job))
    r = job.status.sources[0]
    assert job.status.state == "cancelled" and r.state == "cancelled"
    assert r.indexed == 1 and r.removed == 0 and 99 in ids(store)    # the stale point survives a cancel


def test_unreadable_file_keeps_its_old_point(repo, monkeypatch):
    store = MemoryStore()
    asyncio.run(run(settings(repo), store))
    real = jobs_module.read_text

    def locked(path, max_chars):
        if path.name == "B.php":
            raise PermissionError(13, "Permission denied")      # e.g. opened exclusively by an editor on Windows
        return real(path, max_chars)

    monkeypatch.setattr(jobs_module, "read_text", locked)
    job = asyncio.run(run(settings(repo), store))
    r = job.status.sources[0]
    assert r.state == "done" and r.skipped == 1 and "nicht lesbar" in r.skipped_files[0].detail
    assert point_id("bent_php", "src/B.php") in ids(store) and r.removed == 0


def test_secret_file_names_are_never_opened(repo, monkeypatch):
    write(repo, "src/secrets.php", "<?php harmless")
    opened = []
    real = jobs_module.read_text
    monkeypatch.setattr(jobs_module, "read_text", lambda path, n: opened.append(path.name) or real(path, n))
    asyncio.run(run(settings(repo), MemoryStore()))
    assert "secrets.php" not in opened and "A.php" in opened
