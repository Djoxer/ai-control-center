"""Logs module settings: [modules.logs] in control-center.toml (runtime editing comes with the settings module)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

LogFormat = Literal["json", "text"]      # json = our own JSON lines, text = anything else (Ollama, ...)


class LogSourceConfig(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")   # used in URLs and SSE topics
    title: str
    format: LogFormat = "text"
    # glob patterns; %VARS% / $VARS are expanded. Rotated files must match too (server-1.log, x.log.1)
    paths: list[str]


class LogsSettings(BaseModel):
    # our own log is always added as source "control-center"; these are the extra sources
    sources: list[LogSourceConfig] = Field(default_factory=lambda: [
        LogSourceConfig(
            key="ollama", title="Ollama", format="text",
            paths=["%LOCALAPPDATA%/Ollama/server*.log"],   # Windows default of the Ollama app
        ),
    ])
    tail_interval_s: float = Field(1.0, gt=0, le=60)   # how often the live tail looks for new lines
    max_page_size: int = Field(1000, ge=10, le=10_000) # upper bound for ?limit=
