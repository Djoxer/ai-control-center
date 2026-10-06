"""Ollama REST API: what is loaded right now (/api/ps) and which version runs (/api/version).

HttpOllama talks to a real Ollama (local, or the AI box via its LAN IP).
FakeOllama replays raw responses recorded with ``python -m control_center.capture_samples``.
Both share parse_ps(), so a recorded file is parsed exactly like a live answer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import TypeAdapter, ValidationError

from control_center.adapters.common import describe

_datetime = TypeAdapter(datetime)     # parses Go's nanosecond timestamps (keeps microseconds)
PINNED_AFTER = timedelta(days=365 * 20)   # keep_alive -1 -> Ollama reports an expiry centuries ahead


class OllamaUnavailable(Exception):
    """No usable answer: Ollama down, wrong address, timeout, or a response we cannot read."""


@dataclass(frozen=True)
class RunningModel:
    name: str                       # "qwen3.5-9b-64k-code:latest"
    digest: str                     # sha256 hex of the manifest: stable identity, names can be re-tagged
    size: int                       # bytes the loaded model occupies in total (VRAM + system RAM)
    size_vram: int                  # part of it in VRAM; size_vram < size = partly running on the CPU
    context_length: int | None      # num_ctx the runner was started with; missing in older versions
    expires_at: datetime | None     # planned unload time; None = unknown
    family: str | None              # "qwen3"
    parameter_size: str | None      # "9.0B"
    quantization: str | None        # "Q4_K_M"


class OllamaAdapter(Protocol):
    simulated: bool

    async def running(self) -> list[RunningModel]: ...

    async def version(self) -> str: ...


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _time_or_none(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        ts = _datetime.validate_python(value)
    except ValidationError:
        return None
    if ts.year <= 1:                # Go zero time "0001-01-01T00:00:00Z" = not set
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def parse_ps(payload: Any) -> list[RunningModel]:
    """Raw /api/ps JSON -> RunningModel list. Unknown fields are ignored, missing ones become None."""
    if not isinstance(payload, dict):
        raise OllamaUnavailable("unexpected /api/ps response (not an object)")
    items = payload.get("models") or []          # "models": null is treated like an empty list
    if not isinstance(items, list):
        raise OllamaUnavailable("unexpected /api/ps response (models is not a list)")
    out: list[RunningModel] = []
    for m in items:
        if not isinstance(m, dict):
            continue
        details = m.get("details") or {}
        out.append(RunningModel(
            name=str(m.get("name") or m.get("model") or "?"),
            digest=str(m.get("digest") or ""),
            size=_int_or_none(m.get("size")) or 0,
            size_vram=_int_or_none(m.get("size_vram")) or 0,
            context_length=_int_or_none(m.get("context_length")),
            expires_at=_time_or_none(m.get("expires_at")),
            family=details.get("family") or None,
            parameter_size=details.get("parameter_size") or None,
            quantization=details.get("quantization_level") or None,
        ))
    return out


class HttpOllama:
    simulated = False

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client                    # shared, owned by the registry
        self._base = base_url.rstrip("/")

    async def _get(self, path: str) -> Any:
        try:
            r = await self._client.get(self._base + path)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:          # ValueError = body is not JSON
            raise OllamaUnavailable(f"{path}: {describe(exc)}") from exc

    async def running(self) -> list[RunningModel]:
        return parse_ps(await self._get("/api/ps"))

    async def version(self) -> str:
        data = await self._get("/api/version")
        if not isinstance(data, dict) or not data.get("version"):
            raise OllamaUnavailable("/api/version: no version field")
        return str(data["version"])


class FakeOllama:
    """Replays ollama-ps.json / ollama-version.json of a scenario folder. Missing file = Ollama down.

    Expiry times are shifted by (now - capturedAt from meta.json), so a model recorded with
    "unloads in 4 minutes" still shows 4 minutes today instead of "unloading since last week".
    Files are re-read on every call: edit them while the server runs to try out a situation.
    """
    simulated = True

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def _load(self, name: str) -> Any:
        path = self.folder / name
        if not path.is_file():
            raise OllamaUnavailable(f"fake: {name} missing in scenario '{self.folder.name}'")
        return json.loads(path.read_text(encoding="utf-8"))

    def _captured_at(self) -> datetime | None:
        meta = self.folder / "meta.json"
        if not meta.is_file():
            return None
        return _time_or_none(json.loads(meta.read_text(encoding="utf-8")).get("capturedAt"))

    async def running(self) -> list[RunningModel]:
        models = parse_ps(self._load("ollama-ps.json"))
        captured = self._captured_at()
        if captured is None:
            return models
        shift = datetime.now(timezone.utc) - captured
        return [
            m if m.expires_at is None or m.expires_at - captured > PINNED_AFTER
            else replace(m, expires_at=m.expires_at + shift)
            for m in models
        ]

    async def version(self) -> str:
        return str(self._load("ollama-version.json")["version"])
