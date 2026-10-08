"""HTTP endpoints of the RAG module, mounted under /api/v1/rag.

Write actions (reindex, cancel, delete a collection) are guarded against cross-site requests
(core/guards.py) and refused while Qdrant runs on another machine (see RagService.write_access).
The test search is a POST too: it loads the embedding model in Ollama, which unloads the chat model.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from control_center.core.guards import same_origin
from control_center.modules.rag.schemas import (
    JobRequest,
    JobStatus,
    RagOverview,
    SearchRequest,
    SearchResult,
)
from control_center.modules.rag.service import (
    Busy,
    CollectionMissing,
    EmbedError,
    EmbedUnavailable,
    Locked,
    NoSources,
    RagService,
    StoreError,
    StoreUnavailable,
    UnknownJob,
    UnknownSource,
)

router = APIRouter()
write = [Depends(same_origin)]


def service(request: Request) -> RagService:
    svc = request.app.state.ctx.service("rag.service")
    if svc is None:
        raise HTTPException(503, "RAG-Modul läuft nicht")
    return svc


@router.get("/overview", response_model=RagOverview)
async def overview(request: Request) -> RagOverview:
    """Sources, collections, store and embedder state, newest job. Qdrant down = reachable false, no error."""
    return await service(request).overview()


@router.get("/jobs", response_model=list[JobStatus])
async def jobs(request: Request) -> list[JobStatus]:
    """Running job first, then finished ones, newest first (kept: [modules.rag] keep_jobs)."""
    return service(request).jobs()


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def job(request: Request, job_id: str) -> JobStatus:
    try:
        return service(request).job(job_id)
    except UnknownJob:
        raise HTTPException(404, f"Unbekannter Job: {job_id}") from None


@router.post("/jobs", response_model=JobStatus, status_code=status.HTTP_202_ACCEPTED, dependencies=write,
             responses={404: {"description": "unknown collection"},
                        409: {"description": "a job runs already, writes locked, or no sources configured"}})
async def start_job(request: Request, body: JobRequest) -> JobStatus:
    """Starts a reindex in the background and returns at once; progress follows via SSE rag.job."""
    try:
        return await service(request).start_job(body.collections)
    except (Busy, Locked) as exc:
        raise HTTPException(409, str(exc)) from None
    except NoSources:
        raise HTTPException(409, "Keine Quellen in [modules.rag] eingetragen") from None
    except UnknownSource as exc:
        raise HTTPException(404, f"Keine Quelle für Collection {exc.args[0]} eingetragen") from None


@router.post("/jobs/{job_id}/cancel", response_model=JobStatus, dependencies=write)
async def cancel_job(request: Request, job_id: str) -> JobStatus:
    """Stops after the current file. Embedded files stay in the collection, nothing is deleted."""
    try:
        return service(request).cancel_job(job_id)
    except UnknownJob:
        raise HTTPException(404, f"Unbekannter Job: {job_id}") from None


@router.post("/search", response_model=SearchResult, dependencies=write,
             responses={404: {"description": "collection does not exist"},
                        502: {"description": "Qdrant or Ollama answered with an error"},
                        503: {"description": "Qdrant or Ollama not reachable"}})
async def search(request: Request, body: SearchRequest) -> SearchResult:
    """Embeds the query exactly like the MCP tools do and asks Qdrant for the nearest files."""
    try:
        return await service(request).search(body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except CollectionMissing:
        raise HTTPException(404, f"Collection {body.collection} gibt es nicht") from None
    except (StoreUnavailable, EmbedUnavailable) as exc:
        raise HTTPException(503, str(exc)) from None
    except (StoreError, EmbedError) as exc:
        raise HTTPException(502, str(exc)) from None


@router.delete("/collections/{name}", status_code=status.HTTP_204_NO_CONTENT, dependencies=write,
               responses={400: {"description": "confirm does not repeat the name"},
                          404: {"description": "collection does not exist"},
                          409: {"description": "writes locked or a job is running"}})
async def delete_collection(request: Request, name: str,
                            confirm: str = Query(description="must repeat the collection name")) -> None:
    """Deletes the collection with all points. Irreversible - the name must be sent twice."""
    if confirm != name:
        raise HTTPException(400, "Zur Bestätigung muss confirm den Namen der Collection wiederholen")
    try:
        await service(request).drop_collection(name)
    except (Busy, Locked) as exc:
        raise HTTPException(409, str(exc)) from None
    except CollectionMissing:
        raise HTTPException(404, f"Collection {name} gibt es nicht") from None
    except StoreUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except StoreError as exc:
        raise HTTPException(502, str(exc)) from None
