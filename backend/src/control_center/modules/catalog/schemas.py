"""API shapes of the catalog (camelCase in JSON via CamelModel).

Every number carries its origin, because the page colors by it:
- facts from Ollama (size, quantization, parameters) are plain - they are what is installed,
- "measured" = seen in /api/ps while the model ran on this GPU,
- "estimated" = computed from the GGUF metadata before loading,
- (later, part c) "adopted" = taken over from BenchLM.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from control_center.core.schemas import CamelModel

Via = Literal["declared", "weights"]
ContextSource = Literal["model", "server", "fallback"]
ServerSource = Literal["log", "config", "mixed", "unknown"]
Confidence = Literal["normal", "low"]
Placement = Literal["gpu", "split", "cpu", "unknown"]
VerdictState = Literal["fits", "tight", "split", "cpu", "unknown"]
Basis = Literal["measured", "estimated", "none"]


class OllamaState(CamelModel):
    online: bool
    version: str | None = None
    error: str | None = None
    simulated: bool = False                 # fake adapter: recorded scenario, not the live box


class ServerConfig(CamelModel):
    """Ollama's defaults for models without own values. From server.log and/or [modules.catalog]."""
    source: ServerSource
    log_file: str | None = None             # file name only (the folder contains the Windows user name)
    note: str | None = None                 # German: why values are missing
    context_length: int | None = None
    kv_cache_type: str | None = None
    flash_attention: bool | None = None
    num_parallel: int | None = None
    max_loaded_models: int | None = None
    keep_alive: str | None = None
    overridden: list[str] = Field(default_factory=list)   # keys set in [modules.catalog]


class HardwareInfo(CamelModel):
    key: str | None = None                  # profile measurements are filed under: "<GPU> · <MiB> MiB"
    gpu_name: str | None = None
    vram_total_bytes: int | None = None
    note: str | None = None                 # German: why the GPU is unknown


class Assumptions(CamelModel):
    """The fixed numbers behind every estimate - shown on the page, set in [modules.catalog]."""
    graph_reserve_bytes: int
    driver_overhead_bytes: int
    tight_ratio: float
    fallback_context_length: int
    clamp_to_trained: bool


class ParentInfo(CamelModel):
    declared: str | None = None             # details.parent_model as Ollama reports it
    resolved: str | None = None             # installed parent the catalog hangs this model under
    via: Via | None = None
    installed: bool = False                 # the declared parent is installed


class ContextInfo(CamelModel):
    effective: int
    source: ContextSource
    own: int | None = None
    server: int | None = None
    trained: int | None = None
    clamped: bool = False
    parallel: int = 1


class VramEstimate(CamelModel):
    weights_bytes: int
    kv_bytes: int | None = None
    graph_bytes: int
    driver_bytes: int
    total_bytes: int | None = None
    kv_type: str
    tokens: int
    notes: list[str] = Field(default_factory=list)
    confidence: Confidence = "normal"


class Observation(CamelModel):
    num_ctx: int | None                     # None = Ollama did not report the context
    hardware: str | None
    size_bytes: int
    vram_bytes: int
    placement: Placement
    gpu_ratio: float | None
    first_seen: datetime
    last_seen: datetime
    loads: int
    current: bool                           # same context and GPU as the effective setting now


class Verdict(CamelModel):
    state: VerdictState
    basis: Basis
    need_bytes: int | None = None           # Ollama's count (measured size, or estimate without driver)
    expected_bytes: int | None = None       # need + driver overhead: what the card must hold
    vram_total_bytes: int | None = None
    message: str


class CatalogModel(CamelModel):
    name: str
    digest: str
    size_bytes: int
    modified_at: datetime | None = None
    family: str | None = None
    parameter_size: str | None = None
    quantization: str | None = None
    format: str | None = None
    architecture: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    parameters: dict[str, list[str]] = Field(default_factory=dict)
    system_chars: int = 0
    weights_digest: str | None = None
    parent: ParentInfo
    origin: str
    depth: int
    changes: list[str] = Field(default_factory=list)       # vs. the resolved parent
    context: ContextInfo
    estimate: VramEstimate | None = None
    observations: list[Observation] = Field(default_factory=list)
    verdict: Verdict
    loaded: bool = False                    # in /api/ps right now
    first_seen: datetime | None = None
    show_error: str | None = None           # /api/show failed: only /api/tags data


class ModelGroup(CamelModel):
    origin: str
    installed: bool
    members: list[str]


class RemovedModel(CamelModel):
    name: str
    digest: str
    removed_at: datetime
    measurements: int


class CatalogOverview(CamelModel):
    as_of: datetime
    revision: int
    refreshed_at: datetime | None = None    # last successful /api/tags of this process
    ollama: OllamaState
    server: ServerConfig
    hardware: HardwareInfo
    assumptions: Assumptions
    models: list[CatalogModel]
    groups: list[ModelGroup]
    removed: list[RemovedModel]


class RefreshRequest(CamelModel):
    name: str | None = Field(None, description="one model; null = all installed models")
