"""Candidate check: would a model from the Ollama library run on this card - BEFORE an 18 GB download?

The registry answers in three cheap steps:
1. manifest: which layers and how big - weights, projector (vision encoder), template, params ...
2. small files: config (family, size class, quantization, Ollama's renderer/parser, minimum version),
   params (num_ctx, stop ...), template (does it know .Tools?)
3. the first MiB of the weights file: GGUF metadata (layers, KV heads, head size, trained context).
   Several MiB when the tokenizer tables come first - read in growing pieces up to registry_header_max_mib.

From that a ModelRecord is built like for an installed model, so the same estimate, verdict and OpenCode
check apply - including the calibration: an installed model with the very same weights file (Ollama
shares blobs) lends its measurements.

What the registry cannot say: whether the model really makes structured tool calls (only a test run after
the pull shows it) and whether the installed Ollama can run the architecture (the config's "requires"
names the minimum version, when the publisher set it).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from control_center.adapters.library import Layer, LibraryAdapter, Manifest, ModelRef
from control_center.modules.catalog.collector import ModelRecord, keep_info
from control_center.modules.catalog.gguf import GgufError, Header, NeedMore, parse_header, partial_header
from control_center.modules.catalog.settings import CatalogSettings

MIB = 1024 ** 2
HEADER_FIRST = 1 * MIB                   # first piece of the weights file; grows x2 while the header goes on
SMALL_MAX = 64 * 1024                    # config / params: a few hundred bytes in reality
TEMPLATE_MAX = 256 * 1024                # templates with tool sections have ~5 KB
MAX_INFO_STRING = 512                    # longer GGUF strings (chat templates) stay out of the record
# llama.cpp file types, for files whose config has no file_type
FILE_TYPES = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K",
              11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M",
              18: "Q6_K", 19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS", 23: "IQ3_XXS", 24: "IQ1_S",
              25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M",
              32: "BF16", 36: "TQ1_0", 37: "TQ2_0", 38: "MXFP4"}
# Ollama's built-in parsers that split off a reasoning part (the template then is a plain {{ .Prompt }})
THINKING_PARSERS = {"harmony", "qwen3", "qwen3.5", "qwen3-thinking", "qwen3-vl-thinking", "deepseek3"}

_TOOLS = re.compile(r"\.Tools\b")
_SUFFIX = re.compile(r"\.Suffix\b")
_THINK = re.compile(r"\.Think(Level)?\b|<think>")


@dataclass
class CandidateFacts:
    """What the registry said. Stored as JSON; verdict and steps are computed fresh for every overview,
    so a new measurement or a changed budget reaches the candidates too."""
    name: str                                   # as Ollama will list it after the pull
    host: str
    page: str | None
    checked_at: float                           # unix seconds
    simulated: bool
    record: ModelRecord
    download_bytes: int
    weights_bytes: int
    projector_bytes: int = 0
    capability_notes: list[str] = field(default_factory=list)
    requires: str | None = None                 # minimum Ollama version from the config
    renderer: str | None = None
    parser: str | None = None
    header_bytes: int = 0                       # bytes of the weights file read
    header_complete: bool = False
    notes: list[str] = field(default_factory=list)
    error: str | None = None                    # no estimate possible (cloud model, no GGUF ...)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["record"] = self.record.to_json()
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "CandidateFacts":
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in known}
        values["record"] = ModelRecord.from_json(values.get("record") or {"name": data["name"], "digest": "",
                                                                          "size": 0})
        return cls(**values)


# ---- reading ------------------------------------------------------------------------------------------

async def _small(library: LibraryAdapter, ref: ModelRef, layer: Layer | None, limit: int,
                 timeout: float) -> bytes | None:
    """Up to `limit` bytes, whatever size the manifest declares: on 09.10. the registry declared 539 bytes for
    the config of qwen3-coder:30b and delivered 542 - reading exactly 539 cut the JSON short."""
    if layer is None:
        return None
    return await library.blob(ref, layer.digest, 0, limit, timeout)


def _start(data: bytes) -> str:
    """First characters of an unreadable answer, printable - says at a glance what came back instead."""
    text = data[:60].decode("utf-8", errors="replace")
    return "".join(c if c.isprintable() else "·" for c in text) + ("…" if len(data) > 60 else "")


def _json(data: bytes | None, what: str, notes: list[str], limit: int = SMALL_MAX) -> dict[str, Any]:
    if not data:
        return {}
    try:
        value = json.loads(data)
    except ValueError:
        cut = " (größer als die Lesegrenze)" if len(data) >= limit else ""
        notes.append(f"{what} nicht lesbar (kein JSON{cut}, beginnt mit „{_start(data)}“) – ohne diese Angaben "
                     f"gerechnet.")
        return {}
    return value if isinstance(value, dict) else {}


async def read_header(library: LibraryAdapter, ref: ModelRef, layer: Layer, cap: int,
                      timeout: float, first: int = HEADER_FIRST) -> tuple[Header, int]:
    """GGUF header of the weights file in growing pieces (1, 2, 4 ... MiB) up to `cap` bytes.
    At the cap: the entries read so far (Header.complete False). Raises GgufError.
    The end of the file is what the storage says (a shorter answer than asked), not the manifest's size."""
    buf = b""
    want = min(first, cap)
    while True:
        asked = want - len(buf)
        chunk = await library.blob(ref, layer.digest, len(buf), asked, timeout)
        buf += chunk
        try:
            return parse_header(buf), len(buf)
        except NeedMore as more:
            if len(chunk) < asked:
                raise GgufError("Die Datei endet mitten in den Metadaten – unvollständig veröffentlicht?") from None
            if len(buf) >= cap:
                return partial_header(buf, more), len(buf)
            want = min(cap, max(len(buf) * 2, more.at))


