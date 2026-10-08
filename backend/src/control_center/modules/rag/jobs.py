"""Reindex job: scan -> filter -> embed -> upsert -> clean up, one source after the other.

Point IDs are stable: uuid5(collection + relative path). Running the job twice therefore overwrites
the same points instead of adding new ones, and the cleanup at the end can remove exactly the points
whose files are gone - or are now caught by the secret filter. The integer IDs (0, 1, 2 ...) that the
old scripts wrote are never "kept", so the first run here removes them as well.

Cleanup rules (the dangerous part - deleting is the only irreversible step):
- only after a COMPLETE pass over the source; a cancelled or failed source deletes nothing
- not when the scan found no files at all (wrong path, unplugged drive): that is an error, not "all gone"
- a file that exists but could not be read or embedded keeps its old point (fate unknown)
- a source owns its collection (settings enforce one source per collection)
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from control_center.modules.rag import secret_filter
from control_center.modules.rag.embedder import Embedder, EmbedError, EmbedUnavailable
from control_center.modules.rag.scanner import Candidate, SourceError, read_text, scan
from control_center.modules.rag.schemas import (
    FileNote,
    JobProgress,
    JobStatus,
    SecretNote,
    SourceReport,
)
from control_center.modules.rag.settings import RagSettings, SourceConfig
from control_center.modules.rag.store import (
    CollectionMissing,
    Point,
    StoreError,
    StoreUnavailable,
    VectorStore,
)

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "ai-control-center/rag")


def point_id(collection: str, rel: str) -> str:
    """Same file, same collection -> same ID, on every machine and in every run."""
    return str(uuid.uuid5(NAMESPACE, f"{collection}/{rel}"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobAborted(Exception):
    """Nothing can continue (Ollama or Qdrant gone, model missing). German message."""


class Job:
    """A job and its live report. Mutated only on the event loop."""

    def __init__(self, job_id: str, sources: list[SourceConfig], model: str, max_chars: int) -> None:
        self.sources = sources
        self.status = JobStatus(
            id=job_id, revision=0, as_of=_now(), state="queued", created_at=_now(), model=model, max_chars=max_chars,
            sources=[SourceReport(collection=s.collection, title=s.title) for s in sources],
        )
        self.task: asyncio.Task | None = None

    @property
    def cancel_requested(self) -> bool:
        return self.status.cancel_requested


class Indexer:
    def __init__(self, cfg: RagSettings, base: Path, store: VectorStore, embedder: Embedder,
                 log: logging.Logger, publish: Callable[[Job], None]) -> None:
        self.cfg = cfg
        self.base = base                        # relative source paths: next to control-center.toml
        self.store = store
        self.embedder = embedder
        self.log = log
        self.publish = publish
        self._last_progress = 0.0

    async def run(self, job: Job) -> None:
        st = job.status
        st.state, st.started_at = "running", _now()
        self.log.info("reindex %s started: %s (model %s, store %s)", st.id,
                      ", ".join(s.collection for s in job.sources), self.embedder.model, self.store.mode)
        self.publish(job)
        try:
            for src, report in zip(job.sources, st.sources):
                if job.cancel_requested:
                    report.state = "cancelled"
                    continue
                try:
                    await self._source(job, src, report)
                except SourceError as exc:
                    self._fail(report, str(exc))
                except JobAborted as exc:
                    self._fail(report, str(exc))
                    st.error = str(exc)
                    break
        except asyncio.CancelledError:
            st.error = st.error or "Abgebrochen, weil das Control Center beendet wurde"
            st.cancel_requested = True
            raise
        finally:
            for report in st.sources:
                if report.state in ("pending", "running"):
                    report.state = "cancelled"
                    report.finished_at = report.finished_at or _now()
            st.current, st.finished_at = None, _now()
            if st.error and not st.cancel_requested:
                st.state = "failed"
            elif any(r.state == "cancelled" for r in st.sources):
                st.state = "cancelled"
            elif any(r.state == "failed" for r in st.sources):
                st.state = "failed"
            else:
                st.state = "done"
            self.log.log(logging.INFO if st.state == "done" else logging.WARNING,
                         "reindex %s finished: %s%s", st.id, st.state, f" - {st.error}" if st.error else "")

    def _fail(self, report: SourceReport, message: str) -> None:
        report.state, report.error, report.finished_at = "failed", message, _now()
        if report.started_at:
            report.duration_s = round((report.finished_at - report.started_at).total_seconds(), 2)
        self.log.error("source %s failed: %s", report.collection, message)

    # ---- one source -----------------------------------------------------------------------------

    async def _source(self, job: Job, src: SourceConfig, report: SourceReport) -> None:
        st = job.status
        started = time.monotonic()
        report.state, report.started_at = "running", _now()
        st.current = JobProgress(collection=src.collection, phase="scan")
        self.publish(job)

        result = await asyncio.to_thread(scan, src, self.base)
        report.root, report.missing_dirs, report.files = str(result.root), result.missing_dirs, len(result.files)
        for missing in result.missing_dirs:
            self.log.warning("source %s: include dir not found: %s", src.collection, missing)
        if not result.files:
            raise SourceError("Keine passenden Dateien gefunden – die Collection bleibt unverändert "
                              "(Ordner, Endungen und Ausschlüsse prüfen)")

        size = await self._vector_size(src.collection)          # None = collection does not exist yet
        keep: set[str] = set()                                  # point IDs that stay after the cleanup
        batch: list[Point] = []
        errors_in_row = 0
        embed_time = 0.0
        st.current = JobProgress(collection=src.collection, phase="embed", total=len(result.files))
        self.publish(job)

        try:
            for cand in result.files:
                if job.cancel_requested:
                    break
                st.current.file = cand.rel
                pid = point_id(src.collection, cand.rel)
                outcome = await self._prepare(src, cand, report)
                if outcome == "keep":
                    keep.add(pid)
                elif isinstance(outcome, tuple):
                    text, truncated = outcome
                    t0 = time.monotonic()
                    try:
                        vector = await self.embedder.embed(text)
                        errors_in_row = 0
                    except EmbedUnavailable as exc:
                        raise JobAborted(str(exc)) from exc
                    except EmbedError as exc:
                        errors_in_row += 1
                        keep.add(pid)                           # failed now, maybe fine before: keep it
                        report.skipped += 1
                        self._note(report, report.skipped_files, FileNote(file=cand.rel, detail=str(exc)))
                        self.log.warning("source %s: %s skipped: %s", src.collection, cand.rel, exc)
                        if errors_in_row >= self.cfg.max_consecutive_errors:
                            raise JobAborted(f"{errors_in_row} Einbettungen nacheinander fehlgeschlagen, "
                                             f"zuletzt: {exc}") from exc
                        vector = None
                    finally:
                        embed_time += time.monotonic() - t0
                    if vector is not None:
                        if size is None:
                            await self.store.create(src.collection, len(vector), self.cfg.distance)
                            size, report.created = len(vector), True
                            self.log.info("collection %s created (%d dimensions, %s)",
                                          src.collection, size, self.cfg.distance)
                        elif len(vector) != size:
                            raise SourceError(
                                f"Vektorgröße passt nicht: Collection „{src.collection}“ hat {size} Dimensionen, "
                                f"{self.embedder.model} liefert {len(vector)}. Entweder das passende Modell "
                                f"eintragen oder die Collection löschen und neu indexieren.")
                        batch.append(Point(pid, vector, {
                            "filename": cand.rel, "text": text,         # what mcp_server.py reads
                            "truncated": truncated, "bytes": cand.size, "indexed_at": _now().isoformat(),
                        }))
                        keep.add(pid)
                        if len(batch) >= self.cfg.batch_size:
                            await self._flush(src.collection, batch, report)
                report.done += 1
                st.current.done = report.done
                self._progress(job)
            await self._flush(src.collection, batch, report)    # also after a cancel: finished work stays
        except StoreUnavailable as exc:
            raise JobAborted(str(exc)) from exc
        except CollectionMissing as exc:
            raise SourceError(f"Collection ist während des Laufs verschwunden: {exc}") from exc
        except StoreError as exc:
            raise SourceError(str(exc)) from exc
        finally:
            report.embed_s = round(embed_time, 2)

        if job.cancel_requested:
            report.state, report.finished_at = "cancelled", _now()
            report.duration_s = round(time.monotonic() - started, 2)
            self.log.warning("source %s cancelled after %d of %d files - nothing deleted",
                             src.collection, report.done, report.files)
            return

        st.current = JobProgress(collection=src.collection, phase="cleanup", done=report.done, total=report.files)
        self.publish(job)
        if size is not None:                                    # nothing to clean in a collection that does not exist
            try:
                stale = [i for i in await self.store.point_ids(src.collection) if str(i) not in keep]
                await self.store.delete_points(src.collection, stale)
            except StoreUnavailable as exc:
                raise JobAborted(str(exc)) from exc
            except StoreError as exc:
                raise SourceError(f"Aufräumen fehlgeschlagen: {exc}") from exc
            report.removed = len(stale)
        report.state, report.finished_at = "done", _now()
        report.duration_s = round(time.monotonic() - started, 2)
        self.log.info("source %s done in %.1f s: %d files, %d indexed, %d truncated, %d empty, %d secret, "
                      "%d skipped, %d removed", src.collection, report.duration_s, report.files, report.indexed,
                      report.truncated, report.empty, report.secret, report.skipped, report.removed)

    async def _prepare(self, src: SourceConfig, cand: Candidate, report: SourceReport) -> str | tuple[str, bool]:
        """Secret filter, read, cut. Returns (text, truncated) to embed, 'keep' (old point stays) or 'drop'."""
        name_rule = secret_filter.name_hit(cand.rel.rsplit("/", 1)[-1])
        allowed = secret_filter.allowed(cand.rel, src.secret_allow)
        if name_rule and not allowed:                           # no need to read the file at all
            self._secret(src, report, cand.rel, [secret_filter.SecretHit(None, name_rule)], allowed=False)
            return "drop"
        try:
            ft = await asyncio.to_thread(read_text, cand.path, self.cfg.max_chars)
        except OSError as exc:
            report.skipped += 1
            self._note(report, report.skipped_files, FileNote(file=cand.rel, detail=f"nicht lesbar: {exc}"))
            self.log.warning("source %s: %s not readable: %s", src.collection, cand.rel, exc)
            return "keep"
        hits = ([secret_filter.SecretHit(None, name_rule)] if name_rule else []) + secret_filter.content_hits(ft.text)
        if hits:
            self._secret(src, report, cand.rel, hits, allowed)
            if not allowed:
                return "drop"                                   # its old point (with the secret) goes in the cleanup
        if not ft.text.strip():
            report.empty += 1
            return "drop"
        if ft.truncated:
            report.truncated += 1
            self._note(report, report.truncated_files, FileNote(file=cand.rel, detail=f"{cand.size} Bytes"))
        return ft.text, ft.truncated

    def _secret(self, src: SourceConfig, report: SourceReport, rel: str,
                hits: list[secret_filter.SecretHit], allowed: bool) -> None:
        if not allowed:
            report.secret += 1
        for h in hits:
            self._note(report, report.secret_hits, SecretNote(file=rel, line=h.line, rule=h.rule, allowed=allowed))
        where = ", ".join(f"{h.rule}" + (f" (line {h.line})" if h.line else "") for h in hits[:5])
        self.log.warning("source %s: secret filter %s %s: %s", src.collection,
                         "allowed" if allowed else "skipped", rel, where)

    def _note(self, report: SourceReport, target: list, note) -> None:
        if len(target) < self.cfg.list_limit:
            target.append(note)
        else:
            report.lists_cut = True

    async def _vector_size(self, collection: str) -> int | None:
        try:
            stats = await self.store.stats(collection)
        except CollectionMissing:
            return None
        except StoreUnavailable as exc:
            raise JobAborted(str(exc)) from exc
        except StoreError as exc:
            raise SourceError(str(exc)) from exc
        if stats.vector_size is None:
            raise SourceError(f"Collection „{collection}“ hat benannte Vektoren oder ein unbekanntes Format – "
                              f"so legt sie das Control Center nicht an. Löschen oder eine andere Collection eintragen.")
        return stats.vector_size

    async def _flush(self, collection: str, batch: list[Point], report: SourceReport) -> None:
        if batch:
            await self.store.upsert(collection, batch)
            report.indexed += len(batch)
            batch.clear()

    def _progress(self, job: Job) -> None:
        now = time.monotonic()
        if now - self._last_progress >= self.cfg.progress_interval_s:
            self._last_progress = now
            self.publish(job)
