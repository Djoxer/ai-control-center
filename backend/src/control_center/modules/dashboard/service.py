"""Dashboard service: runs the sampler loop, turns raw readings into a DashboardSnapshot,
derives warnings, keeps the latest snapshot in memory and pushes it to SSE subscribers.

build_snapshot() and derive_warnings() are pure functions: readings in, snapshot out.
That is where the logic lives, so that is what the tests hammer.
"""
from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import asdict
from datetime import datetime, timezone

from control_center.adapters.ollama import PINNED_AFTER, RunningModel
from control_center.core.context import AppContext
from control_center.modules.dashboard.sampler import Readings, Sampler
from control_center.modules.dashboard.schemas import (
    DashboardSnapshot, DashboardWarning, DiskUsage, GpuState, HostState, LoadedModel, Placement,
    ProcessInfo, ServiceStatus, SourceError, WarningCode, WarningLevel,
)
from control_center.modules.dashboard.settings import DashboardSettings

log = logging.getLogger("control_center.modules.dashboard")

TOPIC = "dashboard.snapshot"
GIB = 1024 ** 3
MAX_PROCESSES = 30                  # "python*" can match dozens on a dev box; the biggest ones matter
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

# throttle reasons worth a warning; power_cap under full load and idle clocks are normal
THROTTLE_WARN = {
    "hw_slowdown": "Hardware-Bremse",
    "sw_thermal": "Temperatur (Treiber)",
    "hw_thermal": "Temperatur (Hardware)",
    "power_brake": "Netzteil-Bremse",
}
_LEVEL_ORDER = {"critical": 0, "warning": 1, "info": 2}


# ---- pure helpers -----------------------------------------------------------------------------

def default_node_id(ai_host: str) -> str:
    """Local box: its host name. Remote box (dev PC): the address we observe."""
    return socket.gethostname() if ai_host in LOCAL_HOSTS else ai_host


def classify(size: int, size_vram: int, threshold: float) -> tuple[float | None, Placement]:
    """Where does the model live? The crash-relevant case is "split", not "cpu"."""
    if size <= 0:
        return None, "unknown"                  # Ollama reports 0 while a model is still loading
    if size_vram <= 0:
        return 0.0, "cpu"
    ratio = round(min(size_vram / size, 1.0), 4)
    return ratio, ("gpu" if ratio >= threshold else "split")


def to_loaded_model(m: RunningModel, threshold: float, now: datetime) -> LoadedModel:
    ratio, placement = classify(m.size, m.size_vram, threshold)
    pinned = m.expires_at is not None and m.expires_at - now > PINNED_AFTER
    return LoadedModel(
        name=m.name, digest=m.digest, family=m.family, parameter_size=m.parameter_size,
        quantization=m.quantization, size_bytes=m.size, vram_bytes=m.size_vram,
        gpu_ratio=ratio, placement=placement, context_length=m.context_length,
        expires_at=None if pinned else m.expires_at, pinned=pinned,
        unloading=m.expires_at is not None and not pinned and m.expires_at <= now,
    )


def _num(value: float, digits: int = 0) -> str:
    """German number format for messages: 1500 -> '1.500', 12.5 -> '12,5'."""
    text = f"{value:,.{digits}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def derive_warnings(s: DashboardSnapshot, cfg: DashboardSettings) -> list[DashboardWarning]:
    out: list[DashboardWarning] = []

    def add(code: WarningCode, level: WarningLevel, message: str, subject: str | None = None) -> None:
        out.append(DashboardWarning(code=code, level=level, message=message, subject=subject))

    if not s.ollama_online:
        add("ollama_offline", "critical", "Ollama ist nicht erreichbar.")
    for m in s.models:
        if m.placement == "split":
            pct = int((m.gpu_ratio or 0) * 100)     # floor: 99.9 % is still a split, never show "100 %"
            add("model_split", "critical",
                f"{m.name}: nur {pct} % auf der GPU, Rest auf der CPU – Absturzgefahr beim Teil-Offload. "
                f"num_ctx verkleinern oder andere Modelle entladen.", m.name)
        elif m.placement == "cpu":
            add("model_cpu", "warning", f"{m.name} läuft komplett auf der CPU – sehr langsam.", m.name)
    if s.gpu is not None:
        g = s.gpu
        free = g.vram_total_mib - g.vram_used_mib
        if free < cfg.vram_headroom_warn_mib:
            add("vram_low", "warning",
                f"Nur noch {_num(free)} MiB VRAM frei (Warnschwelle {_num(cfg.vram_headroom_warn_mib)} MiB).")
        if g.temp_c is not None and g.temp_c >= cfg.temp_warn_c:
            add("gpu_hot", "warning", f"GPU-Temperatur {g.temp_c} °C (Warnschwelle {cfg.temp_warn_c} °C).")
        hits = [THROTTLE_WARN[r] for r in g.throttle_reasons if r in THROTTLE_WARN]
        if hits:
            add("gpu_throttled", "warning", f"GPU drosselt den Takt: {', '.join(hits)}.")
    if s.host is not None and s.host.ram_total_bytes:
        pct = s.host.ram_used_bytes / s.host.ram_total_bytes * 100
        if pct >= cfg.ram_warn_percent:
            add("ram_high", "warning", f"Arbeitsspeicher zu {_num(pct)} % belegt.")
    for d in s.disks or []:
        free_gib = d.free_bytes / GIB
        if free_gib < cfg.disk_warn_free_gib:
            add("disk_low", "warning", f"Laufwerk {d.mount} hat nur noch {_num(free_gib, 1)} GiB frei.", d.mount)
    for svc in s.services:
        if svc.key != "ollama" and not svc.up:          # Ollama has its own, critical warning above
            add("service_down", "warning", f"{svc.title} ist nicht erreichbar.", svc.key)
    out.sort(key=lambda w: _LEVEL_ORDER[w.level])       # stable: keeps the order above within a level
    return out


