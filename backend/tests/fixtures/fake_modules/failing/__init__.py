from fastapi import APIRouter

from control_center.core.module import ModuleSpec


async def startup(ctx) -> None:
    raise RuntimeError("boom on startup")


MODULE = ModuleSpec(key="failing", title="Failing", router=APIRouter(), order=20, on_startup=startup)
