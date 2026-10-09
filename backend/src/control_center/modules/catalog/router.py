"""HTTP endpoints of the catalog, mounted under /api/v1/catalog.

Reading is free; a refresh only reads from Ollama (no model is loaded), but it is still a POST with
the cross-site guard: it costs Ollama a few /api/show calls and changes what the page shows.
A test run loads a model and unloads the current one - POST with the guard, refused by the preflight
when the estimate says "Teil-Offload", and only with confirm=true when it says "knapp".
Usage tags are team notes about a model (OpenCode, OpenWebUI, ...) - POST with the guard as well.
A candidate check makes the SERVER ask Ollama's registry (only the hosts of [adapters] library_hosts) -
POST with the guard; the result stays in the overview until it is removed (DELETE with the guard).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from control_center.adapters.library import BadReference, LibraryNotFound, LibraryUnavailable
from control_center.core.guards import same_origin
from control_center.modules.catalog.schemas import (
    BenchRequest, BenchStatus, CandidateRequest, CandidateResult, CatalogOverview, Preflight, RefreshRequest,
    UsageRequest,
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


@router.post("/usage", response_model=CatalogOverview, dependencies=write,
             responses={404: {"description": "model not installed"}})
async def set_usage(request: Request, body: UsageRequest) -> CatalogOverview:
    """What the team uses a model for (tags + short note). Empty tags and note = remove the entry."""
    try:
        return await service(request).set_usage(body.name, list(body.tags), body.note)
    except UnknownModel as exc:
        raise HTTPException(404, f"Modell nicht installiert: {body.name}") from exc


@router.post("/candidates", response_model=CandidateResult, dependencies=write,
             responses={400: {"description": "not a model name, or a registry that is not allowed"},
                        404: {"description": "the registry does not know the model or tag"},
                        502: {"description": "registry not reachable or unreadable answer"}})
async def check_candidate(request: Request, body: CandidateRequest) -> CandidateResult:
    """Reads manifest, small files and the GGUF header of a model in Ollama's library (nothing is pulled) and
    keeps the facts. The overview then shows it with estimate, verdict, max. context and OpenCode prognosis."""
    svc = service(request)
    try:
        name = await svc.check_candidate(body.name)
    except BadReference as exc:
        raise HTTPException(400, str(exc)) from None
    except LibraryNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except LibraryUnavailable as exc:
        raise HTTPException(502, f"Registry nicht lesbar: {exc}") from None
    return CandidateResult(name=name, overview=await svc.overview())


@router.delete("/candidates", response_model=CatalogOverview, dependencies=write,
               responses={404: {"description": "no such candidate"}})
async def forget_candidate(request: Request, name: str = Query(description="candidate name")) -> CatalogOverview:
    svc = service(request)
    try:
        await svc.forget_candidate(name)
    except UnknownModel:
        raise HTTPException(404, f"Kein Kandidat {name}") from None
    return await svc.overview()
