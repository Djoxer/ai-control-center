"""HTTP reachability check for neighbouring services (MCP server, OpenWebUI, Qdrant ...).

"Up" means: the service answered with any status below 500. A 405 from an MCP endpoint that
only accepts POST is a perfectly alive server. Only the status line is read: some endpoints
(MCP over GET, SSE) would otherwise stream forever.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from control_center.adapters.common import describe


@dataclass(frozen=True)
class ProbeResult:
    up: bool
    http_status: int | None
    latency_ms: float | None
    error: str | None


async def probe(client: httpx.AsyncClient, url: str) -> ProbeResult:
    start = time.perf_counter()
    try:
        async with client.stream("GET", url) as r:          # headers only, body is never read
            status = r.status_code
    except httpx.HTTPError as exc:
        return ProbeResult(up=False, http_status=None, latency_ms=None, error=describe(exc))
    ms = round((time.perf_counter() - start) * 1000, 1)
    up = status < 500
    return ProbeResult(up=up, http_status=status, latency_ms=ms, error=None if up else f"HTTP {status}")
