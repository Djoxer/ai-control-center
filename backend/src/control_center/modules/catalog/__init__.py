"""Catalog module: the models installed in Ollama, grouped by origin, with what they cost on the GPU.

Inventory from /api/tags + /api/show (derivations via parent_model, fallback via the weights blob),
effective context per model, VRAM estimate before loading, observed VRAM while loaded (/api/ps).
Replaces the old static market catalog step by step: own test runs and BenchLM data follow.
"""
from __future__ import annotations

from control_center.core.context import AppContext
from control_center.core.module import ModuleSpec
from control_center.modules.catalog.router import router
from control_center.modules.catalog.service import CatalogService
from control_center.modules.catalog.settings import CatalogSettings


async def startup(ctx: AppContext) -> None:
    svc = CatalogService(ctx, ctx.module_config("catalog", CatalogSettings))
    await svc.start_up()                         # tables, stored inventory, server defaults, GPU; loop starts
    ctx.services["catalog.service"] = svc


async def shutdown(ctx: AppContext) -> None:
    svc: CatalogService | None = ctx.services.pop("catalog.service", None)
    if svc:
        await svc.shut_down()


MODULE = ModuleSpec(
    key="catalog", title="Katalog", icon="list", order=20, router=router,
    settings_model=CatalogSettings, on_startup=startup, on_shutdown=shutdown,
)
