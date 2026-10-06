"""The module contract. Everything the core knows about a module is in one ModuleSpec."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

from fastapi import APIRouter
from pydantic import BaseModel

if TYPE_CHECKING:
    from control_center.core.context import AppContext

Hook = Callable[["AppContext"], Awaitable[None]]


@dataclass(frozen=True)
class ModuleSpec:
    key: str                                       # must equal the folder name; URL prefix /api/v1/<key>
    title: str                                     # menu label in Angular
    router: APIRouter
    icon: str = "extension"                        # icon name, interpreted by the frontend
    order: int = 100                               # menu position AND startup order (low first)
    settings_model: type[BaseModel] | None = None  # runtime settings schema, collected by the settings module
    on_startup: Hook | None = None                 # start background tasks, register services
    on_shutdown: Hook | None = None                # stop them again
