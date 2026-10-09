"""Ollama REST API: what is loaded (/api/ps), which version runs (/api/version), what is installed
(/api/tags), what one installed model is made of (/api/show), and - for test runs - answers from
/api/generate and /api/chat (with tools).

HttpOllama talks to a real Ollama (local, or the AI box via its LAN IP).
FakeOllama replays raw responses recorded with ``python -m control_center.capture_samples``.
Both share the parse_* functions, so a recorded file is parsed exactly like a live answer.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
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


class OllamaModelMissing(Exception):
    """/api/show for a name Ollama does not know (deleted between /api/tags and /api/show, or a typo)."""


class OllamaRequestFailed(Exception):
    """Ollama answered, but with an error: model too big, runner crashed, bad options. Text from Ollama."""


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


@dataclass(frozen=True)
class InstalledModel:
    """One entry of /api/tags: a model on disk, loaded or not."""
    name: str                       # "qwen3.5-9b-64k-code:latest"
    digest: str                     # manifest digest; changes with every re-create or re-pull
    size: int                       # bytes on disk (weights + projector + small layers)
    modified_at: datetime | None
    family: str | None
    families: tuple[str, ...]
    parameter_size: str | None
    quantization: str | None
    format: str | None              # "gguf"
    parent_model: str | None        # set by "ollama create" when FROM names another model


# FROM line of a generated Modelfile that points at a blob: the weights file of the model.
# Windows: "FROM C:\Users\x\.ollama\models\blobs\sha256-<hex>", Linux: ".../blobs/sha256-<hex>".
_BLOB_FROM = re.compile(r"^FROM\s+.*?sha256[-:]([0-9a-f]{64})[\"']?\s*$", re.MULTILINE | re.IGNORECASE)


@dataclass(frozen=True)
class ModelDetails:
    """/api/show of one model, reduced to what the catalog needs. Big texts (license, template) stay out."""
    name: str
    parent_model: str | None
    family: str | None
    families: tuple[str, ...]
    parameter_size: str | None
    quantization: str | None
    format: str | None
    parameters: dict[str, tuple[str, ...]]     # "num_ctx" -> ("65536",); "stop" may repeat
    system_chars: int                          # length of the SYSTEM prompt, 0 = none (content stays out)
    template_hash: str | None                  # to tell "same template as the parent" without storing it
    system_hash: str | None
    weights_digest: str | None                 # sha256 of the weights blob (Modelfile FROM line)
    model_info: dict[str, Any] = field(default_factory=dict)   # GGUF metadata, big arrays already null
    capabilities: tuple[str, ...] = ()         # "completion", "tools", "vision", "embedding", "insert" ...
    modified_at: datetime | None = None


@dataclass(frozen=True)
class GenerateResult:
    """Timings of one /api/generate call (stream off). Durations in seconds, from Ollama's own clocks."""
    total_s: float | None
    load_s: float | None            # loading the model into memory (0 when it was loaded already)
    prompt_tokens: int | None
    prompt_s: float | None
    eval_tokens: int | None
    eval_s: float | None
    done_reason: str | None         # "stop", "length", "load", "unload"


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]       # parsed; a JSON string from the model is decoded, garbage -> {"_raw": text}


@dataclass(frozen=True)
class ChatResult:
    """One /api/chat answer (stream off): text, structured tool calls, timings."""
    content: str
    thinking_chars: int             # thinking models: length of the reasoning Ollama returned separately
    tool_calls: tuple[ToolCall, ...]
    total_s: float | None
    eval_tokens: int | None
    eval_s: float | None
    done_reason: str | None


def _seconds(ns: Any) -> float | None:
    value = _int_or_none(ns)
    return None if value is None else value / 1e9


def parse_generate(payload: Any) -> GenerateResult:
    if not isinstance(payload, dict):
        raise OllamaUnavailable("unexpected /api/generate response (not an object)")
    return GenerateResult(
        total_s=_seconds(payload.get("total_duration")), load_s=_seconds(payload.get("load_duration")),
        prompt_tokens=_int_or_none(payload.get("prompt_eval_count")),
        prompt_s=_seconds(payload.get("prompt_eval_duration")),
        eval_tokens=_int_or_none(payload.get("eval_count")), eval_s=_seconds(payload.get("eval_duration")),
        done_reason=payload.get("done_reason") or None,
    )


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except ValueError:
            return {"_raw": raw}
        return value if isinstance(value, dict) else {"_raw": raw}
    return {}


def parse_chat(payload: Any) -> ChatResult:
    if not isinstance(payload, dict):
        raise OllamaUnavailable("unexpected /api/chat response (not an object)")
    msg = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    calls = []
    for item in msg.get("tool_calls") or []:
        fn = item.get("function") if isinstance(item, dict) else None
        if isinstance(fn, dict) and fn.get("name"):
            calls.append(ToolCall(name=str(fn["name"]), arguments=_arguments(fn.get("arguments"))))
    return ChatResult(
        content=str(msg.get("content") or ""), thinking_chars=len(str(msg.get("thinking") or "")),
        tool_calls=tuple(calls), total_s=_seconds(payload.get("total_duration")),
        eval_tokens=_int_or_none(payload.get("eval_count")), eval_s=_seconds(payload.get("eval_duration")),
        done_reason=payload.get("done_reason") or None,
    )


