"""MCP module settings: [modules.mcp] in control-center.toml.

The list of servers is the security boundary: the API only takes a server key, never a command,
so nothing can be started that is not written in this file on the AI box.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

SERVER_KEY_PATTERN = r"^[a-z0-9][a-z0-9-]*$"     # URL part, log source "mcp-<key>"


class McpServerConfig(BaseModel):
    key: str = Field(pattern=SERVER_KEY_PATTERN, max_length=40)
    title: str
    # program + arguments, started WITHOUT a shell (no quoting trouble, no injection).
    # Placeholders: "{python}" = the interpreter running the control center; %VARS%, $VARS and ~ expand.
    command: list[str] = Field(min_length=1)
    cwd: str | None = None              # working directory; relative = next to control-center.toml
    url: str | None = None              # MCP endpoint, e.g. http://127.0.0.1:8000/mcp -> port check + tool list
    autostart: bool = False             # start together with the control center
    restart_on_crash: bool = True       # automatic restart, limited by max_restarts / restart_window_s
    env: dict[str, str] = Field(default_factory=dict)   # extra environment variables for this process

    @field_validator("command")
    @classmethod
    def _no_empty_program(cls, value: list[str]) -> list[str]:
        if not value[0].strip():
            raise ValueError("command[0] (the program) must not be empty")
        return value

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"url must look like http://127.0.0.1:8000/mcp, got {value!r}")
        _ = parts.port                  # raises ValueError for a port like :99999
        return value

    @property
    def port(self) -> int | None:
        """TCP port of the endpoint - what 'is it up?' and 'who blocks it?' are checked against."""
        if self.url is None:
            return None
        parts = urlsplit(self.url)
        return parts.port or (443 if parts.scheme == "https" else 80)


class McpSettings(BaseModel):
    servers: list[McpServerConfig] = Field(default_factory=list)

    start_timeout_s: float = Field(30, gt=0, le=600)     # until the port must be open ("uv run" may sync first)
    stop_timeout_s: float = Field(5, gt=0, le=60)        # polite stop, then the process tree is killed
    max_restarts: int = Field(3, ge=0, le=20)            # automatic restarts after crashes ...
    restart_window_s: float = Field(300, ge=10, le=3600) # ... within this many seconds, then give up
    restart_backoff_s: float = Field(2, ge=0, le=60)     # wait before restart n: backoff * 2^n (2, 4, 8 s)
    check_interval_s: float = Field(10, ge=1, le=600)    # port check of every server (who listens?)
    tools_timeout_s: float = Field(10, gt=0, le=120)     # MCP tools/list round trip
    poll_interval_s: float = Field(1.0, ge=0.02, le=10)  # exit/readiness detection; small values for tests
    log_max_bytes: int = Field(5 * 1024 * 1024, ge=64 * 1024)   # per server log file, then rotated
    log_backup_count: int = Field(3, ge=0, le=20)

    @model_validator(mode="after")
    def _unique(self) -> "McpSettings":
        keys = [s.key for s in self.servers]
        if len(keys) != len(set(keys)):
            raise ValueError("server keys must be unique")
        ports = [s.port for s in self.servers if s.port is not None]
        if len(ports) != len(set(ports)):
            raise ValueError("two servers use the same port - only one of them could ever run")
        return self
