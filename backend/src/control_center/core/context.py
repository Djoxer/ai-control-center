"""Shared runtime state. Modules talk to each other only through services registered here."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
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


@dataclass(frozen=True)
class LogSource:
    """Log files a module wants the logs page to list and tail, e.g. the output of a managed process.

    A core type on purpose: the providing module must not import the logs module, and the logs
    module picks entries up while it runs - startup order and a disabled logs module do not matter.
    """
    key: str                        # URL and SSE topic part: lower case, digits, '-' (e.g. "mcp-bent-rag")
    title: str                      # button label on the logs page
    format: Literal["json", "text"]
    paths: tuple[str, ...]          # glob patterns, same rules as [modules.logs] paths


@dataclass
class AppContext:
    settings: Settings
    db: Database
    events: EventBus
    modules: dict[str, ModuleStatus] = field(default_factory=dict)
    services: dict[str, Any] = field(default_factory=dict)
    help_files: dict[str, Path] = field(default_factory=dict)   # module key -> its HELP.md (may not exist)
    log_sources: dict[str, LogSource] = field(default_factory=dict)  # added by modules, shown by the logs module
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
