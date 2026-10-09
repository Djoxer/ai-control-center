"""API shapes of the catalog (camelCase in JSON via CamelModel).

Every number carries its origin, because the page colors by it:
- facts from Ollama (size, quantization, parameters) are plain - they are what is installed,
- "measured" = seen in /api/ps while the model ran on this GPU, or measured by a test run,
- "estimated" = computed from the GGUF metadata before loading (calibrated by measurements of the same
  weights where there are some),
- (later, part c2b) "adopted" = taken over from BenchLM.
Candidates are models of Ollama's library checked before a pull: facts from the registry, verdict estimated.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from control_center.core.schemas import CamelModel

Via = Literal["declared", "weights", "copy"]
ContextSource = Literal["model", "server", "fallback", "request"]
ServerSource = Literal["log", "config", "mixed", "unknown"]
Confidence = Literal["normal", "low"]
Placement = Literal["gpu", "split", "cpu", "unknown"]
VerdictState = Literal["fits", "tight", "split", "cpu", "unknown"]
Basis = Literal["measured", "estimated", "none"]
OtherSource = Literal["measured", "assumed"]
BenchState = Literal["queued", "running", "done", "failed", "cancelled"]
BenchPhase = Literal["unload", "baseline", "load", "measure", "tools", "cleanup"]
UsageTag = Literal["opencode", "openwebui", "rag", "test", "remove"]
FitState = Literal["fits", "maybe", "no", "unknown"]


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
    note: str | None = None                 # German: why the GPU is unknown, or that it is simulated


class BudgetInfo(CamelModel):
    """What the card offers Ollama: total - other programs - Ollama's reserve."""
    total_bytes: int | None = None
    other_bytes: int
    other_source: OtherSource               # measured while Ollama had nothing loaded, or the setting
    other_measured_at: datetime | None = None
    reserve_bytes: int
    available_bytes: int | None = None


class Assumptions(CamelModel):
    """The fixed numbers behind every estimate - shown on the page, set in [modules.catalog]."""
    graph_reserve_bytes: int
    tight_ratio: float
    fallback_context_length: int
    clamp_to_trained: bool
    opencode_min_context: int = 65536


class ParentInfo(CamelModel):
    declared: str | None = None             # details.parent_model as Ollama reports it
    resolved: str | None = None             # installed model the catalog hangs this one under
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
    formula_bytes: int | None = None        # weights + KV + graph
    need_bytes: int | None = None           # calibrated where possible, else the formula
    calibrated: str | None = None           # German: from which measurements
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
    need_bytes: int | None = None           # Ollama's scale (same number as the dashboard)
    available_bytes: int | None = None      # budget of the card for Ollama
    extra_bytes: int = 0                    # held beyond Ollama's count and the reserve (test run); compared too
    message: str


class OverheadInfo(CamelModel):
    """What the runner held beyond Ollama's count in the newest test run of the same weights on this GPU."""
    measured_bytes: int                     # NVML growth minus Ollama's size_vram
    reserve_bytes: int                      # covered by the budget's reserve already
    extra_bytes: int                        # the rest: added to the need in the verdict
    model: str                              # the model that ran (a relative with the same weights counts too)
    measured_at: datetime


class ToolCase(CamelModel):
    key: str                                # read | choose | types
    label: str                              # German
    ok: bool
    detail: str                             # German: the call as made, or what went wrong
    seconds: float | None = None
    thinking: bool = False                  # the model reasoned before answering


class ToolCheck(CamelModel):
    """Did the model answer with structured tool calls? Three agent-like requests via /api/chat."""
    passed: int
    total: int
    skipped: str | None = None              # German: why no request was sent (no tool support, switched off)
    simulated: bool = False                 # answers replayed from the fake adapter
    cases: list[ToolCase] = Field(default_factory=list)


class BenchResult(CamelModel):
    """What one test run measured. Speeds from Ollama's own clocks, memory from /api/ps and NVML."""
    requested_ctx: int
    actual_ctx: int | None = None           # what Ollama loaded (shows whether it cut the context)
    hardware: str | None = None
    kv_type: str | None = None
    ollama_version: str | None = None
    load_s: float | None = None
    prompt_tokens: int | None = None
    prompt_tps: float | None = None
    eval_tokens: int | None = None
    eval_tps: float | None = None
    total_s: float | None = None
    size_bytes: int | None = None
    vram_bytes: int | None = None
    placement: Placement = "unknown"
    gpu_before_bytes: int | None = None     # NVML, after the other models were unloaded
    gpu_after_bytes: int | None = None
    runner_overhead_bytes: int | None = None  # NVML growth beyond Ollama's own count
    tools: ToolCheck | None = None          # None = run made before the check existed, or it was switched off
    note: str | None = None


class BenchStatus(CamelModel):
    id: str
    revision: int = 0
    as_of: datetime
    name: str
    digest: str
    num_ctx: int
    state: BenchState
    phase: BenchPhase | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    unloaded: list[str] = Field(default_factory=list)   # models the run unloaded first
    error: str | None = None
    result: BenchResult | None = None


class DerivedTag(CamelModel):
    tag: UsageTag
    source: str                             # German: who says so ("RAG-Modul")


class UsageInfo(CamelModel):
    """What the team uses a model for - set on the page, kept by name (survives re-pulls)."""
    tags: list[UsageTag] = Field(default_factory=list)
    note: str | None = None
    updated_at: datetime | None = None
    derived: list[DerivedTag] = Field(default_factory=list)   # known from the configuration, not set by hand


