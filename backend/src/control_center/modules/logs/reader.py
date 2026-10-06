"""File-level log reading: parse lines, read files backwards, page across rotated files.

Pure and synchronous on purpose: easy to test, called via asyncio.to_thread by the service.
Every function opens, reads and closes - no handle stays open (Windows rotation would fail).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from control_center.core.tail import resolve_files  # noqa: F401  (moved to core, re-exported)
from control_center.modules.logs.schemas import LogEntry
from control_center.modules.logs.settings import LogFormat

CHUNK = 64 * 1024

LEVEL_RANK = {"TRACE": 5, "DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
LEVEL_ALIASES = {"WARN": "WARNING", "FATAL": "CRITICAL", "ERR": "ERROR"}

# Go slog text format used by Ollama: time=... level=INFO source=server.go:12 msg="Listening on ..."
KV = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')


def normalize_level(raw: str | None) -> str | None:
    if not raw:
        return None
    up = raw.strip().upper()
    up = LEVEL_ALIASES.get(up, up)
    return up if up in LEVEL_RANK else None


def parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)   # naive -> assume UTC, never compare naive/aware


def parse_line(line: str, fmt: LogFormat, source: str, file: str) -> LogEntry:
    """Never raises: a line that does not fit the format becomes a raw entry with msg = line."""
    if fmt == "json":
        try:
            d = json.loads(line)
            if isinstance(d, dict):
                return LogEntry(source=source, file=file, ts=parse_ts(d.get("ts")),
                                level=normalize_level(d.get("level")), logger=d.get("logger"),
                                msg=str(d.get("msg", "")), exc=d.get("exc"))
        except json.JSONDecodeError:
            pass
    elif line.startswith("time="):
        kv = {k: (v[1:-1].replace('\\"', '"') if v.startswith('"') else v) for k, v in KV.findall(line)}
        return LogEntry(source=source, file=file, ts=parse_ts(kv.get("time")),
                        level=normalize_level(kv.get("level")), logger=kv.get("source"),
                        msg=kv.get("msg", line))
    # llama.cpp output, [GIN] access lines, half-written lines, ...
    return LogEntry(source=source, file=file, ts=None, level=None, logger=None, msg=line)


def _end_of_last_complete_line(f, size: int) -> int:
    """Offset just after the last newline; 0 if the file has none."""
    pos = size
    while pos > 0:
        step = min(CHUNK, pos)
        f.seek(pos - step)
        nl = f.read(step).rfind(b"\n")
        if nl >= 0:
            return pos - step + nl + 1
        pos -= step
    return 0


def iter_lines_reverse(path: Path, end: int | None = None) -> Iterator[tuple[int, str]]:
    """Yield (end_offset, line) from the end of the file towards the start, without loading it whole.

    end_offset is the byte offset right after the line's content: iterating again with
    end=end_offset starts with exactly this line (that is what the paging cursor stores).
    With end=None an unfinished last line (no newline yet, the writer is mid-write) is skipped;
    the live tail delivers it once it is complete.
    """
    with path.open("rb") as f:
        size = f.seek(0, os.SEEK_END)
        pos = _end_of_last_complete_line(f, size) if end is None else min(end, size)
        tail = b""                                         # bytes of a line cut by the chunk border
        tail_end = pos
        while pos > 0:
            step = min(CHUNK, pos)
            pos -= step
            f.seek(pos)
            block = f.read(step) + tail
            parts = block.split(b"\n")
            # parts[-1] is what follows the last newline of the block (b"" if the block ends with \n)
            line_end = pos + len(block)                    # offset after the block (incl. carried tail)
            for raw in reversed(parts[1:]):
                text = raw.rstrip(b"\r").decode("utf-8", errors="replace")   # CRLF from Windows writers
                if text.strip():
                    yield line_end, text
                line_end -= len(raw) + 1                   # step over the line and its leading newline
            tail = parts[0]                                # first part may continue in the previous chunk
            tail_end = line_end
        if tail.strip():
            yield tail_end, tail.rstrip(b"\r").decode("utf-8", errors="replace")


@dataclass(frozen=True)
class EntryFilter:
    min_level: str | None = None     # normalized level name
    logger_prefix: str | None = None
    exclude_prefixes: tuple[str, ...] = ()   # hide these loggers, e.g. uvicorn.access (own API noise)
    query: str | None = None         # case-insensitive substring over msg, exc and logger
    since: datetime | None = None
    until: datetime | None = None

    def matches(self, e: LogEntry) -> bool:
        if self.min_level:
            # entries without a level (raw text) are hidden by a level filter; text search still finds them
            if e.level is None or LEVEL_RANK[e.level] < LEVEL_RANK[self.min_level]:
                return False
        if self.logger_prefix and not (e.logger or "").startswith(self.logger_prefix):
            return False
        if self.exclude_prefixes and (e.logger or "").startswith(self.exclude_prefixes):
            return False
        if self.since or self.until:
            if e.ts is None:
                return False
            if self.since and e.ts < self.since:
                return False
            if self.until and e.ts > self.until:
                return False
        if self.query:
            hay = f"{e.msg}\n{e.exc or ''}\n{e.logger or ''}".lower()
            if self.query.lower() not in hay:
                return False
        return True


def encode_cursor(file: str, offset: int) -> str:
    return f"{file}|{offset}"


def decode_cursor(cursor: str) -> tuple[str, int]:
    file, _, offset = cursor.rpartition("|")
    if not file or not offset.isdigit():
        raise ValueError(f"invalid cursor: {cursor!r}")
    return file, int(offset)


def read_page(files: list[Path], fmt: LogFormat, source: str, flt: EntryFilter,
              limit: int, cursor: str | None = None) -> tuple[list[LogEntry], str | None]:
    """Newest-first page over all files of a source. Returns (entries, cursor for the next older page)."""
    start_file, start_offset = (None, None) if cursor is None else decode_cursor(cursor)
    started = start_file is None
    out: list[LogEntry] = []
    for path in files:
        if not started:
            if path.name != start_file:
                continue                     # skip newer files already shown on previous pages
            started = True
            end = start_offset
        else:
            end = None
        for line_end, line in iter_lines_reverse(path, end):
            entry = parse_line(line, fmt, source, path.name)
            if flt.matches(entry):
                if len(out) == limit:
                    # one more match exists -> next page starts with exactly this line
                    return out, encode_cursor(path.name, line_end)
                out.append(entry)
    if not started:
        raise ValueError("cursor file no longer exists (rotated or deleted) - reload from the newest page")
    return out, None
