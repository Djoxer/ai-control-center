"""Shared runtime state. Modules talk to each other only through services registered here."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from control_center.adapters.registry import Adapters
from control_center.core.config import Settings
from control_center.core.db import Database
from control_center.core.events import EventBus

M = TypeVar("M", bound=BaseModel)

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
    # shared connections to Ollama, GPU and OS; None only until __post_init__ has run
    adapters: Adapters = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.adapters is None:
            # cheap: nothing connects here, open() runs in the app lifespan
            self.adapters = Adapters(self.settings.adapters)

    def service(self, name: str) -> Any | None:
        """None if the providing module is disabled or failed -> callers degrade gracefully."""
        return self.services.get(name)

    def module_config(self, key: str, model: type[M]) -> M:
        """Validate the [modules.<key>] toml table against the module's own settings model.

        Raises on invalid values -> the module's startup fails and shows up as 'failed' in /health.
        """
        return model.model_validate(self.settings.modules.section(key))
