"""MCP service: one runtime per configured server - start, stop, restart, watch, ask for its tools.

Who changes a runtime:
- the API (start/stop/restart), serialized per server by an asyncio.Lock
- the monitor task: notices exits, readiness and who listens on the port
- a pending automatic restart after a crash (takes the same lock)
Everything runs on the event loop; blocking calls (psutil, Popen, joins) go through to_thread.
"""
from __future__ import annotations

import asyncio
import glob
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from control_center.core.config import config_path
from control_center.core.context import AppContext, LogSource
from control_center.modules.mcp.process import (
    Listener,
    ManagedProcess,
    PortTaken,
    ProcessLog,
    SpawnError,
    child_env,
    display_command,
    drop_record,
    find_listeners,
    kill_leftover,
    kill_tree,
    port_open,
    resolve_argv,
    resolve_cwd,
    save_record,
)
from control_center.modules.mcp.schemas import McpServerStatus, McpToolList, PortInfo, ServerState
from control_center.modules.mcp.settings import McpServerConfig, McpSettings
from control_center.modules.mcp.tools import fetch_tools

log = logging.getLogger("control_center.modules.mcp")

TOPIC = "mcp.server"                 # SSE: full McpServerStatus on every change


class UnknownServer(KeyError):
    pass


class NoUrl(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Runtime:
    """Everything we know about one configured server. Mutated only on the event loop."""

    def __init__(self, cfg: McpServerConfig, output: ProcessLog, record_path: Path) -> None:
        self.cfg = cfg
        self.log = output
        self.record_path = record_path                  # PID file: finds our process after a hard crash
        self.lock = asyncio.Lock()
        self.state: ServerState = "stopped"
        self.proc: ManagedProcess | None = None
        self.command_line = " ".join(cfg.command)       # replaced by the resolved line on the first start
        self.cwd = cfg.cwd or ""
        self.started_at: datetime | None = None
        self.stopped_at: datetime | None = None
        self.exit_code: int | None = None
        self.last_error: str | None = None
        self.recent_output: list[str] = []
        self.wanted = False                             # someone wants it running -> crash means restart
        self.restart_times: deque[float] = deque()      # monotonic times of automatic restarts
        self.restart_task: asyncio.Task | None = None
        self.ready_deadline: float | None = None
        self.port: PortInfo | None = PortInfo(port=cfg.port, open=None) if cfg.port else None
        self.tools: McpToolList | None = None
        self.revision = 0                               # bumped by every publish

    @property
    def log_source(self) -> str:
        return f"mcp-{self.cfg.key}"


class McpService:
    def __init__(self, ctx: AppContext, cfg: McpSettings) -> None:
        self.ctx = ctx
        self.cfg = cfg
        self.base = config_path().parent                # relative cwd = next to control-center.toml
        state_dir = ctx.settings.data_dir / "mcp"
        self.runtimes: dict[str, Runtime] = {}
        for s in cfg.servers:
            output = ProcessLog(ctx.settings.log_dir / f"mcp-{s.key}.log", cfg.log_max_bytes, cfg.log_backup_count)
            self.runtimes[s.key] = Runtime(s, output, state_dir / f"{s.key}.json")
        self._monitor_task: asyncio.Task | None = None
        self._background: set[asyncio.Task] = set()     # tool fetches after a start
        self._last_check = 0.0

    # ---- lifecycle of the service ----------------------------------------------------------

    async def start_up(self) -> None:
        for rt in self.runtimes.values():
            # escaped directory: '[' in a Windows user path would act as glob syntax
            pattern = os.path.join(glob.escape(str(rt.log.path.parent)), glob.escape(rt.log.path.name) + "*")
            self.ctx.log_sources[rt.log_source] = LogSource(
                key=rt.log_source, title=f"MCP: {rt.cfg.title}", format="json", paths=(pattern,))
        for rt in self.runtimes.values():
            pid = await asyncio.to_thread(kill_leftover, rt.record_path, self.cfg.stop_timeout_s)
            if pid is not None:
                log.warning("mcp server %s: stopped leftover process %s from an earlier run", rt.cfg.key, pid)
                rt.log.supervisor("WARNING", f"stopped leftover process {pid} from an earlier run")
        await self._check_ports(list(self.runtimes.values()))
        for rt in self.runtimes.values():
            if rt.cfg.autostart:
                async with rt.lock:
                    try:
                        await self._start(rt, "autostart")
                    except SpawnError:
                        pass                            # logged and shown as last_error; the module still runs
        self._monitor_task = asyncio.create_task(self._monitor(), name="mcp-monitor")

    async def shut_down(self) -> None:
        tasks = [t for t in [self._monitor_task, *self._background,
                             *(rt.restart_task for rt in self.runtimes.values())] if t]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # stop all children in parallel: n servers must not add up their stop timeouts
        await asyncio.gather(*(self._shutdown_one(rt) for rt in self.runtimes.values()), return_exceptions=True)
        for rt in self.runtimes.values():
            rt.log.close()
            self.ctx.log_sources.pop(rt.log_source, None)

    async def _shutdown_one(self, rt: Runtime) -> None:
        async with rt.lock:
            if rt.proc is not None:
                await self._stop_locked(rt, "control center shuts down")

    # ---- API operations --------------------------------------------------------------------

    def runtime(self, key: str) -> Runtime:
        try:
            return self.runtimes[key]
        except KeyError:
            raise UnknownServer(key) from None

    def statuses(self) -> list[McpServerStatus]:
        return [self.status(rt) for rt in self.runtimes.values()]

    def status(self, rt: Runtime) -> McpServerStatus:
        return McpServerStatus(
            revision=rt.revision, as_of=_now(),
            key=rt.cfg.key, title=rt.cfg.title, url=rt.cfg.url, command_line=rt.command_line, cwd=rt.cwd,
            autostart=rt.cfg.autostart, restart_on_crash=rt.cfg.restart_on_crash, state=rt.state,
            pid=rt.proc.pid if rt.proc else None, started_at=rt.started_at, stopped_at=rt.stopped_at,
            exit_code=rt.exit_code, last_error=rt.last_error, restarts=len(rt.restart_times),
            recent_output=rt.recent_output, port=rt.port, tools=rt.tools, log_source=rt.log_source,
        )

    async def start(self, key: str) -> McpServerStatus:
        rt = self.runtime(key)
        async with rt.lock:
            self._cancel_restart(rt)
            if rt.state in ("starting", "running"):
                return self.status(rt)                  # idempotent: a double click starts nothing twice
            rt.restart_times.clear()                    # a manual start gets a fresh crash budget
            await self._start(rt, "started by user")
            return self.status(rt)

    async def stop(self, key: str) -> McpServerStatus:
        rt = self.runtime(key)
        async with rt.lock:
            self._cancel_restart(rt)
            rt.wanted = False
            if rt.proc is None:
                if rt.state == "crashed":               # acknowledge the crash: back to a calm "stopped"
                    rt.state = "stopped"
                    rt.last_error = None
                    self._publish(rt)
                return self.status(rt)
            await self._stop_locked(rt, "stopped by user")
            return self.status(rt)

    async def restart(self, key: str) -> McpServerStatus:
        rt = self.runtime(key)
        async with rt.lock:
            self._cancel_restart(rt)
            if rt.proc is not None:
                await self._stop_locked(rt, "restart requested by user")
            rt.restart_times.clear()
            await self._start(rt, "restarted by user")
            return self.status(rt)

    async def tools(self, key: str) -> McpToolList:
        rt = self.runtime(key)
        if not rt.cfg.url:
            raise NoUrl(key)
        result = await fetch_tools(rt.cfg.url, self.cfg.tools_timeout_s)
        rt.tools = result
        self._publish(rt)
        return result

    # ---- start / stop (caller holds rt.lock) -----------------------------------------------

    async def _start(self, rt: Runtime, reason: str) -> None:
        cfg = rt.cfg
        try:
            cwd = resolve_cwd(cfg.cwd, self.base)
            argv = resolve_argv(cfg.command, cwd)
            rt.command_line, rt.cwd = display_command(argv), str(cwd)
            if cfg.port is not None:
                owner = await self._port_owner(cfg.port)
                if owner is not None:
                    who = f" (PID {owner.pid}, {owner.name or 'unbekannt'})" if owner.pid else ""
                    raise PortTaken(f"Port {cfg.port} ist schon belegt{who} – läuft der Server noch "
                                    f"woanders, z. B. von Hand gestartet?")
            # synchronous on purpose (a few ms): an await here could be cancelled at shutdown while the
            # OS creates the process - the new process would then belong to nobody
            proc = ManagedProcess.spawn(argv, cwd, child_env(cfg.env), rt.log)
        except SpawnError as exc:
            if not isinstance(exc, PortTaken):
                rt.last_error = str(exc)                # a taken port shows up in rt.port instead (live)
            rt.log.supervisor("ERROR", f"not started ({reason}): {exc}")
            log.warning("mcp server %s not started (%s): %s", cfg.key, reason, exc)
            self._publish(rt)
            raise
        rt.proc = proc
        rt.wanted = True
        rt.state = "starting" if cfg.port is not None else "running"   # without a port there is nothing to wait for
        rt.started_at, rt.stopped_at, rt.exit_code = _now(), None, None
        rt.last_error, rt.recent_output = None, []
        rt.ready_deadline = time.monotonic() + self.cfg.start_timeout_s
        try:
            await asyncio.to_thread(save_record, rt.record_path, proc.record())
        except OSError:
            log.exception("mcp server %s: cannot write PID file %s", cfg.key, rt.record_path)
        rt.log.supervisor("INFO", f"started ({reason}), PID {proc.pid}: {rt.command_line} (cwd {rt.cwd})")
        log.info("mcp server %s started (%s), pid %s", cfg.key, reason, proc.pid)
        self._publish(rt)

    async def _stop_locked(self, rt: Runtime, reason: str) -> None:
        proc = rt.proc
        assert proc is not None
        rt.state = "stopping"
        self._publish(rt)
        code = await asyncio.to_thread(proc.stop, self.cfg.stop_timeout_s)
        await asyncio.to_thread(proc.wait_output, 1.0)
        await asyncio.to_thread(drop_record, rt.record_path)
        rt.proc = None
        rt.state, rt.exit_code, rt.stopped_at = "stopped", code, _now()
        rt.last_error, rt.ready_deadline = None, None
        rt.log.supervisor("INFO", f"stopped ({reason}), exit code {code}")
        log.info("mcp server %s stopped (%s), exit code %s", rt.cfg.key, reason, code)
        if rt.port is not None:
            rt.port = rt.port.model_copy(update={"open": False, "pid": None, "process": None, "managed": False})
        self._publish(rt)

    # ---- crashes and automatic restarts ----------------------------------------------------

    async def _on_exit(self, rt: Runtime, code: int) -> None:
        # synchronous part first: no other coroutine can see a half-updated runtime
        proc = rt.proc
        assert proc is not None
        rt.proc = None
        rt.state, rt.exit_code, rt.stopped_at, rt.ready_deadline = "crashed", code, _now(), None
        rt.last_error = (f"Prozess ist abgestürzt (Exit-Code {code})" if code != 0
                         else "Prozess hat sich unerwartet beendet (Exit-Code 0)")
        rt.log.supervisor("ERROR" if code != 0 else "WARNING", f"exited unexpectedly, exit code {code}")
        log.warning("mcp server %s exited unexpectedly, exit code %s", rt.cfg.key, code)
        # then the slow part: last output lines, children that outlived their parent, PID file
        await asyncio.to_thread(proc.wait_output, 1.0)
        rt.recent_output = list(proc.recent)
        leftovers = await asyncio.to_thread(proc.tree)
        if leftovers:
            await asyncio.to_thread(kill_tree, leftovers, self.cfg.stop_timeout_s)
            rt.log.supervisor("WARNING", f"stopped {len(leftovers)} child process(es) left behind")
        await asyncio.to_thread(drop_record, rt.record_path)
        if rt.state == "crashed" and rt.proc is None:   # nobody started it again meanwhile
            self._schedule_restart(rt)
        self._publish(rt)

    def _schedule_restart(self, rt: Runtime) -> None:
        if not (rt.wanted and rt.cfg.restart_on_crash and self.cfg.max_restarts > 0):
            return
        now = time.monotonic()
        while rt.restart_times and now - rt.restart_times[0] > self.cfg.restart_window_s:
            rt.restart_times.popleft()
        if len(rt.restart_times) >= self.cfg.max_restarts:
            rt.wanted = False
            rt.last_error = (f"{rt.last_error} – automatischer Neustart aufgegeben "
                             f"({self.cfg.max_restarts}× in {self.cfg.restart_window_s / 60:g} min)")
            rt.log.supervisor("ERROR", "automatic restart given up")
            return
        delay = min(self.cfg.restart_backoff_s * 2 ** len(rt.restart_times), 60.0)
        rt.restart_times.append(now)
        rt.last_error = (f"{rt.last_error} – Neustart in {delay:g} s "
                         f"({len(rt.restart_times)}/{self.cfg.max_restarts})")
        rt.restart_task = asyncio.create_task(self._restart_later(rt, delay), name=f"mcp-restart-{rt.cfg.key}")

    async def _restart_later(self, rt: Runtime, delay: float) -> None:
        await asyncio.sleep(delay)
        async with rt.lock:
            rt.restart_task = None                      # before _start: never cancel ourselves
            if not rt.wanted or rt.state != "crashed" or rt.proc is not None:
                return
            try:
                await self._start(rt, "automatic restart")
            except SpawnError:
                rt.state = "crashed"
                self._schedule_restart(rt)              # counts against the same budget
                self._publish(rt)

    def _cancel_restart(self, rt: Runtime) -> None:
        if rt.restart_task is not None:
            rt.restart_task.cancel()
            rt.restart_task = None

    # ---- monitor ---------------------------------------------------------------------------

    async def _monitor(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.poll_interval_s)
            try:
                await self._tick()
            except Exception:
                log.exception("mcp monitor tick failed")    # one bad tick must not end the watching

    async def _tick(self) -> None:
        for rt in self.runtimes.values():
            if rt.proc is not None and rt.state != "stopping":
                code = rt.proc.poll()
                if code is not None:
                    await self._on_exit(rt, code)
        starting = [rt for rt in self.runtimes.values() if rt.state == "starting"]
        now = time.monotonic()
        if now - self._last_check >= self.cfg.check_interval_s:
            self._last_check = now
            await self._check_ports(list(self.runtimes.values()))
        elif starting:
            await self._check_ports(starting)           # readiness: check every tick while starting
        for rt in starting:
            if rt.state != "starting":
                continue
            if rt.port is not None and rt.port.open and rt.port.managed:
                rt.state = "running"
                rt.log.supervisor("INFO", f"ready, port {rt.cfg.port} is open")
                self._publish(rt)
                self._spawn(self._fetch_tools_quietly(rt))
            elif rt.ready_deadline is not None and now > rt.ready_deadline:
                rt.state = "running"                    # it runs, it just does not answer (yet)
                rt.last_error = (f"Port {rt.cfg.port} ist nach {self.cfg.start_timeout_s:g} s noch nicht offen "
                                 f"– Ausgabe im Protokoll prüfen")
                rt.log.supervisor("WARNING", f"port {rt.cfg.port} not open after {self.cfg.start_timeout_s:g} s")
                self._publish(rt)

    async def _check_ports(self, runtimes: list[Runtime]) -> None:
        with_port = [rt for rt in runtimes if rt.cfg.port is not None]
        if not with_port:
            return
        listeners = await asyncio.to_thread(find_listeners, {rt.cfg.port for rt in with_port})  # type: ignore[misc]
        for rt in with_port:
            port = rt.cfg.port
            assert port is not None
            owner: Listener | None
            if listeners is None:                       # OS refused the socket table: test connection
                is_open = await asyncio.to_thread(port_open, port)
                owner = None
            else:
                owner = listeners.get(port)
                is_open = owner is not None
            managed = False
            proc = rt.proc
            if is_open and proc is not None and proc.poll() is None:
                managed = True if owner is None or owner.pid is None else await asyncio.to_thread(proc.owns, owner.pid)
            fresh = PortInfo(port=port, open=is_open, pid=owner.pid if owner else None,
                             process=owner.name if owner else None, managed=managed, checked_at=_now())
            changed = rt.port is None or rt.port.model_dump(exclude={"checked_at"}) != fresh.model_dump(exclude={"checked_at"})
            rt.port = fresh
            if changed:
                self._publish(rt)

    async def _port_owner(self, port: int) -> Listener | None:
        listeners = await asyncio.to_thread(find_listeners, {port})
        if listeners is None:
            return Listener(None, None) if await asyncio.to_thread(port_open, port) else None
        return listeners.get(port)

    async def _fetch_tools_quietly(self, rt: Runtime) -> None:
        if rt.cfg.url:
            rt.tools = await fetch_tools(rt.cfg.url, self.cfg.tools_timeout_s)
            self._publish(rt)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _publish(self, rt: Runtime) -> None:
        rt.revision += 1
        self.ctx.events.publish(TOPIC, self.status(rt).model_dump(mode="json", by_alias=True))
