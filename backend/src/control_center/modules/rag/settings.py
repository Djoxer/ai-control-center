"""RAG module settings: [modules.rag] in control-center.toml.

One source = one repository folder = one Qdrant collection. The collection name is the identity of a
source everywhere (API, job report, point IDs) - so two sources can never clean up each other's points.

Behaviour on purpose identical to the old index scripts (index_bent.py, index_typo3.py): one file =
one point, text cut after max_chars characters, payload "filename" + "text". Better search quality
(chunking, prefixes, incremental runs) is a separate, measured step - see HELP.md.
"""
from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

# Qdrant accepts more, but the name travels in URLs (ours and Qdrant's) - keep it boring
COLLECTION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"

Distance = Literal["Cosine", "Dot", "Euclid", "Manhattan"]


class IncludeRule(BaseModel):
    """One sub-folder of the repository and the file endings taken from it (like the scripts' includes)."""
    dir: str = Field(min_length=1)          # relative to the source path, "/" or "\" both fine
    ext: list[str] = Field(min_length=1)    # endings incl. dot, case-sensitive like str.endswith in the scripts

    @field_validator("dir")
    @classmethod
    def _relative(cls, value: str) -> str:
        norm = value.replace("\\", "/").strip("/")
        if not norm or norm.startswith("..") or "/../" in f"/{norm}/" or ":" in norm:
            raise ValueError(f"dir must be a folder inside the repository, e.g. 'src/app', got {value!r}")
        return norm

    @field_validator("ext")
    @classmethod
    def _dotted(cls, value: list[str]) -> list[str]:
        bad = [e for e in value if not e.startswith(".") or len(e) < 2]
        if bad:
            raise ValueError(f"file endings start with a dot, e.g. '.php', got {bad}")
        return value


class SourceConfig(BaseModel):
    collection: str = Field(pattern=COLLECTION_PATTERN, max_length=64)
    title: str
    # repository folder; %VARS%, $VARS and ~ expand, relative = next to control-center.toml.
    # Lives OUTSIDE the git repo of the control center (customer code never goes to GitHub).
    path: str = Field(min_length=1)
    includes: list[IncludeRule] = Field(min_length=1)
    exclude_names: list[str] = Field(default_factory=lambda: [".env", ".htpasswd"])   # exact file names
    exclude_dirs: list[str] = Field(default_factory=lambda: ["vendor", "node_modules", ".git", "var"])
    # files the secret filter flagged but someone checked: globs on the relative path, e.g. "config/demo.php"
    secret_allow: list[str] = Field(default_factory=list)


class RagSettings(BaseModel):
    sources: list[SourceConfig] = Field(default_factory=list)

    # Vector store. "memory" = in-process store for the second PC (lost on restart, nothing to install).
    store: Literal["qdrant", "memory"] = "qdrant"
    qdrant_url: str = "http://{ai_host}:6333"          # {ai_host} from [adapters]
    qdrant_api_key: SecretStr | None = None            # only if Qdrant gets an API key one day
    qdrant_timeout_s: float = Field(30, gt=0, le=300)
    # Writes (reindex, delete) against a Qdrant on ANOTHER machine are refused unless this is true -
    # a test run on the second PC must never overwrite the real collections on the AI box.
    allow_remote_writes: bool = False

    # Embeddings. Same call as mcp_server.py, otherwise query and documents would not match.
    embedder: Literal["ollama", "fake"] = "ollama"     # fake = deterministic word hashing, no Ollama needed
    ollama_url: str | None = None                      # None = [adapters] ollama_url
    embedding_model: str = Field("nomic-embed-text", min_length=1)
    embed_timeout_s: float = Field(120, gt=0, le=600)  # first call loads the model
    fake_delay_s: float = Field(0.0, ge=0, le=5)       # embedder = "fake" only: makes progress visible

    distance: Distance = "Cosine"                      # only used when a collection is created
    max_chars: int = Field(6000, ge=100, le=1_000_000) # cut after this many characters (as the scripts did)
    batch_size: int = Field(50, ge=1, le=1000)         # points per upsert
    max_consecutive_errors: int = Field(10, ge=1, le=1000)   # embedding errors in a row -> job aborts
    search_limit_max: int = Field(50, ge=1, le=500)
    keep_jobs: int = Field(20, ge=1, le=500)           # finished job reports kept in data/rag/jobs.json
    list_limit: int = Field(500, ge=10, le=100_000)    # file names per list in a report (counts stay exact)
    progress_interval_s: float = Field(0.5, ge=0, le=10)   # SSE progress at most this often

    @field_validator("qdrant_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        parts = urlsplit(value.replace("{ai_host}", "placeholder"))
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"qdrant_url must look like http://127.0.0.1:6333, got {value!r}")
        _ = parts.port                                  # raises for :99999
        return value.rstrip("/")

    @model_validator(mode="after")
    def _unique(self) -> "RagSettings":
        names = [s.collection for s in self.sources]
        if len(names) != len(set(names)):
            raise ValueError("collection names must be unique - one source per collection "
                             "(otherwise the cleanup of one source deletes the points of the other)")
        return self

    def source(self, collection: str) -> SourceConfig | None:
        return next((s for s in self.sources if s.collection == collection), None)