class OpencodeFit(CamelModel):
    """Can OpenCode work with this model on this card? Tools measured, context and VRAM computed."""
    state: FitState
    reasons: list[str]                      # German, one line each; the first one is the summary
    tools_passed: int | None = None
    tools_total: int | None = None
    tools_at: datetime | None = None
    tools_simulated: bool = False
    context: int                            # what OpenCode would get (the effective context)
    min_context: int
    at_min: Verdict | None = None           # when the context is too small: would min_context fit?
    eval_tps: float | None = None
    config_key: str                         # model key in opencode.json (Ollama accepts it without :latest)
    block: str                              # ready-to-paste entry for provider.ollama.models in opencode.json


class ContextStep(CamelModel):
    """One context length of a candidate: what the card would have to hold, and the verdict."""
    tokens: int
    need_bytes: int | None = None           # Ollama's count (estimate, calibrated when the weights were measured)
    extra_bytes: int = 0                    # beyond Ollama's count (test run of the same weights)
    state: VerdictState


class Candidate(CamelModel):
    """A model from Ollama's library, checked BEFORE the pull: the same estimate and verdict as an installed
    one, from the manifest and the GGUF header. Facts are from the time of the check, the verdict is fresh."""
    name: str                               # as Ollama will list it after the pull
    host: str                               # registry.ollama.ai, hf.co
    page: str | None = None                 # web page of the model
    checked_at: datetime
    simulated: bool = False                 # answered by the fake library (library-samples)
    pull: str                               # "ollama pull <name>"
    download_bytes: int                     # all layers = what /api/tags will report as size
    weights_bytes: int
    projector_bytes: int = 0                # vision encoder as its own file
    family: str | None = None
    parameter_size: str | None = None
    quantization: str | None = None
    architecture: str | None = None
    capabilities: list[str] = Field(default_factory=list)   # derived like Ollama does - final after the pull
    capability_notes: list[str] = Field(default_factory=list)
    parameters: dict[str, list[str]] = Field(default_factory=dict)
    requires: str | None = None             # minimum Ollama version (config of the model)
    requires_ok: bool | None = None         # None = unknown (no requirement or Ollama version unknown)
    context: ContextInfo | None = None
    estimate: VramEstimate | None = None
    verdict: Verdict | None = None
    steps: list[ContextStep] = Field(default_factory=list)  # ascending context lengths up to the trained one
    fits_up_to: int | None = None           # largest step with "passt"
    loads_up_to: int | None = None          # largest step that still loads fully ("passt" or "knapp")
    opencode: OpencodeFit | None = None
    installed: bool = False                 # this very name is installed
    same_weights: list[str] = Field(default_factory=list)  # installed models with the same weights file
    header_bytes: int = 0                   # bytes of the weights file that were read
    header_complete: bool = False
    notes: list[str] = Field(default_factory=list)
    error: str | None = None                # why there is no estimate (cloud model, no GGUF, broken header)


class LibraryInfo(CamelModel):
    simulated: bool = False
    hosts: list[str] = Field(default_factory=list)


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
    overhead: OverheadInfo | None = None    # measured by a test run of the same weights
    usage: UsageInfo = Field(default_factory=UsageInfo)
    opencode: OpencodeFit | None = None     # chat models only
    benches: list[BenchStatus] = Field(default_factory=list)   # latest test runs of this model, newest first
    testable: bool = True                   # embedding models have no test run (yet)
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


class BenchAccess(CamelModel):
    allowed: bool
    reason: str | None = None               # German: why test runs are locked here


class CatalogOverview(CamelModel):
    as_of: datetime
    revision: int
    refreshed_at: datetime | None = None    # last successful /api/tags of this process
    ollama: OllamaState
    server: ServerConfig
    hardware: HardwareInfo
    budget: BudgetInfo
    assumptions: Assumptions
    tests: BenchAccess
    bench: BenchStatus | None = None        # the running test run, if any
    models: list[CatalogModel]
    groups: list[ModelGroup]
    removed: list[RemovedModel]
    candidates: list[Candidate] = Field(default_factory=list)   # newest check first
    library: LibraryInfo = Field(default_factory=LibraryInfo)


class RefreshRequest(CamelModel):
    name: str | None = Field(None, description="one model; null = all installed models")


class UsageRequest(CamelModel):
    name: str
    tags: list[UsageTag] = Field(default_factory=list, max_length=5)
    note: str | None = Field(None, max_length=200)


class CandidateRequest(CamelModel):
    name: str = Field(min_length=1, max_length=300,
                      description="as for ollama pull: qwen3-coder:30b, user/model:tag, hf.co/org/repo:tag")


class CandidateResult(CamelModel):
    name: str                               # normalized name of the checked candidate
    overview: CatalogOverview


class Preflight(CamelModel):
    """Before a test run: what loading this model with this context would cost, and whether it may run."""
    name: str
    context: ContextInfo
    estimate: VramEstimate | None = None
    verdict: Verdict
    allowed: bool
    needs_confirm: bool                     # "knapp": only with an explicit confirmation
    reason: str | None = None               # German: why not (or what to know)
    will_unload: list[str] = Field(default_factory=list)
    suggestions: list[int] = Field(default_factory=list)   # context lengths worth trying, ascending


class BenchRequest(CamelModel):
    name: str
    num_ctx: int | None = Field(None, ge=256, le=4_194_304, description="null = the effective context")
    confirm: bool = Field(False, description="required when the preflight says 'knapp'")
