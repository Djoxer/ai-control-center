"""Host readings via psutil: CPU, RAM, uptime, disks and a filtered process list.

Replaces the old dashboard's PowerShell/CIM subprocess per request. psutil keeps Process objects
between calls, so per-process CPU is the average since the previous read (10 s), not a 0.0 snapshot.

Command lines can carry user paths and secrets, so they leave this module only in the requested
mode: "off", "short" (paths cut to file names, secret-looking flag values masked) or "full".
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol

import psutil

CmdlineMode = Literal["full", "short", "off"]
MAX_CMDLINE = 160                                   # characters in "short" mode

_PATH_LIKE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]|~[\\/])")      # C:\..., /..., \\server, ~/...
_SECRET_FLAG = re.compile(r"key|token|secret|passw|pwd|auth", re.IGNORECASE)


class HostUnavailable(Exception):
    pass


@dataclass(frozen=True)
class ProcessReading:
    name: str                       # "ollama.exe"
    pid: int
    cpu_percent: float | None       # share of the whole machine (0-100); None on the first sighting
    rss_bytes: int | None           # resident memory
    cmdline: str | None             # according to CmdlineMode; None if off or access denied
    ppid: int | None = None         # parent pid; lets us hide venv launchers. None in older captures


@dataclass(frozen=True)
class HostReading:
    cpu_percent: float | None       # whole machine since the previous read; None on the very first read
    cpu_count: int
    ram_total_bytes: int
    ram_used_bytes: int             # total - available: what Task Manager calls "in use"
    boot_time: datetime
    processes: tuple[ProcessReading, ...]


@dataclass(frozen=True)
class DiskReading:
    mount: str                      # "C:\\" or "/"
    total_bytes: int
    used_bytes: int
    free_bytes: int


class HostAdapter(Protocol):
    simulated: bool

    def read(self, process_patterns: Sequence[str], cmdline: CmdlineMode) -> HostReading: ...

    def disks(self, paths: Sequence[str]) -> list[DiskReading]: ...


# ---- pure helpers (unit-tested without psutil) ----------------------------------------------

def normalize_name(name: str) -> str:
    """'Ollama.EXE' -> 'ollama': patterns work the same on Windows and Linux."""
    n = name.lower()
    return n[:-4] if n.endswith(".exe") else n


def matches(name: str, patterns: Sequence[str]) -> bool:
    n = normalize_name(name)
    return any(fnmatch.fnmatchcase(n, p.lower()) for p in patterns)


def _basename(path: str) -> str:
    parts = re.split(r"[\\/]+", path.rstrip("\\/"))
    return parts[-1] if parts and parts[-1] else path


def _short_value(value: str) -> str:
    return _basename(value) if _PATH_LIKE.match(value) else value


def shorten_cmdline(argv: Sequence[str], mode: CmdlineMode) -> str | None:
    """Make a command line safe to show on a LAN dashboard.

    short: ['C:\\Users\\me\\...\\ollama.exe', 'runner', '--model', 'C:\\Users\\me\\.ollama\\blobs\\sha256-ab']
        -> 'ollama.exe runner --model sha256-ab'
    Secret-looking flags keep their name, lose their value: '--api-key abc' -> '--api-key ***'.
    """
    if mode == "off" or not argv:
        return None
    if mode == "full":
        return " ".join(argv)
    parts = [_basename(argv[0])]
    hide_next = False
    for arg in argv[1:]:
        if hide_next:
            parts.append("***")
            hide_next = False
            continue
        if arg.startswith("-"):
            flag, eq, value = arg.partition("=")
            if _SECRET_FLAG.search(flag):
                if eq:
                    parts.append(f"{flag}=***")
                else:
                    parts.append(flag)
                    hide_next = True                        # value comes as the next argument
                continue
            parts.append(f"{flag}={_short_value(value)}" if eq else arg)
            continue
        parts.append(_short_value(arg))
    text = re.sub(r"\s+", " ", " ".join(parts)).strip()      # inline scripts (python -c "...") span lines
    return text if len(text) <= MAX_CMDLINE else text[: MAX_CMDLINE - 1] + "…"


def drop_launchers(rows: Iterable[tuple[ProcessReading, str | None]]) -> list[ProcessReading]:
    """Hide launcher processes that only start a twin of themselves.

    On Windows, .venv\\Scripts\\python.exe is a small launcher: it starts the real interpreter as a
    child with the same arguments and waits. Every venv program would show up twice (one of them
    with ~4 MB RAM). A row is dropped when one of its children has the same name and the same
    arguments - the child holds the real CPU and memory numbers.

    rows: (reading, args_key) - args_key is the argument list without argv[0] (the paths differ
    between launcher and interpreter). None or "" = unknown or no arguments: never dropped, because
    a same-name child without arguments says nothing about being a launcher's twin.
    """
    items = list(rows)
    children: dict[int, list[tuple[str, str | None]]] = {}
    for r, key in items:
        if r.ppid is not None:
            children.setdefault(r.ppid, []).append((normalize_name(r.name), key))
    out: list[ProcessReading] = []
    for r, key in items:
        twin = bool(key) and (normalize_name(r.name), key) in children.get(r.pid, [])
        if not twin:
            out.append(r)
    return out


def args_key(argv: Sequence[str]) -> str:
    """Arguments without the program path: launcher and interpreter differ only in argv[0]."""
    return " ".join(argv[1:])


def stored_args_key(name: str, line: str | None) -> str | None:
    """args_key for a stored "short" line ('python.exe -m x'): strip the program name in front.

    The name may contain spaces ('ollama app.exe'), so cutting at the first space would be wrong.
    None when the line does not start with the name - then we cannot tell and never drop.
    """
    if not line or not line.lower().startswith(name.lower()):
        return None
    return line[len(name):].strip()


def existing_ancestor(path: str) -> Path | None:
    """Expand %VARS%/~ and walk up to a folder that exists - the drive is what we measure.

    Returns None when a variable stayed unexpanded (e.g. %OLLAMA_MODELS% not set): measuring
    the current working directory instead would report a random drive.
    """
    expanded = os.path.expandvars(os.path.expanduser(path))
    if "%" in expanded or "$" in expanded:
        return None
    p = Path(expanded)
    if not p.is_absolute():
        return None
    while not p.exists():
        if p.parent == p:
            return None
        p = p.parent
    return p


def mount_of(path: Path, mountpoints: Sequence[str]) -> str:
    """Longest mount point that contains path; falls back to the drive/root anchor."""
    s = str(path).replace("\\", "/").lower()             # compare with one separator style
    best, best_len = "", -1
    for m in mountpoints:
        base = m.replace("\\", "/").lower().rstrip("/")     # "C:\\" -> "c:", "/" -> ""
        inside = s == base or s.startswith(base + "/")
        # "/mnt/data2" must not count as inside "/mnt/data" - hence the separator check
        if inside and len(base) > best_len:
            best, best_len = m, len(base)
    return best or path.anchor


# ---- implementations ------------------------------------------------------------------------

class PsutilHost:
    simulated = False

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cpu_primed = False
        self._seen: set[int] = set()        # pids we have read before -> their cpu_percent is meaningful
        psutil.cpu_percent(interval=None)   # prime: the first call always returns 0.0

    def read(self, process_patterns: Sequence[str], cmdline: CmdlineMode) -> HostReading:
        with self._lock:
            cpu = psutil.cpu_percent(interval=None)
            cpu_value = cpu if self._cpu_primed else None
            self._cpu_primed = True
            vm = psutil.virtual_memory()
            ncpu = psutil.cpu_count() or 1
            rows: list[tuple[ProcessReading, str | None]] = []
            seen_now: set[int] = set()
            for p in psutil.process_iter(["name", "ppid"]):
                name = p.info.get("name") or ""
                if not process_patterns or not matches(name, process_patterns):
                    continue
                seen_now.add(p.pid)
                try:
                    with p.oneshot():
                        raw_cpu = p.cpu_percent(interval=None)
                        rss = p.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue                                   # exited meanwhile or protected system process
                try:
                    argv = p.cmdline()                         # read even in "off" mode: needed for drop_launchers
                except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                    argv = None
                line = shorten_cmdline(argv, cmdline) if argv is not None and cmdline != "off" else None
                rows.append((ProcessReading(
                    name=name, pid=p.pid,
                    cpu_percent=round(raw_cpu / ncpu, 1) if p.pid in self._seen else None,
                    rss_bytes=rss, cmdline=line, ppid=p.info.get("ppid"),
                ), args_key(argv) if argv else None))
            self._seen = seen_now
            procs = drop_launchers(rows)
            procs.sort(key=lambda r: (-(r.rss_bytes or 0), r.pid))      # biggest memory users first
            return HostReading(
                cpu_percent=cpu_value, cpu_count=ncpu,
                ram_total_bytes=vm.total, ram_used_bytes=vm.total - vm.available,
                boot_time=datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc),
                processes=tuple(procs),
            )

    def disks(self, paths: Sequence[str]) -> list[DiskReading]:
        mountpoints = [p.mountpoint for p in psutil.disk_partitions(all=False)]
        out: dict[str, DiskReading] = {}
        missing: list[str] = []
        for raw in paths:
            p = existing_ancestor(raw)
            if p is None:
                missing.append(raw)
                continue
            mount = mount_of(p, mountpoints)
            if mount in out:
                continue                                       # two paths on the same drive
            u = psutil.disk_usage(str(p))
            out[mount] = DiskReading(mount=mount, total_bytes=u.total, used_bytes=u.used, free_bytes=u.free)
        if missing and not out:
            raise HostUnavailable(f"no usable disk path: {', '.join(missing)}")
        return list(out.values())


class FakeHost:
    """Replays host.json and disks.json of a scenario folder (re-read on every call)."""
    simulated = True

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def _load(self, name: str):
        path = self.folder / name
        if not path.is_file():
            raise HostUnavailable(f"fake: {name} missing in scenario '{self.folder.name}'")
        return json.loads(path.read_text(encoding="utf-8"))

    def read(self, process_patterns: Sequence[str], cmdline: CmdlineMode) -> HostReading:
        data = self._load("host.json")
        rows = []
        for p in data.pop("processes", []):
            if not matches(p["name"], process_patterns):    # no patterns = no processes, like psutil
                continue
            stored = p.get("cmdline")
            rows.append((ProcessReading(**{**p, "cmdline": stored if cmdline != "off" else None}),
                         stored_args_key(p["name"], stored)))
        data["boot_time"] = datetime.fromisoformat(data["boot_time"])
        return HostReading(**data, processes=tuple(drop_launchers(rows)))

    def disks(self, paths: Sequence[str]) -> list[DiskReading]:
        return [DiskReading(**d) for d in self._load("disks.json")]
