"""Child processes: start without a shell, write their output to a rotating log, stop the whole tree.

Plain subprocess.Popen plus threads instead of asyncio subprocesses on purpose: asyncio needs the
Proactor event loop on Windows for subprocesses, and which loop uvicorn picks depends on its version
and options. Popen works under every loop; the blocking parts (reading output, waiting for the end
of a process tree) run in worker threads.

Why the TREE matters: on Windows the python.exe of a venv is only a launcher that starts the real
interpreter as its child, and "uv run" starts python as a child too. Killing just the PID we started
would leave the real server running - and holding its port.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import psutil

log = logging.getLogger("control_center.modules.mcp")

IS_WINDOWS = os.name == "nt"
MAX_LINE_BYTES = 64 * 1024          # a child printing megabytes without a newline must not eat the RAM
RECENT_LINES = 20                   # last output lines kept in memory: shown when a process dies

# "INFO:     Uvicorn running", "ERROR:root:...", "[WARNING] ...", "level=WARN msg=..." -> level
_LEVEL_PREFIX = re.compile(r"^\[?(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\b[\]:]?", re.IGNORECASE)
_LEVEL_KV = re.compile(r"\blevel=(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\b", re.IGNORECASE)
_LEVEL_NAMES = {"WARN": "WARNING", "FATAL": "CRITICAL"}


class SpawnError(Exception):
    """A server cannot be started. The message is German: it is shown as-is in the UI."""


class PortTaken(SpawnError):
    """Someone else listens on the server's port. A state the port check shows live - not stored as last_error."""


# ---- command line -------------------------------------------------------------------------------

def expand(value: str) -> str:
    """'{python}' -> this interpreter; %VARS%, $VARS and a leading ~ expand like in log paths."""
    return os.path.expandvars(os.path.expanduser(value.replace("{python}", sys.executable)))


def resolve_cwd(cwd: str | None, base: Path) -> Path:
    path = Path(expand(cwd)) if cwd else base
    path = path if path.is_absolute() else base / path
    if not path.is_dir():
        raise SpawnError(f"Arbeitsordner fehlt: {path}")
    return path


def resolve_argv(command: list[str], cwd: Path) -> list[str]:
    """Expand placeholders and find the program, so a typo fails here with a clear message.

    Relative program paths are resolved against the server's cwd on every OS. Popen alone would
    do that on Linux but resolve them against the control center's own folder on Windows.
    """
    argv = [expand(a) for a in command]
    program = argv[0]
    if "/" in program or "\\" in program:
        path = Path(program)
        path = path if path.is_absolute() else cwd / path
        candidates = [path]
        if IS_WINDOWS and not path.suffix:
            candidates.append(path.with_suffix(".exe"))     # "…/Scripts/python" -> python.exe
        found = next((c for c in candidates if c.is_file()), None)
        if found is None:
            raise SpawnError(f"Programm nicht gefunden: {path}")
        argv[0] = str(found)
    else:
        found_name = shutil.which(program)                  # PATH (+ PATHEXT on Windows)
        if found_name is None:
            raise SpawnError(f"Programm „{program}“ nicht gefunden (nicht im PATH)")
        argv[0] = found_name
    return argv


def display_command(argv: list[str]) -> str:
    """One line to show and copy, quoted the way the OS shell of the AI box expects it."""
    return subprocess.list2cmdline(argv) if IS_WINDOWS else shlex.join(argv)