class OllamaAdapter(Protocol):
    simulated: bool

    async def running(self) -> list[RunningModel]: ...

    async def version(self) -> str: ...

    async def tags(self) -> list[InstalledModel]: ...

    async def show(self, name: str, timeout: float | None = None) -> ModelDetails: ...

    async def generate(self, name: str, prompt: str, options: dict[str, Any], keep_alive: str | int,
                       timeout: float) -> GenerateResult: ...

    async def chat(self, name: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                   options: dict[str, Any], keep_alive: str | int, timeout: float) -> ChatResult: ...

    async def unload(self, name: str, timeout: float = 30) -> None: ...


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


def _details(raw: Any) -> dict:
    return raw if isinstance(raw, dict) else {}


def _families(value: Any) -> tuple[str, ...]:
    return tuple(str(f) for f in value) if isinstance(value, list) else ()


def parse_tags(payload: Any) -> list[InstalledModel]:
    """Raw /api/tags JSON -> InstalledModel list, sorted by name (Ollama sorts by modification time)."""
    if not isinstance(payload, dict):
        raise OllamaUnavailable("unexpected /api/tags response (not an object)")
    items = payload.get("models") or []
    if not isinstance(items, list):
        raise OllamaUnavailable("unexpected /api/tags response (models is not a list)")
    out: list[InstalledModel] = []
    for m in items:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or m.get("model") or "")
        if not name:
            continue                                    # nothing to show without a name
        d = _details(m.get("details"))
        out.append(InstalledModel(
            name=name, digest=str(m.get("digest") or ""), size=_int_or_none(m.get("size")) or 0,
            modified_at=_time_or_none(m.get("modified_at")),
            family=d.get("family") or None, families=_families(d.get("families")),
            parameter_size=d.get("parameter_size") or None, quantization=d.get("quantization_level") or None,
            format=d.get("format") or None, parent_model=d.get("parent_model") or None,
        ))
    return sorted(out, key=lambda m: m.name)


