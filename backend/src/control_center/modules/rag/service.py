"""RAG service: overview of sources and collections, reindex jobs, test search, deleting a collection.

One job at a time: Ollama runs with NUM_PARALLEL=1 on the AI box, a second job would only queue up
behind the first one there. Finished job reports go to data/rag/jobs.json (survive a restart: the
last run per source stays visible, and the numbers are the baseline for later quality work).
"""
from __future__ import annotations

import asyncio
import glob
import ipaddress
import json
import logging
import os
import socket
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from urllib.parse import urlsplit

import psutil
from pydantic import ValidationError

from control_center.core.config import config_path
from control_center.core.context import AppContext, LogSource
from control_center.core.log_setup import JsonLineFormatter
from control_center.modules.rag.embedder import (
    Embedder,
    EmbedError,
    EmbedUnavailable,
    FakeEmbedder,
    OllamaEmbedder,
)
from control_center.modules.rag.jobs import Indexer, Job
from control_center.modules.rag.scanner import resolve_root
from control_center.modules.rag.schemas import (
    CollectionInfo,
    EmbedderInfo,
    IncludeInfo,
    JobStatus,
    RagOverview,
    SearchHit,
    SearchRequest,
    SearchResult,
    SourceInfo,
    SourceReport,
    StoreInfo,
    WriteAccess,
)
from control_center.modules.rag.settings import RagSettings
from control_center.modules.rag.store import (
    CollectionMissing,
    CollectionStats,
    MemoryStore,
    QdrantStore,
    StoreError,
    StoreUnavailable,
    VectorStore,
)

log = logging.getLogger("control_center.modules.rag")
JOB_LOGGER = "control_center.modules.rag.jobs"      # also lands in the main log (propagates)
TOPIC = "rag.job"                                   # SSE: full JobStatus on every change
LOG_SOURCE = "rag"


class Busy(Exception):
    """A reindex is running. German message."""


class Locked(Exception):
    """Writes are not allowed here (remote Qdrant). German message."""


class UnknownSource(KeyError):
    pass


class UnknownJob(KeyError):
    pass