def child_env(extra: dict[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    # Python children: print() reaches the log at once (a pipe is block-buffered otherwise), and
    # umlauts do not crash on the cp1252 default of Windows pipes. Config values win.
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.update(extra)
    return env


# ---- output log ---------------------------------------------------------------------------------

def detect_level(line: str) -> str | None:
    """Best guess from the line itself; None = unknown (shown as '—' on the logs page)."""
    m = _LEVEL_PREFIX.match(line.lstrip()) or _LEVEL_KV.search(line)
    if not m:
        return "ERROR" if line.startswith("Traceback (most recent call last)") else None
    name = m.group(1).upper()
    return _LEVEL_NAMES.get(name, name)


class ProcessLog:
    """Rotating JSON-line file per server, in the format the logs module already reads.

    logger "output" = what the process printed, "supervisor" = start, stop, crash (written by us).
    Thread-safe: the handler has its own lock, the output thread and the event loop both write.
    """

    def __init__(self, path: Path, max_bytes: int, backup_count: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        # delay=True: the file is only opened on the first line, not at import of an unused server
        self._handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count,
                                            encoding="utf-8", delay=True)

    def write(self, logger: str, level: str | None, msg: str) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                 "level": level, "logger": logger, "msg": msg}
        record = logging.makeLogRecord({"msg": json.dumps(entry, ensure_ascii=False)})
        self._handler.handle(record)        # default formatter = the message itself, one line

    def output(self, line: str) -> None:
        self.write("output", detect_level(line), line)

    def supervisor(self, level: str, msg: str) -> None:
        self.write("supervisor", level, msg)

    def close(self) -> None:
        self._handler.close()               # Windows: an open file cannot be rotated or deleted


# ---- the process --------------------------------------------------------------------------------

def _has_console() -> bool:
    if not IS_WINDOWS:
        return True
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.GetConsoleWindow())   # type: ignore[attr-defined]
    except Exception:
        return True


def _popen_kwargs() -> dict:
    if IS_WINDOWS:
        # own process group: Ctrl+C in the control center window does not hit the child first,
        # the control center stops it in order. Closing the window still ends both (same console).
        flags = subprocess.CREATE_NEW_PROCESS_GROUP                  # type: ignore[attr-defined]
        if not _has_console():
            flags |= subprocess.CREATE_NO_WINDOW                     # type: ignore[attr-defined]
        return {"creationflags": flags}
    return {"start_new_session": True}       # own session: the terminal's Ctrl+C reaches only us


@dataclass(frozen=True)
class ProcessRecord:
    """What we need to recognise our own process after a hard crash of the control center."""
    pid: int
    create_time: float
    argv: list[str]


class ManagedProcess:
    def __init__(self, popen: subprocess.Popen, output: ProcessLog) -> None:
        self.popen = popen
        self.pid = popen.pid
        self.recent: deque[str] = deque(maxlen=RECENT_LINES)
        self._log = output
        self._seen: dict[int, psutil.Process] = {}     # descendants met so far, see tree()
        try:
            self.create_time: float | None = psutil.Process(self.pid).create_time()
        except psutil.Error:
            self.create_time = None             # already gone again - poll() will tell
        self._reader = threading.Thread(target=self._read_output, name=f"mcp-output-{self.pid}", daemon=True)
        self._reader.start()

    @classmethod
    def spawn(cls, argv: list[str], cwd: Path, env: dict[str, str], output: ProcessLog) -> "ManagedProcess":
        try:
            popen = subprocess.Popen(
                argv, cwd=cwd, env=env,
                stdin=subprocess.DEVNULL,                       # HTTP servers read nothing from stdin
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,   # one stream, in the order printed
                **_popen_kwargs(),
            )
        except OSError as exc:
            raise SpawnError(f"Start fehlgeschlagen: {exc.strerror or exc}") from exc
        return cls(popen, output)

    def record(self) -> ProcessRecord:
        return ProcessRecord(self.pid, self.create_time or 0.0, list(self.popen.args))  # type: ignore[arg-type]

    def poll(self) -> int | None:
        return self.popen.poll()

    def wait_output(self, timeout: float = 1.0) -> None:
        """Let the reader catch the last lines a dying process wrote (for the crash message)."""
        self._reader.join(timeout)

    def tree(self) -> list[psutil.Process]:
        """This process and its descendants, children first - including descendants seen earlier.

        Remembering matters when the root dies first (a killed venv launcher): its child, the real
        server, lives on, holds the port, and can no longer be found below the dead root. Every call
        refreshes the memory; psutil's identity check (creation time) keeps reused PIDs out.
        """
        found: dict[int, psutil.Process] = {}
        try:
            root = psutil.Process(self.pid)
            for p in [*root.children(recursive=True), root]:
                found[p.pid] = p
        except psutil.Error:
            root = None
        for pid, p in self._seen.items():
            if pid not in found and p.is_running():
                found[pid] = p
        self._seen = {pid: p for pid, p in found.items() if pid != self.pid}
        children = [p for pid, p in found.items() if pid != self.pid]
        return [*children, root] if root is not None else children

    def owns(self, pid: int) -> bool:
        return any(p.pid == pid for p in self.tree())

    def stop(self, timeout: float) -> int | None:
        """Blocking - call from a thread. Polite first, then kill what is left. Returns the exit code."""
        kill_tree(self.tree(), timeout)
        try:
            return self.popen.wait(timeout=2)
        except subprocess.TimeoutExpired:
            return None

    def _read_output(self) -> None:
        stream = self.popen.stdout
        if stream is None:
            return
        try:
            # readline with a cap: a giant line without newline is delivered in pieces
            for raw in iter(lambda: stream.readline(MAX_LINE_BYTES), b""):
                text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if not text.strip():
                    continue
                self.recent.append(text)
                self._log.output(text)
        except (OSError, ValueError):
            pass                                # pipe closed under us during shutdown
        finally:
            try:
                stream.close()
            except OSError:
                pass


