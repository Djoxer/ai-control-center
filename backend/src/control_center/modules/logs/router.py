"""HTTP endpoints of the logs module, mounted under /api/v1/logs."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from control_center.modules.logs.reader import EntryFilter
from control_center.modules.logs.schemas import LogPage, LogSourceInfo
from control_center.modules.logs.service import LogsService, UnknownSource

router = APIRouter()

Level = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def service(request: Request) -> LogsService:
    svc = request.app.state.ctx.service("logs.service")
    if svc is None:
        # module mounted but its startup failed -> say so instead of a 500 with a traceback
        raise HTTPException(503, "logs module is not running")
    return svc


@router.get("/sources", response_model=list[LogSourceInfo])
async def sources(request: Request) -> list[LogSourceInfo]:
    return await service(request).list_sources()


@router.get("/entries", response_model=LogPage)
async def entries(
    request: Request,
    source: str = Query(description="source key from /sources, e.g. control-center"),
    level: Level | None = Query(None, description="minimum level; hides lines without a level"),
    logger: str | None = Query(None, description="logger prefix, e.g. control_center.modules.logs"),
    q: str | None = Query(None, min_length=1, description="case-insensitive text search"),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(200, ge=1),
    cursor: str | None = Query(None, description="nextCursor of the previous page"),
) -> LogPage:
    flt = EntryFilter(min_level=level, logger_prefix=logger, query=q, since=since, until=until)
    try:
        return await service(request).page(source, flt, limit, cursor)
    except UnknownSource:
        raise HTTPException(404, f"unknown log source: {source}") from None
    except ValueError as exc:                      # malformed or outdated cursor
        raise HTTPException(400, str(exc)) from None
