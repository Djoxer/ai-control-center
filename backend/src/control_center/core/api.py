"""Core endpoints the Angular shell needs before any module exists: health, module list, live stream."""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from control_center import __version__
from control_center.core.context import AppContext
from control_center.core.schemas import CamelModel

router = APIRouter(prefix="/api/v1", tags=["core"])

HEARTBEAT_SECONDS = 15              # keeps proxies and the browser from closing an idle stream


class ModuleInfo(CamelModel):
    key: str
    title: str
    icon: str
    order: int
    state: str
    error: str | None = None


class HealthInfo(CamelModel):
    status: str                     # "ok" | "degraded"
    version: str
    database: bool
    modules: list[ModuleInfo]


def topic_matches(topic: str, prefixes: list[str]) -> bool:
    """Empty filter = everything. "dashboard" matches "dashboard.sample" but not "dashboards.x"."""
    return not prefixes or any(topic == p or topic.startswith(p + ".") for p in prefixes)


def ctx_of(request: Request) -> AppContext:
    return request.app.state.ctx


def module_infos(ctx: AppContext) -> list[ModuleInfo]:
    items = sorted(ctx.modules.values(), key=lambda m: (m.order, m.key))
    return [ModuleInfo(**vars(m)) for m in items]


@router.get("/health", response_model=HealthInfo)
async def health(request: Request) -> HealthInfo:
    ctx = ctx_of(request)
    db_ok = await ctx.db.ping()
    failed = any(m.state == "failed" for m in ctx.modules.values())
    return HealthInfo(
        status="ok" if db_ok and not failed else "degraded",
        version=__version__,
        database=db_ok,
        modules=module_infos(ctx),
    )


@router.get("/meta/modules", response_model=list[ModuleInfo])
async def modules(request: Request) -> list[ModuleInfo]:
    """The Angular shell builds its menu from this - disabled or failed modules show up greyed out."""
    return module_infos(ctx_of(request))


@router.get("/stream")
async def stream(
    request: Request,
    topics: list[str] = Query(default_factory=list, description="topic prefixes, e.g. dashboard"),
) -> StreamingResponse:
    ctx = ctx_of(request)

    async def body() -> AsyncIterator[str]:
        with ctx.events.subscription() as queue:
            yield ": connected\n\n"                         # comment line, flushes headers immediately
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if not topic_matches(ev.topic, topics):
                    continue
                # "event:" lets Angular use addEventListener('dashboard.sample', ...)
                yield f"event: {ev.topic}\ndata: {json.dumps(ev.data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
