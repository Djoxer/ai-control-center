"""HTTP endpoints of the dashboard module, mounted under /api/v1/dashboard.

Live updates come via SSE: GET /api/v1/stream?topics=dashboard
  - "dashboard.snapshot": the same DashboardSnapshot as GET /snapshot, every fast tick (default 2 s)
  - "dashboard.event":    one DashboardEvent, the moment it is detected
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from control_center.modules.dashboard.schemas import (
    DashboardHistory, DashboardSnapshot, EventPage, HistoryMetric, HistoryRange,
)
from control_center.modules.dashboard.service import DashboardService

router = APIRouter()


def service(request: Request) -> DashboardService:
    svc = request.app.state.ctx.service("dashboard.service")
    if svc is None:
        raise HTTPException(503, "dashboard module is not running")
    return svc


@router.get("/snapshot", response_model=DashboardSnapshot)
async def snapshot(request: Request) -> DashboardSnapshot:
    snap = await service(request).snapshot()
    if snap is None:
        raise HTTPException(503, "no snapshot yet")     # first tick still running after 5 s
    return snap


@router.get("/history", response_model=DashboardHistory)
async def history(
    request: Request,
    span: HistoryRange = Query("1h", alias="range", description="time span back from now"),
    metrics: list[HistoryMetric] = Query(default_factory=list, description="empty = all metrics"),
) -> DashboardHistory:
    return await service(request).history(span, metrics)


@router.get("/events", response_model=EventPage)
async def events(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    before: int | None = Query(None, description="nextBefore of the previous page"),
) -> EventPage:
    return await service(request).events(limit, before)
