"""Ollama's model library (registry.ollama.ai, also hf.co): what a model consists of BEFORE it is pulled.

`ollama pull qwen3-coder:30b` first fetches a manifest - the list of layers (weights, vision projector,
template, params ...) with digest and size - and then the files ("blobs"). The candidate check reads the
same manifest, the small blobs and only the START of the weights file: the GGUF metadata (layers, KV heads,
trained context) sits in its first few MiB. A Range request fetches those from a file of 18 GB.

    [adapters] library = "http" | "fake"     library_hosts = ["registry.ollama.ai", "hf.co"]

HttpLibrary talks to the registries (public models, no login). Blob downloads answer with a redirect to a
storage server (Cloudflare R2 for Ollama) - followed, the Range header goes along.
FakeLibrary replays library-samples/ (synthetic, for tests and the second PC without internet).

Only hosts from library_hosts are contacted: the name comes from a text field of the page, and the server
must not become a tool that fetches arbitrary addresses inside the LAN.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from control_center.adapters.common import describe

DEFAULT_HOST = "registry.ollama.ai"
MANIFEST_ACCEPT = "application/vnd.docker.distribution.manifest.v2+json"
LAYER_PREFIX = "application/vnd.ollama.image."
MAX_MANIFEST_BYTES = 1024 * 1024            # a real manifest has a few hundred bytes
# what people paste: the web page of a model is not the registry, but names the same model
HOST_ALIASES = {"ollama.com": DEFAULT_HOST, "www.ollama.com": DEFAULT_HOST, "huggingface.co": "hf.co",
                "www.huggingface.co": "hf.co"}
LIBRARY_SAMPLES_DIR = Path(__file__).parent / "library-samples"

_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_HOST = re.compile(r"^[a-z0-9.-]+(:[0-9]{1,5})?$")


class LibraryUnavailable(Exception):
    """No usable answer: no internet, registry down, timeout, unexpected response. German message."""


class LibraryNotFound(Exception):
    """The registry does not know this model or tag (or it is private). German message."""


class BadReference(ValueError):
    """The text is no model name the registry could know. German message."""


@dataclass(frozen=True)
class ModelRef:
    host: str                       # "registry.ollama.ai"
    namespace: str                  # "library" for official models, else the user / organisation
    model: str                      # "qwen3-coder"
    tag: str                        # "30b", default "latest"

    @property
    def repo(self) -> str:
        return f"{self.namespace}/{self.model}"

    @property
    def name(self) -> str:
        """The name Ollama will list after the pull: 'qwen3-coder:30b', 'user/model:tag', 'hf.co/org/repo:tag'."""
        if self.host == DEFAULT_HOST:
            base = self.model if self.namespace == "library" else self.repo
        else:
            base = f"{self.host}/{self.repo}"
        return f"{base}:{self.tag}"

    @property
    def page(self) -> str | None:
        """Web page of the model, for a link on the page."""
        if self.host == DEFAULT_HOST:
            path = self.model if self.namespace == "library" else self.repo
            return f"https://ollama.com/{'library/' if self.namespace == 'library' else ''}{path}:{self.tag}"
        if self.host == "hf.co":
            return f"https://huggingface.co/{self.repo}"
        return None


def parse_ref(text: str, hosts: list[str] | tuple[str, ...] = (DEFAULT_HOST, "hf.co")) -> ModelRef:
    """'qwen3-coder:30b', 'ollama pull gemma3:12b', 'https://ollama.com/library/devstral', 'hf.co/org/repo:Q4_K_M'
    -> ModelRef. Raises BadReference with a German message."""
    raw = " ".join((text or "").split())
    for prefix in ("ollama pull ", "ollama run "):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):].strip()
    if not raw:
        raise BadReference("Bitte einen Modellnamen eingeben, z. B. qwen3-coder:30b.")
    if " " in raw:
        raise BadReference(f"„{raw}“ ist kein Modellname (Leerzeichen).")
    if "://" in raw:                                   # a copied web address
        parts = urlsplit(raw)
        host = HOST_ALIASES.get(parts.hostname or "", parts.hostname or "")
        segments = [s for s in parts.path.split("/") if s]
        if host == DEFAULT_HOST:
            if segments[:1] == ["library"]:
                segments = segments[1:]
            if segments[-1:] == ["tags"]:                # the tag list page of a model
                segments = segments[:-1]
            raw = "/".join(segments)
        else:
            raw = "/".join([host, *segments[:2]])        # hf.co/org/repo, nothing of /blob/main/...
    path, tag = raw, "latest"
    last = raw.rsplit("/", 1)[-1]
    if ":" in last:
        path, tag = raw.rsplit(":", 1)
    parts = path.split("/")
    if len(parts) == 1:
        host, namespace, model = DEFAULT_HOST, "library", parts[0]
    elif len(parts) == 2 and "." not in parts[0]:
        host, namespace, model = DEFAULT_HOST, parts[0], parts[1]
    elif len(parts) == 3:
        host, namespace, model = parts[0].lower(), parts[1], parts[2]
        host = HOST_ALIASES.get(host, host)
    else:
        raise BadReference(f"„{raw}“ ist kein Modellname. Form: modell:tag, nutzer/modell:tag oder "
                           f"hf.co/organisation/repo:tag.")
    if not _HOST.match(host):
        raise BadReference(f"„{host}“ ist kein Registry-Host.")
    if host not in hosts:
        allowed = ", ".join(hosts) or "keine"
        raise BadReference(f"Registry „{host}“ ist nicht freigegeben (erlaubt: {allowed} – "
                           f"[adapters] library_hosts).")
    for label, value, pattern in (("Namensraum", namespace, _PART), ("Modell", model, _PART), ("Tag", tag, _TAG)):
        if not value:
            raise BadReference(f"{label} fehlt in „{raw}“.")
        if not pattern.match(value):
            raise BadReference(f"{label} „{value}“ enthält unzulässige Zeichen.")
    if host == DEFAULT_HOST:
        namespace, model, tag = namespace.lower(), model.lower(), tag.lower()   # the library is lower case
    return ModelRef(host, namespace, model, tag)


@dataclass(frozen=True)
class Layer:
    media_type: str
    digest: str                     # "sha256:<hex>"
    size: int
    source: str | None = None       # "from": file name the layer was made of ("model.gguf")

    @property
    def kind(self) -> str:
        """'model', 'projector', 'template', 'params', 'system', 'license', 'adapter', 'messages' ..."""
        mt = self.media_type
        return mt[len(LAYER_PREFIX):] if mt.startswith(LAYER_PREFIX) else mt


@dataclass(frozen=True)
class Manifest:
    digest: str                     # sha256 of the manifest bytes ("sha256:<hex>")
    config: Layer | None
    layers: tuple[Layer, ...]
    extra: dict[str, Any] = field(default_factory=dict)    # other top-level keys ("runner", "format" ...)

    def all(self, kind: str) -> list[Layer]:
        return [la for la in self.layers if la.kind == kind]

    def first(self, kind: str) -> Layer | None:
        found = self.all(kind)
        return found[0] if found else None

    @property
    def size(self) -> int:
        """What /api/tags will report after the pull: all layers plus the config."""
        return sum(la.size for la in self.layers) + (self.config.size if self.config else 0)


def _layer(raw: Any) -> Layer | None:
    if not isinstance(raw, dict):
        return None
    digest, size = raw.get("digest"), raw.get("size")
    if not isinstance(digest, str) or not digest.startswith("sha256:") or not isinstance(size, int) or size < 0:
        return None
    src = raw.get("from")
    return Layer(str(raw.get("mediaType") or ""), digest, size, src if isinstance(src, str) else None)


def parse_manifest(body: bytes) -> Manifest:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise LibraryUnavailable(f"Manifest ist kein JSON: {describe(exc)}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("layers"), list):
        raise LibraryUnavailable("Manifest ohne Layer-Liste – unbekanntes Format.")
    layers = tuple(la for la in (_layer(x) for x in data["layers"]) if la is not None)
    extra = {k: v for k, v in data.items() if k not in ("schemaVersion", "mediaType", "config", "layers")
             and isinstance(v, (str, int, float, bool))}
    return Manifest("sha256:" + hashlib.sha256(body).hexdigest(), _layer(data.get("config")), layers, extra)


class LibraryAdapter(Protocol):
    simulated: bool

    async def manifest(self, ref: ModelRef, timeout: float) -> Manifest: ...

    async def blob(self, ref: ModelRef, digest: str, start: int, length: int, timeout: float) -> bytes:
        """Bytes [start, start+length) of a blob; fewer when the file ends earlier (b"" past its end)."""
        ...


class HttpLibrary:
    simulated = False

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client                  # shared, owned by the adapter set

    @staticmethod
    def _url(ref: ModelRef, path: str) -> str:
        return f"https://{ref.host}/v2/{ref.repo}/{path}"

    async def manifest(self, ref: ModelRef, timeout: float) -> Manifest:
        try:
            r = await self._client.get(self._url(ref, f"manifests/{ref.tag}"), headers={"Accept": MANIFEST_ACCEPT},
                                       timeout=timeout, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise LibraryUnavailable(f"{ref.host} nicht erreichbar: {describe(exc)}") from exc
        if r.status_code in (401, 403, 404):
            # private models answer 401 instead of 404 - for the page both mean "not there for us"
            raise LibraryNotFound(f"{ref.name} gibt es in {ref.host} nicht (Name oder Tag falsch, oder privat).")
        if r.status_code >= 400:
            raise LibraryUnavailable(f"{ref.host} antwortet HTTP {r.status_code}.")
        if len(r.content) > MAX_MANIFEST_BYTES:
            raise LibraryUnavailable("Manifest unerwartet groß – abgebrochen.")
        return parse_manifest(r.content)

    async def blob(self, ref: ModelRef, digest: str, start: int, length: int, timeout: float) -> bytes:
        if length <= 0:
            return b""
        headers = {"Range": f"bytes={start}-{start + length - 1}"}
        out = bytearray()
        try:
            async with self._client.stream("GET", self._url(ref, f"blobs/{digest}"), headers=headers,
                                           timeout=timeout, follow_redirects=True) as r:
                if r.status_code == 416:                     # start lies behind the end of the file
                    return b""
                if r.status_code >= 400:
                    raise LibraryUnavailable(f"Datei {digest[:19]}…: HTTP {r.status_code}.")
                skip = start if r.status_code == 200 else 0  # 200 = the server ignored the Range header
                async for chunk in r.aiter_bytes():
                    if skip:
                        cut = min(skip, len(chunk))
                        chunk, skip = chunk[cut:], skip - cut
                    out += chunk
                    if len(out) >= length:                   # enough: closing the stream ends the download
                        break
        except httpx.HTTPError as exc:
            raise LibraryUnavailable(f"Datei {digest[:19]}…: {describe(exc)}") from exc
        return bytes(out[:length])


class FakeLibrary:
    """Replays library-samples/: index.json maps a model name (ModelRef.name) to a manifest file,
    blobs/sha256-<hex> holds the small files and the HEAD of each weights file (a few KB of GGUF header
    instead of gigabytes). A name missing from the index = not in the registry."""
    simulated = True

    def __init__(self, folder: Path = LIBRARY_SAMPLES_DIR) -> None:
        self.folder = folder

    def _index(self) -> dict[str, str]:
        try:
            data = json.loads((self.folder / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LibraryUnavailable(f"Simulation: index.json nicht lesbar ({describe(exc)})") from exc
        return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}

    async def manifest(self, ref: ModelRef, timeout: float) -> Manifest:
        file = self._index().get(ref.name)
        if file is None:
            raise LibraryNotFound(f"{ref.name} gibt es in {ref.host} nicht (Simulation: nicht in index.json).")
        try:
            body = (self.folder / "manifests" / file).read_bytes()
        except OSError as exc:
            raise LibraryUnavailable(f"Simulation: Manifest {file} fehlt ({describe(exc)})") from exc
        return parse_manifest(body)

    async def blob(self, ref: ModelRef, digest: str, start: int, length: int, timeout: float) -> bytes:
        path = self.folder / "blobs" / digest.replace(":", "-")
        try:
            with path.open("rb") as f:
                f.seek(start)
                return f.read(max(0, length))
        except OSError as exc:
            raise LibraryUnavailable(f"Simulation: Datei {digest[:19]}… fehlt ({describe(exc)})") from exc
