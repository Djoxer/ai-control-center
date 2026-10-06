"""Logs module: list log sources, page through entries with filters, live tail via SSE (topic logs.<source>)."""
from __future__ import annotations

from control_center.core.context import AppContext
from control_center.core.module import ModuleSpec
from control_center.modules.logs.router import router
from control_center.modules.logs.service import LogsService
from control_center.modules.logs.settings import LogsSettings


async def startup(ctx: AppContext) -> None:
    svc = LogsService(ctx, ctx.module_config("logs", LogsSettings))
    svc.start()
    ctx.services["logs.service"] = svc


async def shutdown(ctx: AppContext) -> None:
    svc: LogsService | None = ctx.services.pop("logs.service", None)
    if svc:
        await svc.stop()


MODULE = ModuleSpec(
    key="logs", title="Logs", icon="log", order=30, router=router,
    settings_model=LogsSettings, on_startup=startup, on_shutdown=shutdown,
)
