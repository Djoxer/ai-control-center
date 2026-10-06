from fastapi import APIRouter

from control_center.core.module import ModuleSpec

MODULE = ModuleSpec(key="renamed", title="Wrong", router=APIRouter())
