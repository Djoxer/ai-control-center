"""Dashboard module: live state of the AI box - loaded models with GPU/CPU placement, GPU, host,
services - as one snapshot (GET /snapshot) and as SSE stream (topic dashboard.snapshot)."""
from __future__ import annotations

from control_center.core.context import AppContext
from control_center.core.module import ModuleSpec
from control_center.modules.dashboard.router import router
from control_center.modules.dashboard.service import DashboardService
from control_center.modules.dashboard.settings import DashboardSettings


async def startup(ctx: AppContext) -> None:
    svc = DashboardService(ctx, ctx.module_config("dashboard", DashboardSettings))
    svc.start()
    ctx.services["dashboard.service"] = svc


async def shutdown(ctx: AppContext) -> None:
    svc: DashboardService | None = ctx.services.pop("dashboard.service", None)
    if svc:
        await svc.stop()


MODULE = ModuleSpec(
    key="dashboard", title="Dashboard", icon="dashboard", order=10, router=router,
    settings_model=DashboardSettings, on_startup=startup, on_shutdown=shutdown,
)
