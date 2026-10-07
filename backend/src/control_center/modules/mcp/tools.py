"""Ask a running MCP server which tools it offers (MCP 'tools/list' over Streamable HTTP).

Uses the official SDK client: it negotiates the protocol version with old and new servers alike
(tested against mcp 1.9 and 1.30 servers - the existing mcp_server.py runs on 1.x) and handles
both answer styles of Streamable HTTP (plain JSON or an SSE stream).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from control_center.modules.mcp.schemas import McpTool, McpToolList, McpToolParam

log = logging.getLogger("control_center.modules.mcp")

MAX_PAGES = 20                      # a server that paginates forever must not hang the request


def describe_params(schema: dict[str, Any] | None) -> list[McpToolParam]:
    """Flatten a tool's JSON-schema input into rows a table can show. Unknown shapes degrade to 'any'."""
    if not isinstance(schema, dict):
        return []
    props = schema.get("properties")
    if not isinstance(props, dict):
        return []
    required = set(schema.get("required") or [])
    out = []
    for name, spec in props.items():
        spec = spec if isinstance(spec, dict) else {}
        default = json.dumps(spec["default"], ensure_ascii=False) if "default" in spec else None
        out.append(McpToolParam(name=name, type=_type_of(spec), required=name in required,
                                description=spec.get("description"), default=default))
    return out


def _type_of(spec: dict[str, Any], depth: int = 0) -> str:
    """'string', 'integer | null' (pydantic's Optional), 'string[]'. Depth-limited: schemas can nest."""
    if depth > 3:
        return "any"
    t = spec.get("type")
    if t == "array":
        items = spec.get("items")
        return f"{_type_of(items, depth + 1) if isinstance(items, dict) else 'any'}[]"
    if isinstance(t, str):
        return t
    if isinstance(t, list):
        return " | ".join(str(x) for x in t)
    variants = spec.get("anyOf") or spec.get("oneOf")
    if isinstance(variants, list) and variants:
        return " | ".join(_type_of(v, depth + 1) if isinstance(v, dict) else "any" for v in variants)
    return "any"


async def fetch_tools(url: str, timeout_s: float) -> McpToolList:
    """Never raises: failures come back as McpToolList.error (German), the tool list stays empty."""
    now = datetime.now(timezone.utc)
    try:
        # imported here: the process management works even if the SDK is missing (uv sync forgotten)
        import httpx2
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
    except ImportError as exc:                               # pragma: no cover - depends on the install
        return McpToolList(fetched_at=now, tools=[], error=f"MCP-SDK fehlt ({exc.name}) – im Ordner backend: uv sync")
    try:
        async with asyncio.timeout(timeout_s):
            async with httpx2.AsyncClient(timeout=httpx2.Timeout(timeout_s)) as http:
                transport = streamable_http_client(url, http_client=http)
                # cache=None: we want the server's answer now, not a TTL-cached one
                async with Client(transport, cache=None, read_timeout_seconds=timeout_s) as client:
                    tools = []
                    cursor: str | None = None
                    for _ in range(MAX_PAGES):
                        page = await client.list_tools(cursor=cursor)
                        tools.extend(page.tools)
                        cursor = page.next_cursor
                        if not cursor:
                            break
                    info = client.server_info
                    return McpToolList(
                        fetched_at=now,
                        server_name=info.name if info else None,
                        server_version=info.version if info else None,
                        protocol_version=client.protocol_version,
                        tools=[McpTool(name=t.name, title=t.title, description=t.description,
                                       params=describe_params(t.input_schema)) for t in tools],
                    )
    except TimeoutError:
        return McpToolList(fetched_at=now, tools=[], error=f"Keine Antwort innerhalb von {timeout_s:g} s")
    except Exception as exc:                                 # connection refused, HTTP 4xx/5xx, protocol errors
        log.info("tools/list on %s failed: %r", url, exc)
        return McpToolList(fetched_at=now, tools=[], error=f"Abfrage fehlgeschlagen: {_reason(exc)}")


def _reason(exc: BaseException) -> str:
    """Exception groups from anyio hide the real cause one level down."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__
