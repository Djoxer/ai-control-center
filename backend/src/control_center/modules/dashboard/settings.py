"""Dashboard settings: [modules.dashboard] in control-center.toml (runtime editing comes with the settings module).

Where Ollama/GPU/host data comes from is NOT configured here but in [adapters] (shared by all modules).
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from control_center.adapters.host import CmdlineMode


class ProbeConfig(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str
    url: str                        # "{ai_host}" is replaced by [adapters] ai_host


class DashboardSettings(BaseModel):
    node_id: str | None = None      # label of the observed machine; None = host name (local) or ai_host (remote)

    # three sampling rates: cheap + volatile often, expensive + static rarely
    interval_fast_s: float = Field(2.0, ge=0.5, le=60)      # loaded models, GPU
    interval_medium_s: float = Field(10.0, ge=1, le=600)    # CPU/RAM/processes, service probes
    interval_slow_s: float = Field(60.0, ge=5, le=3600)     # Ollama version, disks

    # warning thresholds
    offload_threshold: float = Field(1.0, gt=0, le=1)       # GPU share below this = model partly on the CPU
    vram_headroom_warn_mib: int = Field(1500, ge=0)         # free VRAM below this -> warning
    temp_warn_c: int = Field(83, ge=40, le=110)
    ram_warn_percent: float = Field(90, gt=0, le=100)
    disk_warn_free_gib: float = Field(20, ge=0)

    # processes: glob patterns on the lower-case name without ".exe"
    process_names: list[str] = Field(default_factory=lambda: [
        "ollama*", "llama-server*", "com.docker.backend", "python*",
    ])
    process_cmdline: CmdlineMode = "short"                  # full | short | off - the dashboard is visible in the LAN

    # disks to watch; the drive containing each path is reported once
    disk_paths: list[str] = Field(default_factory=lambda: [
        "%OLLAMA_MODELS%",                                  # skipped when the variable is not set
        "%USERPROFILE%/.ollama/models",                     # Ollama's default model store on Windows
    ])

    # history in SQLite: raw sample every n fast ticks, condensed to minute and hour averages/peaks
    persist_every_n_ticks: int = Field(5, ge=1, le=600)     # 5 x 2 s = one raw row every 10 s
    retention_raw_h: int = Field(24, ge=1, le=24 * 7)
    retention_minute_d: int = Field(30, ge=1, le=365)
    retention_hour_d: int = Field(365, ge=1, le=3650)
    retention_events_d: int = Field(90, ge=1, le=3650)

    # events
    restart_window_s: float = Field(30, ge=0, le=600)       # Ollama back within this = one "restarted" event
    event_quiet_models: list[str] = Field(default_factory=lambda: ["nomic-embed-text"])   # only counted
    # crash detection: only when Ollama runs on THIS machine (ai_host local), the log lives there
    ollama_log_paths: list[str] = Field(default_factory=lambda: ["%LOCALAPPDATA%/Ollama/server*.log"])
    crash_patterns: list[str] = Field(default_factory=lambda: ["0xc0000409", "CUDA error"])
    crash_cooldown_s: float = Field(30, ge=0, le=3600)      # one crash writes many lines -> one event

    probes: list[ProbeConfig] = Field(default_factory=lambda: [
        ProbeConfig(key="mcp", title="MCP-Server", url="http://{ai_host}:8000/mcp"),
        ProbeConfig(key="openwebui", title="OpenWebUI", url="http://{ai_host}:3000/health"),
        ProbeConfig(key="qdrant", title="Qdrant", url="http://{ai_host}:6333/healthz"),
    ])

    @model_validator(mode="after")
    def _rates_in_order(self) -> "DashboardSettings":
        if not self.interval_fast_s <= self.interval_medium_s <= self.interval_slow_s:
            raise ValueError("intervals must satisfy fast <= medium <= slow")
        keys = [p.key for p in self.probes]
        if "ollama" in keys or len(keys) != len(set(keys)):
            raise ValueError("probe keys must be unique and must not be 'ollama' (built in)")
        return self