def kill_tree(procs: list[psutil.Process], timeout: float) -> None:
    """terminate() everything (SIGTERM; on Windows TerminateProcess), wait, kill() what still runs.

    The list must be collected BEFORE the first terminate: once a parent is gone its children
    are re-parented and no longer found below it. psutil refuses to signal a PID that has been
    reused by another program meanwhile (it checks the creation time).
    """
    for p in procs:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    if alive:
        psutil.wait_procs(alive, timeout=2)


# ---- leftovers from a crashed control center ----------------------------------------------------

def save_record(path: Path, rec: ProcessRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": rec.pid, "createTime": rec.create_time, "argv": rec.argv}),
                    encoding="utf-8")


def drop_record(path: Path) -> None:
    path.unlink(missing_ok=True)


def kill_leftover(path: Path, timeout: float) -> int | None:
    """If the control center died without stopping its child, that child still holds the port.

    The record names PID and creation time; only a process matching BOTH is ours (Windows reuses
    PIDs quickly). Returns the PID that was stopped, None if there was nothing to do.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pid, created = int(data["pid"]), float(data["createTime"])
    except (OSError, ValueError, KeyError, TypeError):
        drop_record(path)
        return None
    try:
        proc = psutil.Process(pid)
        if abs(proc.create_time() - created) > 1.0:
            raise psutil.NoSuchProcess(pid)      # same number, different process
        procs = [*proc.children(recursive=True), proc]
    except psutil.Error:
        drop_record(path)
        return None
    kill_tree(procs, timeout)
    drop_record(path)
    return pid


# ---- who listens on a port ----------------------------------------------------------------------

@dataclass(frozen=True)
class Listener:
    pid: int | None           # None = the OS did not tell (no permission)
    name: str | None


def find_listeners(ports: set[int]) -> dict[int, Listener] | None:
    """Port -> listening process, for the given TCP ports. None = the OS refused to list sockets.

    One call for all ports: on Windows this reads the TCP table (no admin needed), much faster and
    more telling than a test connection - which waits ~2 s per refused attempt on Windows.
    """
    try:
        conns = psutil.net_connections(kind="tcp")
    except (psutil.AccessDenied, PermissionError, OSError):
        return None
    found: dict[int, Listener] = {}
    for c in conns:
        if c.status != psutil.CONN_LISTEN or not c.laddr or c.laddr.port not in ports:
            continue
        if c.laddr.port in found and found[c.laddr.port].pid is not None:
            continue                                 # IPv4 and IPv6 socket of the same process
        name = None
        if c.pid:
            try:
                name = psutil.Process(c.pid).name()
            except psutil.Error:
                pass
        found[c.laddr.port] = Listener(c.pid or None, name)
    return found


def port_open(port: int, timeout: float = 0.5) -> bool:
    """Fallback when sockets cannot be listed: try to connect locally."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False
