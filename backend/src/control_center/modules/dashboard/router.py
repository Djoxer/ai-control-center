"""HTTP endpoints of the dashboard module, mounted under /api/v1/dashboard.

Live updates come via SSE: GET /api/v1/stream?topics=dashboard
  - "dashboard.snapshot": the same DashboardSnapshot as GET /snapshot, every fast tick (default 2 s)
  - "dashboard.event":    one DashboardEvent, the moment it is detected
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from control_center import __version__
from control_center.core.schemas import CamelModel
from control_center.modules.dashboard import export as ex
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


# ---- export (AI-friendly Markdown / JSON) --------------------------------------------------------

class ExportDocument(CamelModel):
    format: ex.ExportFormat
    detail: ex.ExportDetail
    filename: str                   # suggestion for "save as", e.g. acc-export-2026-10-07-1403.md
    media_type: str
    tokens: int                     # rough estimate: characters / 4
    content: str                    # the Markdown text or the JSON document as text


@dataclass
class ExportOptions:
    format: ex.ExportFormat
    detail: ex.ExportDetail
    parts: tuple[ex.ExportPart, ...]
    span: HistoryRange
    anonymize: bool


def export_options(
    format: ex.ExportFormat = Query("md", description="md = Markdown with YAML header, json"),
    detail: ex.ExportDetail = Query("short", description="short = overview, full = everything"),
    parts: list[ex.ExportPart] = Query(default_factory=list, description="empty = snapshot, events, history"),
    span: HistoryRange = Query("1h", alias="range", description="history time span"),
    anonymize: bool = Query(True, description="replace host names, IPs and user folders, drop command lines"),
) -> ExportOptions:
    chosen = tuple(p for p in ex.ALL_PARTS if p in parts) or ex.ALL_PARTS     # fixed order, no duplicates
    return ExportOptions(format=format, detail=detail, parts=chosen, span=span, anonymize=anonymize)


async def build_export(request: Request, opt: ExportOptions) -> ExportDocument:
    svc = service(request)
    settings = request.app.state.ctx.settings
    now = datetime.now(timezone.utc)
    data = ex.ExportData(
        generated=now, version=__version__, node=svc.node_id, detail=opt.detail, parts=opt.parts,
        anonymized=opt.anonymize,
        snapshot=await svc.snapshot() if "snapshot" in opt.parts else None,
        events=await svc.events(ex.EVENT_LIMIT[opt.detail], None) if "events" in opt.parts else None,
        history=await svc.history(opt.span, []) if "history" in opt.parts else None,
    )
    names = ex.host_names(svc.node_id, settings.adapters.ai_host, settings.adapters.expand(settings.adapters.ollama_url),
                          *(settings.adapters.expand(p.url) for p in svc.cfg.probes))
    data = ex.prepare(data, ex.Anonymizer(opt.anonymize, names))
    if opt.format == "md":
        content, media = ex.to_markdown(data), "text/markdown; charset=utf-8"
    else:
        content, media = json.dumps(ex.to_json(data), ensure_ascii=False, indent=2) + "\n", "application/json"
    stamp = now.astimezone().strftime("%Y-%m-%d-%H%M")
    return ExportDocument(format=opt.format, detail=opt.detail, filename=f"acc-export-{stamp}.{opt.format}",
                          media_type=media, tokens=ex.estimate_tokens(content), content=content)


@router.get("/export", response_model=ExportDocument)
async def export(request: Request, opt: Annotated[ExportOptions, Depends(export_options)]) -> ExportDocument:
    """Export for the UI: the text plus file name and token estimate."""
    return await build_export(request, opt)


# Not in OpenAPI: plain text for curl, OpenCode or a browser tab - the generated client would only
# wrap the same content that /export already returns.
@router.get("/export/raw", include_in_schema=False)
async def export_raw(request: Request, opt: Annotated[ExportOptions, Depends(export_options)]) -> Response:
    doc = await build_export(request, opt)
    return Response(doc.content, media_type=doc.media_type,
                    headers={"Content-Disposition": f'inline; filename="{doc.filename}"'})
