"""RAG module: replaces the hand-run index scripts (index_bent.py, index_typo3.py).

Sources from [modules.rag] -> reindex jobs in the background (progress via SSE topic rag.job),
secret filter before embedding, stable point IDs with cleanup, collections overview and a test search
that embeds like the MCP tools. Qdrant and Ollama stay separate services.
"""
from __future__ import annotations

from control_center.core.context import AppContext
from control_center.core.module import ModuleSpec
from control_center.modules.rag.router import router
from control_center.modules.rag.service import RagService
from control_center.modules.rag.settings import RagSettings


async def startup(ctx: AppContext) -> None:
    svc = RagService(ctx, ctx.module_config("rag", RagSettings))
    await svc.start_up()                         # log source, job history, is Qdrant on this machine?
    ctx.services["rag.service"] = svc


async def shutdown(ctx: AppContext) -> None:
    svc: RagService | None = ctx.services.pop("rag.service", None)
    if svc:
        await svc.shut_down()                    # a running job is cancelled; finished work stays


MODULE = ModuleSpec(
    key="rag", title="RAG", icon="database", order=50, router=router,
    settings_model=RagSettings, on_startup=startup, on_shutdown=shutdown,
)
