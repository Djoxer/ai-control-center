"""GGUF header reader: the metadata at the start of a model file, without the rest of the file.

Layout (little endian), as written by llama.cpp and Ollama:

    "GGUF"  u32 version (2 or 3)  u64 tensor_count  u64 kv_count
    kv_count x [ key: u64 length + UTF-8 bytes | u32 type | value ]

Values: 0 u8, 1 i8, 2 u16, 3 i16, 4 u32, 5 i32, 6 f32, 7 bool, 8 string (u64 length + bytes),
9 array (u32 element type, u64 count, elements), 10 u64, 11 i64, 12 f64.

The parser works on a byte buffer that may end too early: then it raises NeedMore with the position it
needs and what it collected so far - the caller fetches the next piece of the file (a Range request) and
parses again. The tokenizer tables (150,000+ strings) are walked through but not kept; like /api/show,
only scalars and short number lists (per-layer KV heads) end up in the result.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

MAGIC = b"GGUF"
KEEP_LIST = 512                          # number lists up to this length are kept (one entry per layer)
MAX_KEYS = 100_000                       # a real header has a few dozen
MAX_STRING = 64 * 1024 * 1024            # one string longer than this = a broken file, not a big one
MAX_ARRAY_BYTES = 1024 ** 3              # same for a number list, and 50 million strings in one list
MAX_STRINGS = 50_000_000

_SCALAR = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
STRING, ARRAY = 8, 9


class GgufError(ValueError):
    """Not a GGUF file, or a broken one. German message."""


class NeedMore(Exception):
    """The buffer ends inside the header. `at` = bytes needed to get past the point where it ended."""

    def __init__(self, at: int, kv: dict[str, Any]) -> None:
        super().__init__(at)
        self.at = at
        self.kv = kv                     # what was complete before the buffer ended


@dataclass
class Header:
    version: int
    tensor_count: int
    kv_count: int
    kv: dict[str, Any] = field(default_factory=dict)
    end: int = 0                         # byte offset where the key/value section ends
    complete: bool = True                # False = cut at the size limit, kv holds the first entries only


class _Reader:
    def __init__(self, buf: bytes) -> None:
        self.buf = buf
        self.pos = 0
        self.kv: dict[str, Any] = {}

    def need(self, n: int) -> None:
        if self.pos + n > len(self.buf):
            raise NeedMore(self.pos + n, self.kv)

    def unpack(self, fmt: str) -> Any:
        size = struct.calcsize(fmt)
        self.need(size)
        value = struct.unpack_from(fmt, self.buf, self.pos)[0]
        self.pos += size
        return value

    def string(self, keep: bool = True) -> str | None:
        n = self.unpack("<Q")
        if n > MAX_STRING:
            raise GgufError(f"Zeichenkette mit {n} Byte an Position {self.pos} – Datei beschädigt?")
        self.need(n)
        start, self.pos = self.pos, self.pos + n
        return self.buf[start:self.pos].decode("utf-8", errors="replace") if keep else None

    def value(self, kind: int) -> Any:
        if kind in _SCALAR:
            return self.unpack(_SCALAR[kind])
        if kind == STRING:
            return self.string()
        if kind == ARRAY:
            return self.array()
        raise GgufError(f"Unbekannter Werttyp {kind} an Position {self.pos}.")

    def array(self) -> list[Any] | None:
        """Short number lists come back as lists; strings, nested and long lists are skipped (None)."""
        kind = self.unpack("<I")
        count = self.unpack("<Q")
        if kind in _SCALAR:
            fmt = _SCALAR[kind]
            size = struct.calcsize(fmt)
            if size * count > MAX_ARRAY_BYTES:
                raise GgufError(f"Liste mit {count} Einträgen – Datei beschädigt?")
            self.need(size * count)
            values = None
            if count <= KEEP_LIST:
                values = list(struct.unpack_from(f"<{count}{fmt[1:]}", self.buf, self.pos))
            self.pos += size * count
            return values
        if count > MAX_STRINGS:
            raise GgufError(f"Liste mit {count} Einträgen – Datei beschädigt?")
        if kind == STRING:
            for _ in range(count):
                self.string(keep=False)
            return None
        if kind == ARRAY:
            for _ in range(count):
                self.array()
            return None
        raise GgufError(f"Unbekannter Listentyp {kind} an Position {self.pos}.")


def parse_header(buf: bytes) -> Header:
    """Whole key/value section in `buf` -> Header. Raises NeedMore (buffer too short) or GgufError."""
    r = _Reader(buf)
    r.need(4)
    if buf[:4] != MAGIC:
        raise GgufError("Keine GGUF-Datei (Kennung fehlt) – dieses Format kann der Katalog nicht lesen.")
    r.pos = 4
    version = r.unpack("<I")
    if version == 1:
        raise GgufError("GGUF Version 1 (sehr alt) wird nicht unterstützt.")
    if version not in (2, 3):
        if version > 0xFFFF:
            raise GgufError("GGUF in Big-Endian-Byte-Reihenfolge wird nicht unterstützt.")
        raise GgufError(f"Unbekannte GGUF-Version {version}.")
    tensor_count = r.unpack("<Q")
    kv_count = r.unpack("<Q")
    if kv_count > MAX_KEYS:
        raise GgufError(f"{kv_count} Metadaten-Einträge – Datei beschädigt?")
    for _ in range(kv_count):
        key = r.string()
        kind = r.unpack("<I")
        value = r.value(kind)
        if value is not None:
            r.kv[key] = value                                    # type: ignore[index]
    return Header(version=version, tensor_count=tensor_count, kv_count=kv_count, kv=r.kv, end=r.pos)


def partial_header(buf: bytes, more: NeedMore) -> Header:
    """The size limit was reached inside the header: keep what was complete (version and counts re-read)."""
    version, tensor_count, kv_count = struct.unpack_from("<IQQ", buf, 4) if len(buf) >= 24 else (0, 0, 0)
    return Header(version=version, tensor_count=tensor_count, kv_count=kv_count, kv=dict(more.kv),
                  end=len(buf), complete=False)


# ---- writing (tests and the synthetic library samples) ---------------------------------------------------

def _w_string(text: str) -> bytes:
    data = text.encode("utf-8")
    return struct.pack("<Q", len(data)) + data


def _w_value(value: Any) -> tuple[int, bytes]:
    if isinstance(value, bool):
        return 7, struct.pack("<?", value)
    if isinstance(value, int):
        return (4, struct.pack("<I", value)) if 0 <= value < 2 ** 32 else (11, struct.pack("<q", value))
    if isinstance(value, float):
        return 6, struct.pack("<f", value)
    if isinstance(value, str):
        return STRING, _w_string(value)
    if isinstance(value, list):
        if all(isinstance(v, bool) for v in value) and value:
            return ARRAY, struct.pack("<IQ", 7, len(value)) + struct.pack(f"<{len(value)}?", *value)
        if all(isinstance(v, str) for v in value) and value:
            return ARRAY, struct.pack("<IQ", STRING, len(value)) + b"".join(_w_string(v) for v in value)
        if all(isinstance(v, float) for v in value) and value:
            return ARRAY, struct.pack("<IQ", 6, len(value)) + struct.pack(f"<{len(value)}f", *value)
        ints = [int(v) for v in value]
        return ARRAY, struct.pack("<IQ", 5, len(ints)) + struct.pack(f"<{len(ints)}i", *ints)
    raise TypeError(f"cannot write {type(value).__name__} as a GGUF value")


def write_header(kv: dict[str, Any], tensor_count: int = 0, version: int = 3) -> bytes:
    """A GGUF key/value section (no tensors) - what the start of a real model file looks like to the parser."""
    out = [MAGIC, struct.pack("<IQQ", version, tensor_count, len(kv))]
    for key, value in kv.items():
        kind, data = _w_value(value)
        out += [_w_string(key), struct.pack("<I", kind), data]
    return b"".join(out)
