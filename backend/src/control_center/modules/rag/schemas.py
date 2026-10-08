"""API shapes of the RAG module (camelCase in JSON via CamelModel)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from control_center.core.schemas import CamelModel

# queued    = accepted, not started yet (a moment at most)
# running   = working through its sources
# done      = every source finished (individual files may still have been skipped)
# failed    = at least one source could not be indexed, or the job stopped (Ollama/Qdrant gone)
# cancelled = stopped by a user or by the shutdown of the control center
JobState = Literal["queued", "running", "done", "failed", "cancelled"]
SourceState = Literal["pending", "running", "done", "failed", "cancelled"]
Phase = Literal["scan", "embed", "cleanup"]


# ---- overview -------------------------------------------------------------------------------------

class StoreInfo(CamelModel):
    mode: Literal["qdrant", "memory"]
    url: str | None
    reachable: bool
    version: str | None = None
    error: str | None = None
    local: bool                             # runs on this machine (writes allowed by default)
    port: int | None = None                 # for the link to Qdrant's own web UI (:6333/dashboard)


class EmbedderInfo(CamelModel):
    mode: Literal["ollama", "fake"]
    url: str | None
    model: str


class WriteAccess(CamelModel):
    allowed: bool
    reason: str | None = None               # German: why reindex and delete are locked


class CollectionInfo(CamelModel):
    name: str
    status: str | None = None               # green | yellow | grey | red
    points: int | None = None
    indexed_vectors: int | None = None
    segments: int | None = None
    vector_size: int | None = None
    distance: str | None = None
    source: str | None = None               # title of the configured source writing it; None = not managed here


class IncludeInfo(CamelModel):
    dir: str
    ext: list[str]


class SourceInfo(CamelModel):
    collection: str
    title: str
    path: str                               # resolved, as the job will see it
    path_exists: bool
    includes: list[IncludeInfo]
    exclude_names: list[str]
    exclude_dirs: list[str]
    secret_allow: list[str]
    stats: CollectionInfo | None = None     # None = collection does not exist (yet), or Qdrant unreachable
    last_run: SourceReport | None = None    # newest finished report of this source


class RagOverview(CamelModel):
    as_of: datetime
    store: StoreInfo
    embedder: EmbedderInfo
    writes: WriteAccess
    max_chars: int
    sources: list[SourceInfo]
    collections: list[CollectionInfo]       # everything in the store, managed or not
    job: JobStatus | None = None            # running job, else the newest finished one


# ---- jobs -----------------------------------------------------------------------------------------

class FileNote(CamelModel):
    file: str                               # relative path, as stored in the payload
    detail: str | None = None               # reason, rule, or size


class SecretNote(CamelModel):
    file: str
    line: int | None = None                 # None = the file name is the hit
    rule: str
    allowed: bool = False                   # listed in secret_allow: indexed anyway


class SourceReport(CamelModel):
    collection: str
    title: str
    state: SourceState = "pending"
    error: str | None = None                # German, when state = failed
    root: str | None = None
    missing_dirs: list[str] = []            # include dirs that do not exist
    files: int = 0                          # matching files found by the scan
    done: int = 0                           # files handled so far (indexed or not)
    indexed: int = 0                        # points written
    truncated: int = 0                      # cut after max_chars - the number the quality step wants down
    empty: int = 0
    secret: int = 0                         # skipped by the secret filter
    skipped: int = 0                        # read or embedding errors - their old point stays
    removed: int = 0                        # points of files that are gone (or now filtered) deleted
    created: bool = False                   # collection was created by this run
    truncated_files: list[FileNote] = []    # detail = size in bytes
    secret_hits: list[SecretNote] = []
    skipped_files: list[FileNote] = []      # detail = reason
    lists_cut: bool = False                 # a list above hit list_limit; the counts are still exact
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_s: float | None = None
    embed_s: float | None = None            # time spent waiting for Ollama


class JobProgress(CamelModel):
    collection: str
    phase: Phase
    file: str | None = None
    done: int = 0
    total: int = 0


class JobStatus(CamelModel):
    id: str
    revision: int                           # +1 per change: the UI keeps the newest of HTTP answer and SSE
    as_of: datetime
    state: JobState
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested: bool = False
    current: JobProgress | None = None
    sources: list[SourceReport]
    error: str | None = None                # German: why the whole job stopped
    model: str                              # embedding model used
    max_chars: int


class JobRequest(CamelModel):
    collections: list[str] | None = None    # None = every configured source


# ---- search ---------------------------------------------------------------------------------------

class SearchRequest(CamelModel):
    collection: str
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(8, ge=1)             # upper bound: [modules.rag] search_limit_max


class SearchHit(CamelModel):
    id: str
    score: float
    filename: str | None = None
    text: str | None = None
    truncated: bool | None = None           # only for points written by the control center


class SearchResult(CamelModel):
    collection: str
    query: str
    model: str
    embed_ms: float
    search_ms: float
    hits: list[SearchHit]


SourceInfo.model_rebuild()
RagOverview.model_rebuild()
