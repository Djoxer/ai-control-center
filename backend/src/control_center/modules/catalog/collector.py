"""What the catalog keeps about one installed model, and what it reads about Ollama's server defaults.

A ModelRecord is the merged answer of /api/tags (name, digest, size) and /api/show (parameters,
parent, weights blob, GGUF metadata). It is stored as JSON in SQLite, so a new field needs no
migration - an older row simply lacks it and gets the default.

ServerDefaults come from the "server config" line Ollama writes to server.log when it starts:
the values a model falls back to when it has no own num_ctx, and how the KV cache is stored.
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from control_center.adapters.ollama import InstalledModel, ModelDetails
from control_center.core.tail import resolve_files

# GGUF metadata worth keeping: scalars and short number lists (per-layer head counts).
# Token tables are null already (/api/show without verbose), long lists would only bloat the row.
MAX_INFO_LIST = 512
LOG_HEAD_BYTES = 1024 * 1024           # the "server config" line is written right after the start
LOG_FILES_TRIED = 2                    # newest server.log, then the one rotated before it


@dataclass
class ModelRecord:
    name: str
    digest: str
    size: int                                   # bytes on disk = weights (+ projector)
    modified_at: str | None = None              # ISO time from Ollama
    family: str | None = None
    families: list[str] = field(default_factory=list)
    parameter_size: str | None = None
    quantization: str | None = None
    format: str | None = None
    parent_model: str | None = None             # as Ollama reports it (may not be installed any more)
    weights_digest: str | None = None           # sha256 of the weights blob, for the lineage fallback
    parameters: dict[str, list[str]] = field(default_factory=dict)
    system_chars: int = 0
    system_hash: str | None = None
    template_hash: str | None = None
    capabilities: list[str] = field(default_factory=list)
    model_info: dict[str, Any] = field(default_factory=dict)
    show_error: str | None = None               # /api/show failed: tags data only, retried next refresh
    collected_at: str | None = None             # ISO time of the last /api/show

    @property
    def architecture(self) -> str | None:
        arch = self.model_info.get("general.architecture")
        return arch if isinstance(arch, str) and arch else None

    def info(self, suffix: str) -> Any:
        """GGUF value "<arch>.<suffix>", e.g. info("block_count") -> qwen2.block_count."""
        arch = self.architecture
        return self.model_info.get(f"{arch}.{suffix}") if arch else None

    def param(self, key: str) -> str | None:
        values = self.parameters.get(key)
        return values[0] if values else None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ModelRecord":
        """Tolerant: unknown keys (written by a newer version) are dropped, missing ones get defaults."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def keep_info(info: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in info.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            out[key] = value
        elif isinstance(value, list) and len(value) <= MAX_INFO_LIST and all(
                isinstance(v, (int, float)) for v in value):
            out[key] = value                      # numbers and flags per layer (bool lists: e.g. window patterns)
    return out


def record_from(tag: InstalledModel, details: ModelDetails | None, now: datetime,
                show_error: str | None = None) -> ModelRecord:
    """tags + show -> record. Without show (it failed) the record keeps what /api/tags knows."""
    rec = ModelRecord(
        name=tag.name, digest=tag.digest, size=tag.size, modified_at=_iso(tag.modified_at), family=tag.family,
        families=list(tag.families), parameter_size=tag.parameter_size, quantization=tag.quantization,
        format=tag.format, parent_model=tag.parent_model, show_error=show_error,
    )
    if details is None:
        return rec
    rec.parent_model = details.parent_model or tag.parent_model
    rec.family = details.family or tag.family
    rec.weights_digest = details.weights_digest
    rec.parameters = {k: list(v) for k, v in details.parameters.items()}
    rec.system_chars = details.system_chars
    rec.system_hash = details.system_hash
    rec.template_hash = details.template_hash
    rec.capabilities = list(details.capabilities)
    rec.model_info = keep_info(details.model_info)
    rec.collected_at = now.isoformat()
    return rec


# ---- Ollama server defaults ------------------------------------------------------------------

_CONFIG_LINE = 'msg="server config"'
# value ends at a space, or at the "]" / quote that close the map when it is the last entry
_ENV_PAIR = re.compile(r"\b(OLLAMA_[A-Z_]+):([^\s\]\"]*)")


@dataclass(frozen=True)
class ServerDefaults:
    """Values Ollama applies to every model without its own setting. None = not known."""
    context_length: int | None = None
    kv_cache_type: str | None = None            # "f16" when the variable is empty
    flash_attention: bool | None = None
    num_parallel: int | None = None
    max_loaded_models: int | None = None
    keep_alive: str | None = None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _bool(value: str | None) -> bool | None:
    v = (value or "").strip().lower()
    if v in ("true", "1"):
        return True
    if v in ("false", "0"):
        return False
    return None


def parse_server_config(line: str) -> ServerDefaults:
    """One 'msg="server config" env="map[...]"' line -> ServerDefaults. Paths in it are never kept."""
    env = dict(_ENV_PAIR.findall(line))
    kv = env.get("OLLAMA_KV_CACHE_TYPE")
    parallel = _int(env.get("OLLAMA_NUM_PARALLEL"))
    return ServerDefaults(
        context_length=_int(env.get("OLLAMA_CONTEXT_LENGTH")) or None,     # 0 = Ollama's own default
        kv_cache_type=(kv.lower() if kv else "f16") if "OLLAMA_KV_CACHE_TYPE" in env else None,
        flash_attention=_bool(env.get("OLLAMA_FLASH_ATTENTION")),
        num_parallel=(parallel or 1) if parallel is not None else None,      # 0 = automatic, which is 1 today
        max_loaded_models=_int(env.get("OLLAMA_MAX_LOADED_MODELS")),
        keep_alive=env.get("OLLAMA_KEEP_ALIVE") or None,
    )


def read_server_config(patterns: list[str]) -> tuple[ServerDefaults, str] | None:
    """Newest "server config" line from the first MB of the newest log files. Blocking: call from a thread.

    Returns (values, file name) or None. Ollama writes the line once per start; the app starts a
    fresh server.log then, so the line sits at the top of the newest file.
    """
    for path in resolve_files(patterns)[:LOG_FILES_TRIED]:
        try:
            with Path(path).open("rb") as f:
                head = f.read(LOG_HEAD_BYTES).decode("utf-8", errors="replace")
        except OSError:
            continue
        found = [ln for ln in head.splitlines() if _CONFIG_LINE in ln]
        if found:
            return parse_server_config(found[-1]), os.path.basename(path)
    return None
