"""API shapes of the dashboard (camelCase in JSON via CamelModel).

One DashboardSnapshot = the complete picture at one moment. GET /snapshot and every SSE
"dashboard.snapshot" event carry the same, full object - the browser never merges partial updates.
A failed source is null plus an entry in `errors`, never a fake 0.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from control_center.core.schemas import CamelModel

# gpu = fully in VRAM | split = partly on the CPU (crash risk on Blackwell) | cpu = no VRAM at all
# unknown = Ollama reported size 0 (model still loading)
Placement = Literal["gpu", "split", "cpu", "unknown"]

WarningCode = Literal[
    "ollama_offline", "model_split", "model_cpu", "vram_low", "gpu_hot", "gpu_throttled",
    "ram_high", "disk_low", "service_down",
]
WarningLevel = Literal["critical", "warning", "info"]


class LoadedModel(CamelModel):
    name: str
    digest: str                     # key for the catalog later; names can be re-tagged, digests cannot
    family: str | None
    parameter_size: str | None
    quantization: str | None
    size_bytes: int                 # total footprint (VRAM + RAM)
    vram_bytes: int
    gpu_ratio: float | None         # vram / size, 0..1; None when size is 0
    placement: Placement
    context_length: int | None
    expires_at: datetime | None     # None when pinned or unknown
    pinned: bool                    # keep_alive -1: stays loaded until unloaded by hand
    unloading: bool                 # expiry already passed, Ollama is releasing it


class GpuState(CamelModel):
    name: str
    driver_version: str | None
    util_percent: int | None
    vram_used_mib: int
    vram_total_mib: int
    temp_c: int | None
    power_w: float | None
    power_limit_w: float | None
    fan_percent: int | None
    throttle_reasons: list[str]     # idle, power_cap, sw_thermal, hw_thermal, hw_slowdown, power_brake, ...


class ProcessInfo(CamelModel):
    name: str
    pid: int
    cpu_percent: float | None       # share of the whole machine; None on first sighting
    rss_bytes: int | None
    cmdline: str | None             # shortened or hidden according to process_cmdline


class DiskUsage(CamelModel):
    mount: str
    total_bytes: int
    used_bytes: int
    free_bytes: int


class HostState(CamelModel):
    cpu_percent: float | None
    cpu_count: int
    ram_used_bytes: int
    ram_total_bytes: int
    uptime_s: int
    processes: list[ProcessInfo]    # filtered by process_names, biggest memory users first


class ServiceStatus(CamelModel):
    key: str                        # "ollama" first, then the configured probes
    title: str
    up: bool
    http_status: int | None
    latency_ms: float | None
    error: str | None


class DashboardWarning(CamelModel):
    code: WarningCode               # stable; the frontend may map it to its own text/icon
    level: WarningLevel
    message: str                    # ready-to-show German text
    subject: str | None             # model name, service key or mount the warning is about; None = general


class SourceError(CamelModel):
    source: str                     # ollama, ollama_version, gpu, host, disks
    message: str
    since: datetime                 # start of the outage, not of this tick


class DashboardSnapshot(CamelModel):
    ts: datetime
    node_id: str
    simulated: list[str]            # sources replayed from a fake scenario - the UI must label them
    ollama_online: bool
    ollama_version: str | None      # last known version, also while Ollama is offline
    models: list[LoadedModel]
    gpu: GpuState | None
    host: HostState | None
    disks: list[DiskUsage] | None   # own source (slow clock); None = failed, see errors
    services: list[ServiceStatus]
    warnings: list[DashboardWarning]    # critical first
    errors: list[SourceError]


# ---- history ----------------------------------------------------------------------------------

HistoryRange = Literal["1h", "6h", "24h", "7d", "30d"]
HistoryMetric = Literal["gpu_util", "vram_used_mib", "temp_c", "power_w", "cpu_percent", "ram_percent"]
HistoryResolution = Literal["raw", "minute", "hour"]


class HistorySeries(CamelModel):
    metric: HistoryMetric
    unit: str                       # "%", "MiB", "°C", "W"
    scale_max: float | None         # fixed top of the y axis (100 %, VRAM total, power limit); None = auto
    warn: float | None              # threshold line (temp warning, VRAM headroom ...); None = none
    avg: list[float | None]         # aligned with DashboardHistory.ts; None = no value (source failed)
    max: list[float | None]         # peak within the bucket; equals avg for raw rows


class DashboardHistory(CamelModel):
    range: HistoryRange
    since: datetime                 # x axis start (server clock) - charts span since..until even if data is sparse
    until: datetime                 # server "now" when the answer was built
    resolution: HistoryResolution
    step_s: int                     # expected distance between points; bigger gaps = server was off
    ts: list[datetime]              # bucket start, oldest first
    series: list[HistorySeries]


# ---- events -----------------------------------------------------------------------------------

EventKind = Literal[
    "model_loaded", "model_unloaded", "model_reloaded", "offload_started", "offload_ended",
    "ollama_down", "ollama_up", "ollama_restarted", "ollama_updated", "ollama_crash",
]


class DashboardEvent(CamelModel):
    id: int | None                  # None = could not be stored (database error), only seen live
    ts: datetime
    kind: EventKind
    level: WarningLevel
    subject: str | None             # model name, if the event is about one
    message: str                    # ready-to-show German text


class QuietCount(CamelModel):
    name: str
    loads: int                      # load events counted instead of listed (e.g. embedding model)


class CrashWatch(CamelModel):
    active: bool
    file: str | None                # log file being watched
    reason: str | None              # why not active


class EventPage(CamelModel):
    events: list[DashboardEvent]    # newest first
    next_before: int | None         # pass as ?before= to load older ones; None = end reached
    quiet: list[QuietCount]         # since quiet_since (server start)
    quiet_since: datetime
    crash_watch: CrashWatch
