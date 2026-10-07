"""MCP module: starts, stops and watches the MCP servers listed in [modules.mcp] as child processes of
the control center - state, port, tool list (MCP tools/list), output as a source on the logs page.
Live updates via SSE topic mcp.server."""
from __future__ import annotations

from control_center.core.context import AppContext
from control_center.core.module import ModuleSpec
from control_center.modules.mcp.router import router
from control_center.modules.mcp.service import McpService
from control_center.modules.mcp.settings import McpSettings


async def startup(ctx: AppContext) -> None:
    svc = McpService(ctx, ctx.module_config("mcp", McpSettings))
    await svc.start_up()                         # leftovers from a crash, autostart, monitor
    ctx.services["mcp.service"] = svc


async def shutdown(ctx: AppContext) -> None:
    svc: McpService | None = ctx.services.pop("mcp.service", None)
    if svc:
        await svc.shut_down()                    # children end with the control center


MODULE = ModuleSpec(
    key="mcp", title="MCP-Server", icon="server", order=40, router=router,
    settings_model=McpSettings, on_startup=startup, on_shutdown=shutdown,
)