def parse_parameters(text: Any) -> dict[str, tuple[str, ...]]:
    """The "parameters" text of /api/show -> {"num_ctx": ("65536",), "stop": ("<|im_end|>", ...)}.

    Format: one "key<spaces>value" per line, strings in double quotes (Go's %q). Unknown keys are kept.
    """
    out: dict[str, list[str]] = {}
    if not isinstance(text, str):
        return {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        key, value = parts[0], parts[1].strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            try:
                value = json.loads(value)               # Go %q is close enough to a JSON string
            except ValueError:
                value = value[1:-1]
        out.setdefault(key, []).append(str(value))
    return {k: tuple(v) for k, v in out.items()}


# capture_samples replaces a system prompt with this text: recorded samples keep its length, not its words
_SYSTEM_PLACEHOLDER = re.compile(r"^<system prompt removed: (\d+) characters>$")


def system_placeholder(length: int) -> str:
    return f"<system prompt removed: {length} characters>"


def _system_chars(system: Any) -> int:
    if not isinstance(system, str):
        return 0
    m = _SYSTEM_PLACEHOLDER.match(system)
    return int(m.group(1)) if m else len(system)


def _hash(text: Any) -> str | None:
    if not isinstance(text, str) or not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def weights_digest(modelfile: Any) -> str | None:
    """sha256 of the weights blob from the first blob FROM line of a generated Modelfile, else None."""
    if not isinstance(modelfile, str):
        return None
    m = _BLOB_FROM.search(modelfile)
    return m.group(1).lower() if m else None


def parse_show(name: str, payload: Any) -> ModelDetails:
    """Raw /api/show JSON -> ModelDetails. Missing parts become empty, never an error."""
    if not isinstance(payload, dict):
        raise OllamaUnavailable("unexpected /api/show response (not an object)")
    d = _details(payload.get("details"))
    info = payload.get("model_info")
    caps = payload.get("capabilities")
    system = payload.get("system")
    return ModelDetails(
        name=name, parent_model=d.get("parent_model") or None,
        family=d.get("family") or None, families=_families(d.get("families")),
        parameter_size=d.get("parameter_size") or None, quantization=d.get("quantization_level") or None,
        format=d.get("format") or None, parameters=parse_parameters(payload.get("parameters")),
        system_chars=_system_chars(system),
        template_hash=_hash(payload.get("template")), system_hash=_hash(system),
        weights_digest=weights_digest(payload.get("modelfile")),
        model_info=dict(info) if isinstance(info, dict) else {},
        capabilities=tuple(str(c) for c in caps) if isinstance(caps, list) else (),
        modified_at=_time_or_none(payload.get("modified_at")),
    )


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

    async def tags(self) -> list[InstalledModel]:
        return parse_tags(await self._get("/api/tags"))

    async def show(self, name: str, timeout: float | None = None) -> ModelDetails:
        """POST /api/show. 404 = the model is gone (OllamaModelMissing), anything else = unavailable."""
        try:
            kwargs = {} if timeout is None else {"timeout": timeout}
            r = await self._client.post(self._base + "/api/show", json={"model": name}, **kwargs)
            if r.status_code == 404:
                raise OllamaModelMissing(name)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OllamaUnavailable(f"/api/show {name}: {describe(exc)}") from exc
        return parse_show(name, data)

    async def _post_generate(self, body: dict[str, Any], timeout: float, path: str = "/api/generate") -> Any:
        """404 -> OllamaModelMissing, other HTTP errors -> OllamaRequestFailed with Ollama's error text."""
        try:
            r = await self._client.post(self._base + path, json=body, timeout=timeout)
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"{path}: {describe(exc)}") from exc
        if r.status_code == 404:
            raise OllamaModelMissing(body.get("model", "?"))
        if r.status_code >= 400:
            try:
                detail = r.json().get("error") or r.text
            except ValueError:
                detail = r.text
            raise OllamaRequestFailed(f"HTTP {r.status_code}: {str(detail).strip()[:500]}")
        try:
            return r.json()
        except ValueError as exc:
            raise OllamaUnavailable(f"{path}: {describe(exc)}") from exc

    async def generate(self, name: str, prompt: str, options: dict[str, Any], keep_alive: str | int,
                       timeout: float) -> GenerateResult:
        """Loads the model if needed and answers the prompt; stream off, so the timings come in one piece."""
        body = {"model": name, "prompt": prompt, "options": options, "keep_alive": keep_alive, "stream": False}
        return parse_generate(await self._post_generate(body, timeout))

    async def chat(self, name: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                   options: dict[str, Any], keep_alive: str | int, timeout: float) -> ChatResult:
        """One chat turn with tool definitions, as a coding agent sends it. A model without tool support in its
        template makes Ollama answer HTTP 400 "... does not support tools" (-> OllamaRequestFailed)."""
        body = {"model": name, "messages": messages, "tools": tools, "options": options, "keep_alive": keep_alive,
                "stream": False}
        return parse_chat(await self._post_generate(body, timeout, "/api/chat"))

    async def unload(self, name: str, timeout: float = 30) -> None:
        """keep_alive 0 without a prompt = Ollama's documented way to unload a model right away."""
        await self._post_generate({"model": name, "keep_alive": 0}, timeout)


class FakeOllama:
    """Replays ollama-ps.json / ollama-version.json / ollama-tags.json / ollama-show.json of a scenario
    folder. Missing file = Ollama down (for that call). ollama-show.json maps model name -> raw answer.

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

    async def tags(self) -> list[InstalledModel]:
        return parse_tags(self._load("ollama-tags.json"))

    async def show(self, name: str, timeout: float | None = None) -> ModelDetails:
        shows = self._load("ollama-show.json")
        if not isinstance(shows, dict) or name not in shows:
            raise OllamaModelMissing(name)
        return parse_show(name, shows[name])

    async def generate(self, name: str, prompt: str, options: dict[str, Any], keep_alive: str | int,
                       timeout: float) -> GenerateResult:
        """Simulation: plausible fixed timings for every installed model; nothing is loaded for real."""
        shows = self._load("ollama-show.json")
        if not isinstance(shows, dict) or name not in shows:
            raise OllamaModelMissing(name)
        predict = int(options.get("num_predict") or 128)
        return GenerateResult(total_s=6.4 + predict / 60, load_s=4.2, prompt_tokens=58, prompt_s=0.12,
                              eval_tokens=predict, eval_s=predict / 60, done_reason="length")

    async def chat(self, name: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                   options: dict[str, Any], keep_alive: str | int, timeout: float) -> ChatResult:
        """Simulation: replays raw /api/chat answers from ollama-chat.json, looked up by the text of the last
        message - first under "models" -> <model name>, then under "default". A model without "tools" in its
        capabilities gets Ollama's real refusal, as live."""
        shows = self._load("ollama-show.json")
        if not isinstance(shows, dict) or name not in shows:
            raise OllamaModelMissing(name)
        if "tools" not in parse_show(name, shows[name]).capabilities:
            raise OllamaRequestFailed(f"HTTP 400: registry.ollama.ai/library/{name} does not support tools")
        recorded = self._load("ollama-chat.json")
        request = str(messages[-1].get("content", "")) if messages else ""
        own = (recorded.get("models") or {}).get(name) or {}
        raw = own.get(request) or (recorded.get("default") or {}).get(request)
        return parse_chat(raw or {"message": {"content": "(no recorded answer for this request)"}})

    async def unload(self, name: str, timeout: float = 30) -> None:
        return None
