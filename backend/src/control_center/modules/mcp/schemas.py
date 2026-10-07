"""API shapes of the MCP module (camelCase in JSON via CamelModel)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from control_center.core.schemas import CamelModel

# stopped  = not running, nobody asked for it (or stopped by a user)
# starting = process runs, its port is not open yet
# running  = process runs (and its port is open, if a URL is configured)
# stopping = stop requested, waiting for the process tree to end
# crashed  = ended without being asked to (exit code says how)
ServerState = Literal["stopped", "starting", "running", "stopping", "crashed"]


class McpToolParam(CamelModel):
    name: str
    type: str                       # JSON-schema type, "string | null" for unions, "any" if unknown
    required: bool
    description: str | None = None
    default: str | None = None      # JSON text of the default value, e.g. "8"


class McpTool(CamelModel):
    name: str
    title: str | None = None
    description: str | None = None
    params: list[McpToolParam]


class McpToolList(CamelModel):
    fetched_at: datetime
    server_name: str | None = None          # what the server calls itself (initialize handshake)
    server_version: str | None = None       # its SDK/app version, e.g. "1.30.0"
    protocol_version: str | None = None     # negotiated MCP protocol version
    tools: list[McpTool]
    error: str | None = None                # German message; tools is empty then


class PortInfo(CamelModel):
    port: int
    open: bool | None                       # None = not checked yet
    pid: int | None = None                  # process listening on it, when the OS tells
    process: str | None = None              # its name, e.g. python.exe
    managed: bool = False                   # the listener is the server started from here (or its child)
    checked_at: datetime | None = None


class McpServerStatus(CamelModel):
    revision: int                           # +1 per change: the UI keeps the newest of HTTP answer and SSE
    as_of: datetime                         # server clock when this was built: uptime without clock skew
    key: str
    title: str
    url: str | None
    command_line: str                       # resolved program + arguments, as one line
    cwd: str
    autostart: bool
    restart_on_crash: bool
    state: ServerState
    pid: int | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    exit_code: int | None = None
    last_error: str | None = None           # German, shown as-is
    restarts: int = 0                       # automatic restarts within the restart window
    recent_output: list[str] = []           # last lines the process printed (crash diagnosis)
    port: PortInfo | None = None
    tools: McpToolList | None = None        # last tools/list result, None = never fetched
    log_source: str                         # key on the logs page, e.g. "mcp-bent-rag"
