"""API shapes of the logs module (camelCase in JSON via CamelModel)."""
from __future__ import annotations

from datetime import datetime

from control_center.core.schemas import CamelModel


class LogFileInfo(CamelModel):
    name: str
    size_bytes: int
    modified: datetime


class LogSourceInfo(CamelModel):
    key: str
    title: str
    format: str
    available: bool                 # False = no file matches (e.g. Ollama log on the dev PC)
    files: list[LogFileInfo]        # newest first


class LogEntry(CamelModel):
    source: str
    file: str
    ts: datetime | None             # None = line without a parsable timestamp (raw text lines)
    level: str | None               # normalized: DEBUG, INFO, WARNING, ERROR, CRITICAL
    logger: str | None              # e.g. control_center.modules.logs; Ollama: source=server.go:123
    msg: str
    exc: str | None = None          # traceback, only in JSON lines


class LogPage(CamelModel):
    entries: list[LogEntry]         # newest first
    next_cursor: str | None         # pass back as ?cursor= to load older entries; None = end reached
