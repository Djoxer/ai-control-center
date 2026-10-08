"""HTTP endpoints of the catalog, mounted under /api/v1/catalog.

Reading is free; a refresh only reads from Ollama (no model is loaded), but it is still a POST with
the cross-site guard: it costs Ollama a few /api/show calls and changes what the page shows.
A test run loads a model and unloads the current one - POST with the guard, refused by the preflight
when the estimate says "Teil-Offload", and only with confirm=true when it says "knapp".
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from control_center.core.guards import same_origin
from control_center.modules.catalog.schemas import (
    BenchRequest, BenchStatus, CatalogOverview, Preflight, RefreshRequest,
)
from control_center.modules.catalog.service import (
    Busy, CatalogService, NeedsConfirm, OllamaDown, Refused, UnknownModel,
)

router = APIRouter()
write = [Depends(same_origin)]


def service(request: Request) -> CatalogService:
    svc = request.app.state.ctx.service("catalog.service")
    if svc is None:
        raise HTTPException(503, "Katalog-Modul läuft nicht")
    return svc


@router.get("/overview", response_model=CatalogOverview)
async def overview(request: Request) -> CatalogOverview:
    """Installed models grouped by origin, context, VRAM estimate and observations. Ollama down = last known
    inventory with ollama.online false."""
    return await service(request).overview()


@router.post("/refresh", response_model=CatalogOverview, dependencies=write,
             responses={404: {"description": "model not installed"}, 503: {"description": "Ollama not reachable"}})
async def refresh(request: Request, body: RefreshRequest) -> CatalogOverview:
    """Reads /api/tags and /api/show again - for one model (name) or all of them - and returns the result."""
    svc = service(request)
    try:
        await svc.refresh(body.name, force=True)
    except OllamaDown as exc:
        raise HTTPException(503, str(exc)) from None
    except UnknownModel:
        raise HTTPException(404, f"Modell {body.name} ist nicht installiert") from None
    return await svc.overview()


@router.get("/preflight", response_model=Preflight, responses={404: {"description": "model not installed"}})
async def preflight(request: Request, name: str = Query(description="installed model name"),
                    num_ctx: int | None = Query(None, ge=256, le=4_194_304,
                                                description="context to check; empty = the effective one")) -> Preflight:
    """What loading the model with this context would cost on this GPU, and whether a test run may start."""
    try:
        return await service(request).preflight(name, num_ctx)
    except UnknownModel:
        raise HTTPException(404, f"Modell {name} ist nicht installiert") from None


@router.get("/bench", response_model=list[BenchStatus])
async def benches(request: Request, name: str | None = Query(None, description="only this model")) -> list[BenchStatus]:
    """Test runs, the running one first, then the finished ones, newest first."""
    return service(request).bench_list(name)


@router.post("/bench", response_model=BenchStatus, status_code=status.HTTP_202_ACCEPTED, dependencies=write,
             responses={404: {"description": "model not installed"},
                        409: {"description": "a run is going on, the preflight refuses, or confirm is missing"}})
async def start_bench(request: Request, body: BenchRequest) -> BenchStatus:
    """Starts a test run in the background; progress and result follow via SSE catalog.bench."""
    try:
        return await service(request).start_bench(body.name, body.num_ctx, body.confirm)
    except UnknownModel:
        raise HTTPException(404, f"Modell {body.name} ist nicht installiert") from None
    except (Busy, Refused, NeedsConfirm) as exc:
        raise HTTPException(409, str(exc)) from None