class NoSources(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_local_host(host: str) -> bool:
    """True if the host is this machine: localhost, loopback, or one of its own interface addresses.

    Unknown names count as remote - the safe side, because "local" unlocks writing.
    """
    host = host.strip("[]").lower()
    if host == "localhost":
        return True
    try:
        addresses = {ipaddress.ip_address(host.split("%")[0])}
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return False
        addresses = {ipaddress.ip_address(str(i[4][0]).split("%")[0]) for i in infos}
    if not addresses:
        return False
    own: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for addrs in psutil.net_if_addrs().values():
        for a in addrs:
            if a.family in (socket.AF_INET, socket.AF_INET6):
                try:
                    own.add(ipaddress.ip_address(a.address.split("%")[0]))
                except ValueError:
                    pass
    return all(a.is_loopback or a in own for a in addresses)


class RagService:
    def __init__(self, ctx: AppContext, cfg: RagSettings, store: VectorStore | None = None,
                 embedder: Embedder | None = None) -> None:
        self.ctx = ctx
        self.cfg = cfg
        self.base = config_path().parent
        adapters = ctx.adapters
        if store is not None:
            self.store = store
        elif cfg.store == "memory":
            self.store = MemoryStore()
        else:
            key = cfg.qdrant_api_key.get_secret_value() if cfg.qdrant_api_key else None
            self.store = QdrantStore(adapters.http, adapters.cfg.expand(cfg.qdrant_url), cfg.qdrant_timeout_s, key)
        if embedder is not None:
            self.embedder = embedder
        elif cfg.embedder == "fake":
            self.embedder = FakeEmbedder(delay_s=cfg.fake_delay_s)
        else:
            url = adapters.cfg.expand(cfg.ollama_url or adapters.cfg.ollama_url)
            self.embedder = OllamaEmbedder(adapters.http, url, cfg.embedding_model, cfg.embed_timeout_s)
        self.jobs_file = ctx.settings.data_dir / "rag" / "jobs.json"
        self.log_file = ctx.settings.log_dir / "rag.log"
        self.finished: deque[JobStatus] = deque(maxlen=cfg.keep_jobs)   # oldest first
        self.current: Job | None = None
        self.local = True                       # replaced in start_up (name lookup may block)
        self.job_log = logging.getLogger(JOB_LOGGER)
        self._handler: RotatingFileHandler | None = None
        self.indexer = Indexer(cfg, self.base, self.store, self.embedder, self.job_log, self._publish)

    # ---- lifecycle ------------------------------------------------------------------------------

    async def start_up(self) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        for old in [h for h in self.job_log.handlers if getattr(h, "_acc_rag", False)]:
            self.job_log.removeHandler(old)                 # left over by an app that never shut down (tests)
            old.close()
        handler = RotatingFileHandler(self.log_file, maxBytes=5 * 1024 * 1024, backupCount=3,
                                      encoding="utf-8", delay=True)
        handler.setFormatter(JsonLineFormatter())
        handler._acc_rag = True                             # type: ignore[attr-defined]
        self.job_log.addHandler(handler)
        self._handler = handler
        if self.cfg.sources:                                # nothing configured -> no empty entry on the logs page
            pattern = os.path.join(glob.escape(str(self.log_file.parent)), glob.escape(self.log_file.name) + "*")
            self.ctx.log_sources[LOG_SOURCE] = LogSource(key=LOG_SOURCE, title="RAG-Indexierung", format="json",
                                                         paths=(pattern,))
        self.finished.extend(await asyncio.to_thread(self._load_jobs))
        if self.store.mode == "memory":
            self.local = True
        else:
            host = urlsplit(self.store.url or "").hostname or ""
            self.local = await asyncio.to_thread(is_local_host, host)
        if not self.local and not self.cfg.allow_remote_writes:
            log.info("rag: Qdrant at %s is on another machine - reindex and delete are locked here", self.store.url)

    async def shut_down(self) -> None:
        job = self.current
        if job is not None and job.task is not None:
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)
        self.ctx.log_sources.pop(LOG_SOURCE, None)
        if self._handler is not None:
            self.job_log.removeHandler(self._handler)
            self._handler.close()                           # Windows: an open file cannot be rotated or deleted
            self._handler = None

    # ---- access rules ---------------------------------------------------------------------------

    def write_access(self) -> WriteAccess:
        if self.local or self.cfg.allow_remote_writes:
            return WriteAccess(allowed=True)
        host = urlsplit(self.store.url or "").hostname
        return WriteAccess(allowed=False, reason=(
            f"Schreibschutz: Qdrant läuft auf einem anderen Rechner ({host}). Neu indexieren und Löschen sind "
            f"von hier aus gesperrt, damit ein Test nicht die echten Collections überschreibt. Freigeben mit "
            f"allow_remote_writes = true in [modules.rag]."))

    def _require_write(self) -> None:
        access = self.write_access()
        if not access.allowed:
            raise Locked(access.reason)

    # ---- overview -------------------------------------------------------------------------------

    async def overview(self) -> RagOverview:
        store_url = self.store.url
        port = urlsplit(store_url).port if store_url else None
        info = StoreInfo(mode=self.store.mode, url=store_url, reachable=False, local=self.local, port=port)
        stats: dict[str, CollectionStats | None] = {}
        try:
            info.version = await self.store.version()
            names = await self.store.collections()
            info.reachable = True
            results = await asyncio.gather(*(self.store.stats(n) for n in names), return_exceptions=True)
            stats = {n: (r if isinstance(r, CollectionStats) else None) for n, r in zip(names, results)}
        except (StoreUnavailable, StoreError) as exc:
            info.error = str(exc)
        titles = {s.collection: s.title for s in self.cfg.sources}
        collections = [self._collection_info(n, s, titles.get(n)) for n, s in stats.items()]
        exists = await asyncio.to_thread(lambda: [resolve_root(s, self.base).is_dir() for s in self.cfg.sources])
        sources = []
        for src, path_ok in zip(self.cfg.sources, exists):
            st = stats.get(src.collection)
            sources.append(SourceInfo(
                collection=src.collection, title=src.title, path=str(resolve_root(src, self.base)), path_exists=path_ok,
                includes=[IncludeInfo(dir=i.dir, ext=i.ext) for i in src.includes],
                exclude_names=src.exclude_names, exclude_dirs=src.exclude_dirs, secret_allow=src.secret_allow,
                stats=self._collection_info(src.collection, st, src.title) if st else None,
                last_run=self._last_run(src.collection),
            ))
        job = self.current.status if self.current else (self.finished[-1] if self.finished else None)
        return RagOverview(
            as_of=_now(), store=info,
            embedder=EmbedderInfo(mode=self.embedder.mode, url=self.embedder.url, model=self.embedder.model),
            writes=self.write_access(), max_chars=self.cfg.max_chars, sources=sources, collections=collections, job=job,
        )

    @staticmethod
    def _collection_info(name: str, s: CollectionStats | None, source: str | None) -> CollectionInfo:
        if s is None:
            return CollectionInfo(name=name, source=source)
        return CollectionInfo(name=name, status=s.status, points=s.points, indexed_vectors=s.indexed_vectors,
                              segments=s.segments, vector_size=s.vector_size, distance=s.distance, source=source)

    def _last_run(self, collection: str) -> SourceReport | None:
        for job in reversed(self.finished):
            for r in job.sources:
                if r.collection == collection and r.state != "pending":
                    return r
        return None

    # ---- jobs -----------------------------------------------------------------------------------

    def jobs(self) -> list[JobStatus]:
        """Newest first; a running job on top."""
        running = [self.current.status] if self.current else []
        return running + list(reversed(self.finished))

    def job(self, job_id: str) -> JobStatus:
        for j in self.jobs():
            if j.id == job_id:
                return j
        raise UnknownJob(job_id)

    async def start_job(self, collections: list[str] | None) -> JobStatus:
        self._require_write()
        if self.current is not None:
            raise Busy("Es läuft schon eine Indexierung – erst abwarten oder abbrechen.")
        if not self.cfg.sources:
            raise NoSources()
        wanted = collections if collections else [s.collection for s in self.cfg.sources]
        chosen = []
        for name in dict.fromkeys(wanted):                  # keep order, drop duplicates
            src = self.cfg.source(name)
            if src is None:
                raise UnknownSource(name)
            chosen.append(src)
        job = Job(uuid.uuid4().hex[:12], chosen, self.embedder.model, self.cfg.max_chars)
        self.current = job                                  # set before the task runs: a double click gets Busy
        job.task = asyncio.create_task(self._run(job), name=f"rag-job-{job.status.id}")
        self._publish(job)
        return job.status

    async def _run(self, job: Job) -> None:
        try:
            await self.indexer.run(job)
        except Exception:
            # a bug must not leave the job "running" forever; the indexer catches the expected errors itself
            log.exception("rag job %s crashed", job.status.id)
            job.status.state, job.status.error = "failed", "Interner Fehler – Details im Protokoll"
            job.status.finished_at = job.status.finished_at or _now()
        finally:
            self.current = None
            self.finished.append(job.status)
            try:
                await asyncio.to_thread(self._save_jobs)
            except OSError:
                log.exception("rag: cannot write %s", self.jobs_file)
            self._publish(job)

    def cancel_job(self, job_id: str) -> JobStatus:
        job = self.current
        if job is None or job.status.id != job_id:
            return self.job(job_id)                         # finished already: nothing to cancel (idempotent)
        if not job.status.cancel_requested:
            job.status.cancel_requested = True
            self.job_log.warning("reindex %s: cancel requested", job_id)
            self._publish(job)
        return job.status

    def _publish(self, job: Job) -> None:
        job.status.revision += 1
        job.status.as_of = _now()
        self.ctx.events.publish(TOPIC, job.status.model_dump(mode="json", by_alias=True))

    # ---- search and delete ----------------------------------------------------------------------

    async def search(self, req: SearchRequest) -> SearchResult:
        if req.limit > self.cfg.search_limit_max:
            raise ValueError(f"limit höchstens {self.cfg.search_limit_max}")
        await self.store.stats(req.collection)              # CollectionMissing -> 404 before Ollama is bothered
        t0 = time.monotonic()
        vector = await self.embedder.embed(req.query)
        t1 = time.monotonic()
        hits = await self.store.query(req.collection, vector, req.limit)
        t2 = time.monotonic()
        return SearchResult(
            collection=req.collection, query=req.query, model=self.embedder.model,
            embed_ms=round((t1 - t0) * 1000, 1), search_ms=round((t2 - t1) * 1000, 1),
            hits=[SearchHit(
                id=str(h.id), score=round(h.score, 4),
                filename=_str_or_none(h.payload.get("filename")), text=_str_or_none(h.payload.get("text")),
                truncated=h.payload.get("truncated") if isinstance(h.payload.get("truncated"), bool) else None,
            ) for h in hits],
        )

    async def drop_collection(self, name: str) -> None:
        self._require_write()
        if self.current is not None:
            raise Busy("Während einer Indexierung wird nichts gelöscht.")
        await self.store.stats(name)                        # CollectionMissing -> 404
        await self.store.drop(name)
        self.job_log.warning("collection %s deleted by user", name)

    # ---- persistence ----------------------------------------------------------------------------

    def _load_jobs(self) -> list[JobStatus]:
        try:
            raw = json.loads(self.jobs_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            log.exception("rag: %s unreadable - starting without job history", self.jobs_file)
            return []
        jobs = []
        for item in raw if isinstance(raw, list) else []:
            try:
                jobs.append(JobStatus.model_validate(item))
            except ValidationError:
                continue                                    # written by an older version: skip, keep the rest
        return jobs[-self.cfg.keep_jobs:]

    def _save_jobs(self) -> None:
        self.jobs_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.jobs_file.with_suffix(".tmp")
        data = [j.model_dump(mode="json") for j in self.finished]
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.jobs_file)                     # atomic: a crash never leaves half a file


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


__all__ = ["RagService", "Busy", "Locked", "UnknownSource", "UnknownJob", "NoSources", "is_local_host",
           "CollectionMissing", "StoreUnavailable", "StoreError", "EmbedUnavailable", "EmbedError"]
