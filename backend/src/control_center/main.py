"""App factory. Wires core services, discovers modules, mounts routers. No module is named here."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from control_center import __version__
from control_center.core import api
from control_center.core.config import Settings, load_settings
from control_center.core.context import AppContext, ModuleStatus
from control_center.core.db import Database
from control_center.core.events import EventBus
from control_center.core.loader import discover
from control_center.core.log_setup import setup_logging
from control_center.core.module import ModuleSpec
from control_center.core.spa import mount_spa

log = logging.getLogger("control_center.main")


def operation_id(route: APIRoute) -> str:
    """Readable, unique OpenAPI operationIds: <tag>_<function>, e.g. core_health.

    The Angular generator turns these into method names. FastAPI's default would be
    "health_api_v1_health_get" -> healthApiV1HealthGet() in TypeScript.
    """
    tag = route.tags[0] if route.tags else "misc"
    return f"{tag}_{route.name}"


def create_app(
    settings: Settings | None = None,
    modules_package: str = "control_center.modules",
    configure_logging: bool = True,     # False for tools like the OpenAPI export: no log file is opened
) -> FastAPI:
    settings = settings or load_settings()
    if configure_logging:
        setup_logging(settings)
    ctx = AppContext(settings=settings, db=Database(settings.db_path), events=EventBus())

    # 1) discover and register every module folder, including broken ones (for the health view)
    active: list[ModuleSpec] = []
    for d in discover(modules_package):
        if d.spec is None:
            ctx.modules[d.key] = ModuleStatus(d.key, d.key, "error", 9999, "failed", d.error)
            continue
        s = d.spec
        disabled = s.key in settings.modules.disabled
        ctx.modules[s.key] = ModuleStatus(s.key, s.title, s.icon, s.order, "disabled" if disabled else "loaded")
        if not disabled:
            active.append(s)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await ctx.db.open()
        await ctx.adapters.open()                       # before modules: their startup may use them
        started: list[ModuleSpec] = []
        for s in active:                                # already sorted by order
            status = ctx.modules[s.key]
            status.state = "starting"
            try:
                if s.on_startup:
                    await s.on_startup(ctx)
                status.state = "running"
                started.append(s)
            except Exception as exc:
                # a broken catalog must not take the dashboard down with it
                log.exception("module %s failed to start", s.key)
                status.state, status.error = "failed", repr(exc)
        log.info("control center %s up, modules: %s", __version__,
                 {k: m.state for k, m in ctx.modules.items()})
        try:
            yield
        finally:
            for s in reversed(started):                 # reverse order, only what actually started
                if s.on_shutdown:
                    try:
                        await s.on_shutdown(ctx)
                    except Exception:
                        log.exception("module %s failed to stop", s.key)
            await ctx.adapters.close()                  # after modules: their tasks are stopped by now
            await ctx.db.close()

    app = FastAPI(title="AI Control Center", version=__version__, lifespan=lifespan,
                  generate_unique_id_function=operation_id)
    app.state.ctx = ctx

    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                           allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        # log the traceback once, send no internals to the browser (RFC 9457 problem shape)
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, media_type="application/problem+json",
                            content={"title": "Internal Server Error", "status": 500})

    # 2) routes: core first, then modules, SPA catch-all last (it would swallow everything after it)
    app.include_router(api.router)
    for s in active:
        app.include_router(s.router, prefix=f"/api/v1/{s.key}", tags=[s.key])
    if settings.frontend_dist is not None:
        mount_spa(app, settings.frontend_dist)
    return app
