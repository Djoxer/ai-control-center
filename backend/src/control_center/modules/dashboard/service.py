"""Dashboard service: runs the sampler loop, turns raw readings into a DashboardSnapshot,
derives warnings, keeps the latest snapshot in memory and pushes it to SSE subscribers.

build_snapshot() and derive_warnings() are pure functions: readings in, snapshot out.
That is where the logic lives, so that is what the tests hammer.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import time
from dataclasses import asdict
from datetime import datetime, timezone

from control_center.adapters.ollama import PINNED_AFTER, RunningModel
from control_center.core.context import AppContext
from control_center.modules.dashboard.events import CrashWatcher, EventDetector, NewEvent
from control_center.modules.dashboard.repository import METRICS, DashboardRepository, SampleRow
from control_center.modules.dashboard.sampler import Readings, Sampler
from control_center.modules.dashboard.schemas import (
    CrashWatch, DashboardEvent, DashboardHistory, DashboardSnapshot, DashboardWarning, DiskUsage, EventPage,
    GpuState, HistoryMetric, HistoryRange, HistorySeries, HostState, LoadedModel, Placement, ProcessInfo,
    QuietCount, ServiceStatus, SourceError, WarningCode, WarningLevel,
)
from control_center.modules.dashboard.settings import DashboardSettings

log = logging.getLogger("control_center.modules.dashboard")

TOPIC = "dashboard.snapshot"
EVENT_TOPIC = "dashboard.event"
MAINTENANCE_S = 60                  # condense + prune once a minute
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


# ---- history ------------------------------------------------------------------------------------

# range -> (seconds back, resolution); keeps every chart between ~170 and ~1440 points
HISTORY_RANGES: dict[str, tuple[int, str]] = {
    "1h": (3600, "raw"), "6h": (6 * 3600, "minute"), "24h": (86400, "minute"),
    "7d": (7 * 86400, "hour"), "30d": (30 * 86400, "hour"),
}
UNITS = {"gpu_util": "%", "vram_used_mib": "MiB", "temp_c": "°C", "power_w": "W", "cpu_percent": "%",
         "ram_percent": "%"}


def sample_values(s: DashboardSnapshot) -> dict[str, float | None]:
    """The numbers of one snapshot that go into the history table. Missing source -> None (a gap)."""
    g, h = s.gpu, s.host
    ram = h.ram_used_bytes / h.ram_total_bytes * 100 if h and h.ram_total_bytes else None
    return {
        "gpu_util": g.util_percent if g else None,
        "vram_used_mib": g.vram_used_mib if g else None,
        "temp_c": g.temp_c if g else None,
        "power_w": g.power_w if g else None,
        "cpu_percent": h.cpu_percent if h else None,
        "ram_percent": round(ram, 2) if ram is not None else None,
    }


def scale_and_warn(metric: str, latest: DashboardSnapshot | None,
                   cfg: DashboardSettings) -> tuple[float | None, float | None]:
    """Fixed y-axis top and threshold line per metric, so the frontend needs no knowledge of limits."""
    g = latest.gpu if latest else None
    match metric:
        case "gpu_util" | "cpu_percent":
            return 100.0, None
        case "ram_percent":
            return 100.0, cfg.ram_warn_percent
        case "vram_used_mib":
            return (float(g.vram_total_mib), float(g.vram_total_mib - cfg.vram_headroom_warn_mib)) if g else (None, None)
        case "temp_c":
            return None, float(cfg.temp_warn_c)
        case "power_w":
            return (g.power_limit_w if g else None), None
    return None, None


def _rounded(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def build_history(rows: list[SampleRow], span: HistoryRange, resolution: str, step_s: int,
                  metrics: list[str], latest: DashboardSnapshot | None, cfg: DashboardSettings,
                  since: float, until: float) -> DashboardHistory:
    series = []
    for m in metrics:
        scale, warn = scale_and_warn(m, latest, cfg)
        avg = [_rounded(r.avg[m]) for r in rows]
        peak = [_rounded(r.max[m]) if r.max[m] is not None else a for r, a in zip(rows, avg, strict=True)]
        series.append(HistorySeries(metric=m, unit=UNITS[m], scale_max=scale, warn=warn, avg=avg, max=peak))
    return DashboardHistory(range=span, resolution=resolution, step_s=step_s,
                            since=datetime.fromtimestamp(since, tz=timezone.utc),
                            until=datetime.fromtimestamp(until, tz=timezone.utc),
                            ts=[datetime.fromtimestamp(r.ts, tz=timezone.utc) for r in rows], series=series)


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
        self.repo = DashboardRepository(ctx.db)
        self.detector = EventDetector(cfg)
        local = ctx.settings.adapters.ai_host in LOCAL_HOSTS
        self.crash = CrashWatcher(cfg, enabled=local)
        self._crash_reason = None if local else f"Ollama läuft auf {ctx.settings.adapters.ai_host}, das Log liegt dort"
        self._ticks = 0
        self._last_maintenance: float | None = None

    async def init_storage(self) -> None:
        """Tables + crash log position. Called once by the module startup, before start()."""
        await self.repo.create()
        await asyncio.to_thread(self.crash.attach)

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
        # everything below is bookkeeping: it runs after the live picture is out
        new = self.detector.observe(snap)
        new += await self._crashes(snap.ts)
        for ev in new:
            await self._record(ev)
        self._ticks += 1
        if (self._ticks - 1) % self.cfg.persist_every_n_ticks == 0:   # tick 1, 6, 11 ...: first row right away
            await self._store(self.repo.add_sample(snap.ts.timestamp(), sample_values(snap)))
        mono = time.monotonic()
        if self._last_maintenance is None or mono - self._last_maintenance >= MAINTENANCE_S:
            self._last_maintenance = mono
            now = time.time()
            c = self.cfg
            await self._store(self.repo.condense(now))
            await self._store(self.repo.prune(now, c.retention_raw_h, c.retention_minute_d,
                                              c.retention_hour_d, c.retention_events_d))
        return snap

    async def _store(self, op) -> bool:
        """Run one database operation; a full disk or locked file becomes a visible source error."""
        try:
            await op
        except Exception as exc:
            self.sampler.record_failure("history", exc)
            return False
        self.sampler.record_ok("history")
        return True

    async def _crashes(self, ts: datetime) -> list[NewEvent]:
        try:
            lines = await asyncio.to_thread(self.crash.poll)
        except Exception as exc:                        # log unreadable: report, keep running
            self.sampler.record_failure("ollama_log", exc)
            return []
        self.sampler.record_ok("ollama_log")
        return [NewEvent(ts, "ollama_crash", "critical", f"Absturz im Ollama-Log: {line}") for line in lines]

    async def _record(self, ev: NewEvent) -> None:
        log.log(logging.WARNING if ev.level != "info" else logging.INFO, "event %s: %s", ev.kind, ev.message)
        event_id: int | None = None
        try:
            event_id = await self.repo.add_event(ev.ts.timestamp(), ev.kind, ev.level, ev.subject, ev.message)
            self.sampler.record_ok("history")
        except Exception as exc:
            self.sampler.record_failure("history", exc)  # still shown live, just not kept
        out = DashboardEvent(id=event_id, ts=ev.ts, kind=ev.kind, level=ev.level, subject=ev.subject,
                             message=ev.message)
        self.ctx.events.publish(EVENT_TOPIC, out.model_dump(mode="json", by_alias=True))

    # ---- read API ---------------------------------------------------------------------------

    async def history(self, span: HistoryRange, metrics: list[HistoryMetric]) -> DashboardHistory:
        seconds, resolution = HISTORY_RANGES[span]
        until = time.time()
        rows = await self.repo.history(resolution, until - seconds)
        step = (self.cfg.interval_fast_s * self.cfg.persist_every_n_ticks if resolution == "raw"
                else {"minute": 60, "hour": 3600}[resolution])
        return build_history(rows, span, resolution, round(step), list(metrics) or list(METRICS),
                             self._latest, self.cfg, since=until - seconds, until=until)

    async def events(self, limit: int, before: int | None) -> EventPage:
        rows = await self.repo.list_events(limit + 1, before)          # one extra: is there more?
        more = len(rows) > limit
        rows = rows[:limit]
        events = [DashboardEvent(id=r.id, ts=datetime.fromtimestamp(r.ts, tz=timezone.utc), kind=r.kind,
                                 level=r.level, subject=r.subject, message=r.message) for r in rows]
        quiet = self.detector.quiet
        if not self.crash.enabled:
            watch = CrashWatch(active=False, file=None,
                               reason=self._crash_reason or "keine Logpfade oder Muster konfiguriert")
        elif self.crash.watching:
            watch = CrashWatch(active=True, file=self.crash.watching, reason=None)
        else:
            watch = CrashWatch(active=False, file=None, reason="keine Ollama-Logdatei gefunden")
        return EventPage(
            events=events, next_before=rows[-1].id if more and rows else None,
            quiet=[QuietCount(name=n, loads=c) for n, c in quiet.loads.most_common()],
            quiet_since=quiet.since, crash_watch=watch,
        )

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
