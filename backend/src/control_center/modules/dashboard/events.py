"""Events: what changed between two snapshots, plus crashes from Ollama's server.log.

The detector is a diff tool, not a sensor: it compares the previous snapshot with the current one
("model X was there, now it is gone"). It never invents state - the first snapshot after start
describes the situation, nothing "happened" yet.
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from control_center.core.tail import FileTail
from control_center.modules.dashboard.schemas import (
    DashboardSnapshot, EventKind, LoadedModel, WarningLevel,
)
from control_center.modules.dashboard.settings import DashboardSettings

MAX_LINE = 240                       # crash line excerpt in the event message


@dataclass(frozen=True)
class NewEvent:
    ts: datetime
    kind: EventKind
    level: WarningLevel
    message: str
    subject: str | None = None


def _num(value: float) -> str:
    return f"{value:,.0f}".replace(",", ".")


def _downtime(delta: timedelta) -> str:
    s = int(delta.total_seconds())
    return f"{s} s" if s < 120 else f"{s // 60} min"


def _base_name(name: str) -> str:
    return name.split(":", 1)[0]


@dataclass
class QuietCounter:
    loads: Counter[str] = field(default_factory=Counter)
    since: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EventDetector:
    def __init__(self, cfg: DashboardSettings) -> None:
        self.cfg = cfg
        self.prev: DashboardSnapshot | None = None
        self.quiet = QuietCounter()
        self._down_since: datetime | None = None
        self._down_reported = False
        self._initially_down = False

    def _is_quiet(self, name: str) -> bool:
        # "nomic-embed-text" matches every tag of it; an entry with ":" matches exactly that tag
        return any(q == name or (":" not in q and q == _base_name(name)) for q in self.cfg.event_quiet_models)

    def observe(self, snap: DashboardSnapshot) -> list[NewEvent]:
        prev, self.prev = self.prev, snap
        if prev is None:
            if not snap.ollama_online:                     # started while Ollama was down
                self._down_since, self._initially_down = snap.ts, True
            return []
        out: list[NewEvent] = []
        out += self._availability(prev, snap)
        if prev.ollama_online and snap.ollama_online:     # offline -> models [] is not "unloaded"
            out += self._models(prev, snap)
        if prev.ollama_version and snap.ollama_version and prev.ollama_version != snap.ollama_version:
            out.append(NewEvent(snap.ts, "ollama_updated", "info",
                                f"Ollama-Version {prev.ollama_version} → {snap.ollama_version}"))
        return out

    # ---- Ollama up/down: a short gap is one "restarted", a long one is "down" + "up" -----------

    def _availability(self, prev: DashboardSnapshot, snap: DashboardSnapshot) -> list[NewEvent]:
        out: list[NewEvent] = []
        if prev.ollama_online and not snap.ollama_online:
            self._down_since, self._down_reported, self._initially_down = snap.ts, False, False
        if not snap.ollama_online and self._down_since and not self._down_reported:
            if snap.ts - self._down_since >= timedelta(seconds=self.cfg.restart_window_s):
                out.append(NewEvent(self._down_since, "ollama_down", "critical", "Ollama ist nicht erreichbar."))
                self._down_reported = True
        if not prev.ollama_online and snap.ollama_online and self._down_since:
            gap = _downtime(snap.ts - self._down_since)
            if self._down_reported or self._initially_down:
                out.append(NewEvent(snap.ts, "ollama_up", "info", f"Ollama ist wieder erreichbar (nach {gap})."))
            else:
                out.append(NewEvent(snap.ts, "ollama_restarted", "warning",
                                    f"Ollama wurde neu gestartet ({gap} nicht erreichbar)."))
            self._down_since, self._down_reported, self._initially_down = None, False, False
        return out

    # ---- models -------------------------------------------------------------------------------

    def _models(self, prev: DashboardSnapshot, snap: DashboardSnapshot) -> list[NewEvent]:
        out: list[NewEvent] = []
        before = {m.name: m for m in prev.models}
        after = {m.name: m for m in snap.models}
        for name, m in after.items():
            old = before.get(name)
            if old is None:
                if self._is_quiet(name):
                    self.quiet.loads[name] += 1               # embedding model during indexing: count only
                else:
                    ctx = f", Kontext {_num(m.context_length)}" if m.context_length else ""
                    out.append(NewEvent(snap.ts, "model_loaded", "info", f"{name} geladen{ctx}.", name))
                out += self._placement(None, m, snap.ts)
                continue
            if not self._is_quiet(name) and (old.digest != m.digest or old.context_length != m.context_length):
                if old.context_length != m.context_length and old.context_length and m.context_length:
                    what = f"Kontext {_num(old.context_length)} → {_num(m.context_length)}"
                else:
                    what = "neue Version"
                out.append(NewEvent(snap.ts, "model_reloaded", "info", f"{name} neu geladen: {what}.", name))
            out += self._placement(old, m, snap.ts)
        for name in before.keys() - after.keys():
            if not self._is_quiet(name):
                out.append(NewEvent(snap.ts, "model_unloaded", "info", f"{name} entladen.", name))
        return out

    @staticmethod
    def _placement(old: LoadedModel | None, new: LoadedModel, ts: datetime) -> list[NewEvent]:
        was_off = old is not None and old.placement in ("split", "cpu")
        if new.placement == "split" and (old is None or old.placement != "split"):
            pct = int((new.gpu_ratio or 0) * 100)
            return [NewEvent(ts, "offload_started", "critical",
                             f"{new.name}: Teil-Offload – nur {pct} % auf der GPU, Absturzgefahr.", new.name)]
        if new.placement == "cpu" and (old is None or old.placement != "cpu"):
            return [NewEvent(ts, "offload_started", "warning", f"{new.name} läuft komplett auf der CPU.", new.name)]
        if was_off and new.placement == "gpu":
            return [NewEvent(ts, "offload_ended", "info", f"{new.name} läuft wieder komplett auf der GPU.", new.name)]
        return []


class CrashWatcher:
    """Tails Ollama's server.log for crash markers. Blocking file I/O -> call poll() from a thread.

    One crash writes a burst of lines (CUDA error, stack, exit code): the cooldown folds them into
    one event. Only meaningful where Ollama runs - on a second PC the local log is someone else's.
    """

    def __init__(self, cfg: DashboardSettings, enabled: bool, clock=time.monotonic) -> None:
        self.cfg = cfg
        self.enabled = enabled and bool(cfg.ollama_log_paths) and bool(cfg.crash_patterns)
        self.tail = FileTail(cfg.ollama_log_paths)
        self._clock = clock
        self._last_crash: float | None = None
        self.suppressed = 0                             # matching lines folded into an earlier event

    def attach(self) -> None:
        if self.enabled:
            self.tail.attach(from_end=True)             # old crashes are history, not news

    @property
    def watching(self) -> str | None:
        return self.tail.file.name if self.enabled and self.tail.file else None

    def poll(self) -> list[str]:
        """Crash lines that start a new event (cooldown applied)."""
        if not self.enabled:
            return []
        hits = [line for line in self.tail.poll() if any(p in line for p in self.cfg.crash_patterns)]
        out = []
        for line in hits:
            now = self._clock()
            if self._last_crash is None or now - self._last_crash >= self.cfg.crash_cooldown_s:
                self._last_crash = now
                out.append(line.strip()[:MAX_LINE])
            else:
                self.suppressed += 1
        return out
