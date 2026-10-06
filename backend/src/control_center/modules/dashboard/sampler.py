"""Sampler: asks the adapters on three clocks and keeps the latest raw readings.

Like a ward round: the pulse (loaded models, GPU) every 2 s, blood pressure (CPU/RAM, services)
every 10 s, the patient file (versions, disks) once a minute. Every call has its own timeout.
A source that fails becomes None plus an entry in `errors` (with the time the outage started).

Only the fast group is awaited by the tick. Medium and slow groups run as background tasks and
land in the next snapshot: on Windows a connect to a closed port takes ~2 s, and a stopped Qdrant
must not make every fifth GPU reading late. A group still running when it is due again is
skipped, not stacked (no pile-up of hanging probes or threads).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import psutil

from control_center.adapters.common import describe
from control_center.adapters.gpu import GpuReading, GpuUnavailable
from control_center.adapters.host import DiskReading, HostReading, HostUnavailable
from control_center.adapters.ollama import OllamaUnavailable, RunningModel
from control_center.adapters.probe import ProbeResult, probe
from control_center.adapters.registry import Adapters
from control_center.modules.dashboard.settings import DashboardSettings, ProbeConfig

log = logging.getLogger("control_center.modules.dashboard")

# failures that are "the world", not our bug -> one warning line without traceback
EXPECTED_ERRORS = (OllamaUnavailable, GpuUnavailable, HostUnavailable, TimeoutError, OSError, psutil.Error)
TIMEOUT_GRACE_S = 0.5               # httpx has the same timeout; let its more precise message win the race

_FAILED: Any = object()             # sentinel: distinguishes "call failed" from a legitimate None result


@dataclass
class ErrorState:
    message: str
    since: datetime                 # first failure of the current outage


@dataclass
class Readings:
    ollama_online: bool = False
    ollama_latency_ms: float | None = None
    models: list[RunningModel] = field(default_factory=list)
    ollama_version: str | None = None           # last known, kept while Ollama is offline
    gpu: GpuReading | None = None
    host: HostReading | None = None
    disks: list[DiskReading] | None = None
    probes: dict[str, ProbeResult] = field(default_factory=dict)
    errors: dict[str, ErrorState] = field(default_factory=dict)


class Sampler:
    def __init__(self, adapters: Adapters, cfg: DashboardSettings,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.adapters = adapters
        self.cfg = cfg
        self._clock = clock                     # injectable: tests move time without sleeping
        self.readings = Readings()
        self._last_medium: float | None = None
        self._last_slow: float | None = None
        self._background: dict[str, asyncio.Task] = {}      # "medium" / "slow" -> running group

    @property
    def _timeout(self) -> float:
        return self.adapters.cfg.timeout_s + TIMEOUT_GRACE_S

    def _due(self, last: float | None, interval: float, now: float) -> bool:
        # half a fast tick of tolerance: a run 9.98 s after the last one counts as "10 s are over";
        # without it, timer jitter would push every medium run one whole fast tick later
        return last is None or now - last >= interval - self.cfg.interval_fast_s / 2

    def _launch(self, group: str, fn: Callable[[], Awaitable[None]]) -> bool:
        running = self._background.get(group)
        if running is not None and not running.done():
            return False                        # previous run still busy: skip, retry next tick
        self._background[group] = asyncio.create_task(fn(), name=f"dashboard-{group}")
        return True

    async def settle(self) -> None:
        """Wait for running background groups. Used for the first snapshot and by tests."""
        pending = [t for t in self._background.values() if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def close(self) -> None:
        for task in self._background.values():
            task.cancel()
        await self.settle()

    async def sample(self) -> Readings:
        now = self._clock()
        r = self.readings
        first = self._last_medium is None
        was_online = r.ollama_online
        if (self._due(self._last_medium, self.cfg.interval_medium_s, now)
                and self._launch("medium", self._medium_group)):
            self._last_medium = now
        slow_started = (self._due(self._last_slow, self.cfg.interval_slow_s, now)
                        and self._launch("slow", self._slow_group))
        if slow_started:
            self._last_slow = now
        await asyncio.gather(self._ollama_ps(), self._gpu())
        if first:
            await self.settle()                 # the very first snapshot is complete, not half empty
        if r.ollama_online and not was_online and not slow_started:
            await self._ollama_version()        # Ollama (re)started, maybe updated: don't wait a minute
        return r

    async def _medium_group(self) -> None:
        await asyncio.gather(self._host(), self._probes())

    async def _slow_group(self) -> None:
        await asyncio.gather(self._ollama_version(), self._disks())

    # ---- error bookkeeping ----------------------------------------------------------------

    async def _call(self, source: str, fn: Callable[[], Awaitable[Any]]) -> Any:
        """Run one adapter call with timeout. Never raises; returns _FAILED and records the outage."""
        try:
            result = await asyncio.wait_for(fn(), self._timeout)
        except Exception as exc:
            self.record_failure(source, exc)
            return _FAILED
        self.record_ok(source)
        return result

    def record_ok(self, source: str) -> None:
        if self.readings.errors.pop(source, None) is not None:
            log.info("dashboard source %s is back", source)

    def record_failure(self, source: str, exc: Exception) -> None:
        """Also used by the service for its own sources (history database)."""
        message = (f"no answer within {self._timeout:g} s" if isinstance(exc, TimeoutError)
                   else describe(exc))
        state = self.readings.errors.get(source)
        if state is None:
            # once per outage, not every 2 s; tracebacks only for the unexpected (= our bugs)
            log.warning("dashboard source %s failed: %s", source, message,
                        exc_info=not isinstance(exc, EXPECTED_ERRORS))
            self.readings.errors[source] = ErrorState(message, datetime.now(timezone.utc))
        else:
            state.message = message             # keep `since`, update the text

    # ---- the individual sources -----------------------------------------------------------

    async def _ollama_ps(self) -> None:
        r = self.readings
        start = time.perf_counter()
        models = await self._call("ollama", self.adapters.ollama.running)
        if models is _FAILED:
            r.ollama_online, r.models, r.ollama_latency_ms = False, [], None
        else:
            r.ollama_online, r.models = True, models
            r.ollama_latency_ms = round((time.perf_counter() - start) * 1000, 1)

    async def _ollama_version(self) -> None:
        version = await self._call("ollama_version", self.adapters.ollama.version)
        if version is not _FAILED:
            self.readings.ollama_version = version

    async def _gpu(self) -> None:
        gpu = self.adapters.gpu
        if gpu is None:                         # [adapters] gpu = "none": no GPU, and that's fine
            return
        reading = await self._call("gpu", lambda: asyncio.to_thread(gpu.read))
        self.readings.gpu = None if reading is _FAILED else reading

    async def _host(self) -> None:
        host, cfg = self.adapters.host, self.cfg
        reading = await self._call(
            "host", lambda: asyncio.to_thread(host.read, cfg.process_names, cfg.process_cmdline))
        self.readings.host = None if reading is _FAILED else reading

    async def _disks(self) -> None:
        host, paths = self.adapters.host, self.cfg.disk_paths
        disks = await self._call("disks", lambda: asyncio.to_thread(host.disks, paths))
        self.readings.disks = None if disks is _FAILED else disks

    async def _probes(self) -> None:
        async def one(p: ProbeConfig) -> tuple[str, ProbeResult]:
            try:
                url = self.adapters.cfg.expand(p.url)
                return p.key, await asyncio.wait_for(probe(self.adapters.http, url), self._timeout)
            except Exception as exc:            # probe() handles HTTP errors itself; this is the safety net
                return p.key, ProbeResult(up=False, http_status=None, latency_ms=None, error=describe(exc))

        results = await asyncio.gather(*(one(p) for p in self.cfg.probes))
        self.readings.probes = dict(results)
