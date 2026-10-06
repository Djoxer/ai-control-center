from fastapi import APIRouter

from control_center.core.module import ModuleSpec

router = APIRouter()


@router.get("/ping")
async def ping() -> dict:
    return {"pong": True}


async def startup(ctx) -> None:
    ctx.services["good.lookup"] = lambda name: f"info:{name}"


MODULE = ModuleSpec(key="good", title="Good", router=router, order=10, on_startup=startup)
