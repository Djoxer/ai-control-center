"""HTTP endpoints of the catalog, mounted under /api/v1/catalog.

Reading is free; a refresh only reads from Ollama (no model is loaded), but it is still a POST with
the cross-site guard: it costs Ollama a few /api/show calls and changes what the page shows.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from control_center.core.guards import same_origin
from control_center.modules.catalog.schemas import CatalogOverview, RefreshRequest
from control_center.modules.catalog.service import CatalogService, OllamaDown, UnknownModel

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