async def fetch_candidate(library: LibraryAdapter, ref: ModelRef, cfg: CatalogSettings,
                          now: float) -> CandidateFacts:
    """Manifest, small files, GGUF header -> facts. LibraryNotFound / LibraryUnavailable pass through;
    an unreadable weights file ends up in facts.error (the size facts are still worth showing)."""
    timeout = cfg.registry_timeout_s
    manifest = await library.manifest(ref, timeout)
    notes: list[str] = []
    config = _json(await _small(library, ref, manifest.config, SMALL_MAX, timeout), "Konfiguration", notes)
    params = _json(await _small(library, ref, manifest.first("params"), SMALL_MAX, timeout), "Parameter", notes)
    tmpl_layer = manifest.first("template")
    raw_tmpl = await _small(library, ref, tmpl_layer, TEMPLATE_MAX, timeout)
    template = raw_tmpl.decode("utf-8", errors="replace") if raw_tmpl is not None else None
    if raw_tmpl is not None and len(raw_tmpl) >= TEMPLATE_MAX:
        notes.append("Template sehr groß – nur der Anfang gelesen.")

    weights = manifest.first("model")
    header: Header | None = None
    read, error = 0, None
    if weights is None:
        remote = config.get("remote_host")
        error = (f"Cloud-Modell: läuft auf {remote}, nicht auf dieser Karte." if isinstance(remote, str) and remote
                 else "Keine Gewichte im Manifest – nichts, was auf die Karte käme.")
    elif str(config.get("model_format") or "gguf").lower() != "gguf":
        error = f"Format „{config.get('model_format')}“ – der Katalog liest nur GGUF."
    else:
        try:
            header, read = await read_header(library, ref, weights, cfg.registry_header_max_mib * MIB, timeout)
        except GgufError as exc:
            error = str(exc)
    if len(manifest.all("model")) > 1:
        notes.append(f"{len(manifest.all('model'))} Gewichtsdateien – gerechnet mit allen, gelesen nur die erste.")
    if header is not None and not header.complete:
        notes.append(f"Metadaten nur zum Teil gelesen (Grenze {cfg.registry_header_max_mib} MiB) – "
                     f"Angaben können fehlen.")

    record, cap_notes = build_record(ref, manifest, config, params, template, header)
    projector = sum(la.size for la in manifest.all("projector"))
    return CandidateFacts(
        name=ref.name, host=ref.host, page=ref.page, checked_at=now, simulated=library.simulated, record=record,
        download_bytes=manifest.size, weights_bytes=sum(la.size for la in manifest.all("model")),
        projector_bytes=projector, capability_notes=cap_notes, requires=_str(config.get("requires")),
        renderer=_str(config.get("renderer")), parser=_str(config.get("parser")), header_bytes=read,
        header_complete=bool(header and header.complete), notes=notes, error=error)


# ---- building the record ---------------------------------------------------------------------------------

def _str(value: Any) -> str | None:
    """A usable text, or None. hf.co writes "unknown" into fields it cannot fill - that is no value either."""
    if not isinstance(value, str) or not value.strip() or value.strip().lower() == "unknown":
        return None
    return value.strip()