def build_snapshot(r: Readings, cfg: DashboardSettings, *, now: datetime, node_id: str,
                   simulated: list[str]) -> DashboardSnapshot:
    ollama_error = r.errors.get("ollama")
    services = [ServiceStatus(
        key="ollama", title="Ollama", up=r.ollama_online,
        http_status=200 if r.ollama_online else None, latency_ms=r.ollama_latency_ms,
        error=None if r.ollama_online or ollama_error is None else ollama_error.message,
    )]
    for p in cfg.probes:
        res = r.probes.get(p.key)
        if res is not None:                     # not probed yet only before the first medium tick
            services.append(ServiceStatus(key=p.key, title=p.title, up=res.up, http_status=res.http_status,
                                          latency_ms=res.latency_ms, error=res.error))

    host = None
    if r.host is not None:
        h = r.host
        host = HostState(
            cpu_percent=h.cpu_percent, cpu_count=h.cpu_count,
            ram_used_bytes=h.ram_used_bytes, ram_total_bytes=h.ram_total_bytes,
            uptime_s=max(0, int((now - h.boot_time).total_seconds())),
            processes=[ProcessInfo(**asdict(p)) for p in h.processes[:MAX_PROCESSES]],
        )

    models = sorted((to_loaded_model(m, cfg.offload_threshold, now) for m in r.models),
                    key=lambda m: (-m.size_bytes, m.name))      # Ollama's order is random; biggest first
    snap = DashboardSnapshot(
        ts=now, node_id=node_id, simulated=simulated,
        ollama_online=r.ollama_online, ollama_version=r.ollama_version,
        models=models,
        gpu=GpuState(**asdict(r.gpu)) if r.gpu is not None else None,
        host=host,
        disks=[DiskUsage(**asdict(d)) for d in r.disks] if r.disks is not None else None,
        services=services,
        warnings=[],
        errors=[SourceError(source=k, message=e.message, since=e.since) for k, e in sorted(r.errors.items())
                # a failed version call while Ollama is down says nothing the "ollama" error doesn't
                if r.ollama_online or k != "ollama_version"],
    )
    snap.warnings = derive_warnings(snap, cfg)
    return snap


# ---- the running service ------------------------------------------------------------------------

class DashboardService:
    def __init__(self, ctx: AppContext, cfg: DashboardSettings) -> None:
        self.ctx = ctx
        self.cfg = cfg
        self.sampler = Sampler(ctx.adapters, cfg)
        self.node_id = cfg.node_id or default_node_id(ctx.settings.adapters.ai_host)
        self._latest: DashboardSnapshot | None = None
        self._ready = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._tick_failing = False

    @property
    def latest(self) -> DashboardSnapshot | None:
        """For other modules (e.g. a catalog preflight asking for free VRAM). None before the first tick."""
        return self._latest

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="dashboard-sampler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.sampler.close()                      # background groups (probes, disks) too

    async def tick(self) -> DashboardSnapshot:
        readings = await self.sampler.sample()
        snap = build_snapshot(readings, self.cfg, now=datetime.now(timezone.utc), node_id=self.node_id,
                              simulated=self.ctx.adapters.simulated)
        self._latest = snap
        self._ready.set()
        if self.ctx.events.subscriber_count:            # nobody listening -> skip the serialization
            self.ctx.events.publish(TOPIC, snap.model_dump(mode="json", by_alias=True))
        return snap

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        next_at = loop.time()                           # first tick right away: the page wants data now
        while True:
            try:
                await self.tick()
                if self._tick_failing:
                    log.info("dashboard ticks work again")
                    self._tick_failing = False
            except Exception:
                if not self._tick_failing:              # a bug must neither end the loop nor flood the log
                    log.exception("dashboard tick failed")
                    self._tick_failing = True
            next_at += self.cfg.interval_fast_s         # fixed grid: no drift from the tick duration
            delay = next_at - loop.time()
            if delay < 0:                               # tick took longer than the interval:
                next_at, delay = loop.time(), 0         # continue from now instead of bursting to catch up
            await asyncio.sleep(delay)

    async def snapshot(self, wait_s: float = 5.0) -> DashboardSnapshot | None:
        """Latest snapshot; right after startup wait briefly for the first one instead of failing."""
        if self._latest is None:
            try:
                await asyncio.wait_for(self._ready.wait(), wait_s)
            except TimeoutError:
                return None
        return self._latest
