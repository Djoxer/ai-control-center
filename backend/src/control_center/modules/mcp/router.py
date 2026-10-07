"""HTTP endpoints of the MCP module, mounted under /api/v1/mcp.

Write actions (start, stop, restart) only take a server KEY from [modules.mcp] - the command
itself never travels over HTTP. They are guarded against cross-site requests (core/guards.py);
real access control comes with the login via OpenWebUI.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from control_center.core.guards import same_origin
from control_center.modules.mcp.process import SpawnError
from control_center.modules.mcp.schemas import McpServerStatus, McpToolList
from control_center.modules.mcp.service import McpService, NoUrl, UnknownServer

router = APIRouter()
write = [Depends(same_origin)]


def service(request: Request) -> McpService:
    svc = request.app.state.ctx.service("mcp.service")
    if svc is None:
        raise HTTPException(503, "MCP-Modul läuft nicht")
    return svc


def runtime_or_404(svc: McpService, key: str):
    try:
        return svc.runtime(key)
    except UnknownServer:
        raise HTTPException(404, f"Unbekannter MCP-Server: {key}") from None


@router.get("/servers", response_model=list[McpServerStatus])
async def servers(request: Request) -> list[McpServerStatus]:
    """Every server from [modules.mcp], in config order."""
    return service(request).statuses()


@router.get("/servers/{key}", response_model=McpServerStatus)
async def server(request: Request, key: str) -> McpServerStatus:
    svc = service(request)
    return svc.status(runtime_or_404(svc, key))


@router.post("/servers/{key}/start", response_model=McpServerStatus, dependencies=write,
             responses={409: {"description": "cannot start: program/folder missing or port taken"}})
async def start(request: Request, key: str) -> McpServerStatus:
    """Returns right after the process exists (state starting); readiness follows via SSE mcp.server."""
    svc = service(request)
    runtime_or_404(svc, key)
    try:
        return await svc.start(key)
    except SpawnError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/servers/{key}/stop", response_model=McpServerStatus, dependencies=write)
async def stop(request: Request, key: str) -> McpServerStatus:
    """Waits until the whole process tree has ended (at most stop_timeout_s + a few seconds)."""
    svc = service(request)
    runtime_or_404(svc, key)
    return await svc.stop(key)


@router.post("/servers/{key}/restart", response_model=McpServerStatus, dependencies=write,
             responses={409: {"description": "stopped, but the new start failed"}})
async def restart(request: Request, key: str) -> McpServerStatus:
    svc = service(request)
    runtime_or_404(svc, key)
    try:
        return await svc.restart(key)
    except SpawnError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/servers/{key}/tools", response_model=McpToolList,
            responses={409: {"description": "server has no url configured"}})
async def tools(request: Request, key: str) -> McpToolList:
    """Asks the server now (MCP tools/list). Works for a server started elsewhere too, if the URL answers.

    Failures are part of the answer (error field), not an HTTP error: an unreachable server is a state.
    """
    svc = service(request)
    runtime_or_404(svc, key)
    try:
        return await svc.tools(key)
    except NoUrl:
        raise HTTPException(409, "Für diesen Server ist keine url eingetragen") from None
