"""HTTP endpoints of the dashboard module, mounted under /api/v1/dashboard.

Live updates come via SSE: GET /api/v1/stream?topics=dashboard -> topic "dashboard.snapshot",
data = the same DashboardSnapshot as below, every fast tick (default 2 s).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from control_center.modules.dashboard.schemas import DashboardSnapshot
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
