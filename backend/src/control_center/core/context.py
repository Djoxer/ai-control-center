"""Shared runtime state. Modules talk to each other only through services registered here."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from control_center.core.config import Settings
from control_center.core.db import Database
from control_center.core.events import EventBus

ModuleState = Literal["loaded", "starting", "running", "failed", "disabled"]


@dataclass
class ModuleStatus:
    key: str
    title: str
    icon: str
    order: int
    state: ModuleState
    error: str | None = None


@dataclass
class AppContext:
    settings: Settings
    db: Database
    events: EventBus
    modules: dict[str, ModuleStatus] = field(default_factory=dict)
    services: dict[str, Any] = field(default_factory=dict)

    def service(self, name: str) -> Any | None:
        """None if the providing module is disabled or failed -> callers degrade gracefully."""
        return self.services.get(name)