def _param_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:g}"
    return str(value)


def params_from(data: dict[str, Any]) -> dict[str, list[str]]:
    """params blob {"num_ctx": 4096, "stop": ["<|im_end|>"]} -> the shape of /api/show parameters."""
    out: dict[str, list[str]] = {}
    for key, value in data.items():
        values = value if isinstance(value, list) else [value]
        out[str(key)] = [_param_text(v) for v in values if v is not None]
    return {k: v for k, v in out.items() if v}


def capabilities(kv: dict[str, Any], template: str | None, config: dict[str, Any],
                 projector: bool) -> tuple[list[str], list[str]]:
    """What Ollama will report after the pull, derived the way Ollama derives it (template variables, parser,
    projector, pooling type). Returns (capabilities, German notes on where each one came from)."""
    arch = kv.get("general.architecture")
    caps: list[str] = []
    notes: list[str] = []
    remote = config.get("capabilities")
    if config.get("remote_host") and isinstance(remote, list):       # cloud model: the config lists them
        return [str(c) for c in remote], notes
    if arch and f"{arch}.pooling_type" in kv:
        caps.append("embedding")
        notes.append("Einbettungsmodell (pooling_type in den Metadaten).")
        return caps, notes
    caps.append("completion")
    parser = _str(config.get("parser"))
    tmpl = template or ""
    if _TOOLS.search(tmpl):
        caps.append("tools")
        notes.append("Werkzeuge: das Template nutzt .Tools.")
    elif parser:
        caps.append("tools")
        notes.append(f"Werkzeuge: Ollamas eingebauter Parser „{parser}“ (kein Template nötig).")
    elif template is not None:
        notes.append("Keine Werkzeuge: das Template kennt kein .Tools – Ollama wird Tool-Anfragen ablehnen.")
    else:
        notes.append("Werkzeuge unklar: weder Template noch Parser in der Registry.")
    if _SUFFIX.search(tmpl):
        caps.append("insert")
    if projector or (arch and f"{arch}.vision.block_count" in kv):
        caps.append("vision")
        notes.append("Bilder: " + ("eigene Encoder-Datei (projector)." if projector else "Encoder in der Gewichtsdatei."))
    family = str(config.get("model_family") or arch or "")
    if _THINK.search(tmpl) or family in ("gptoss", "gpt-oss") or (parser and (parser in THINKING_PARSERS
                                                                              or "think" in parser)):
        caps.append("thinking")
    return caps, notes


def build_record(ref: ModelRef, manifest: Manifest, config: dict[str, Any], params: dict[str, Any],
                 template: str | None, header: Header | None) -> tuple[ModelRecord, list[str]]:
    kv = {k: v for k, v in (header.kv if header else {}).items()
          if not (isinstance(v, str) and len(v) > MAX_INFO_STRING)}
    weights = manifest.first("model")
    system = manifest.first("system")
    caps, notes = capabilities(kv, template, config, bool(manifest.all("projector")))
    file_type = kv.get("general.file_type")
    families = config.get("model_families")
    record = ModelRecord(
        name=ref.name, digest=manifest.digest.removeprefix("sha256:"), size=manifest.size,
        family=_str(config.get("model_family")) or _str(kv.get("general.architecture")),
        families=[str(f) for f in families] if isinstance(families, list) else [],
        parameter_size=_str(config.get("model_type")),
        quantization=_str(config.get("file_type")) or (FILE_TYPES.get(file_type) if isinstance(file_type, int)
                                                       else None),
        format=_str(config.get("model_format")) or ("gguf" if header else None),
        weights_digest=weights.digest.removeprefix("sha256:") if weights else None,
        parameters=params_from(params),
        system_chars=system.size if system else 0,
        template_hash=hashlib.sha256(template.encode("utf-8")).hexdigest()[:16] if template else None,
        capabilities=caps, model_info=keep_info(kv))
    return record, notes


# ---- versions --------------------------------------------------------------------------------------------

def version_tuple(text: str | None) -> tuple[int, ...] | None:
    """'0.12.6' -> (0, 12, 6); '0.13.0-rc1' -> (0, 13, 0); garbage -> None."""
    if not text:
        return None
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", text)
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def version_ok(installed: str | None, required: str | None) -> bool | None:
    """None = one of them unknown."""
    have, need = version_tuple(installed), version_tuple(required)
    if have is None or need is None:
        return None
    width = max(len(have), len(need))
    return have + (0,) * (width - len(have)) >= need + (0,) * (width - len(need))
