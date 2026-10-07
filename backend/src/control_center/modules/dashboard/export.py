"""AI-friendly exports of the dashboard: snapshot, events and history as Markdown or JSON.

Made for pasting into a chat (OpenCode, Claude, OpenWebUI) or fetching with curl, so a model gets
the state of the AI box without screenshots. Both formats start with the same header fields
(format: acc-export/v1 - versioned, so readers can rely on the shape).

- short: what a person looks at first - warnings, models, GPU, host summary, last 10 events,
  history as key figures per metric (average, peak, minimum, time above the warning line)
- full:  additionally processes, disks, quiet counts, crash detection, 50 events and the history as
  a table (Markdown, at most 60 rows) or as complete series (JSON)

Anonymize (default on): the text may leave the company in a cloud chat. Host names and IPs become
<ai-host> / <ip>, user folders <user>, command lines are dropped. Pure functions, no I/O - the
router collects the data, this module only shapes it.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import Any, Literal, TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel

from control_center.modules.dashboard.schemas import (
    DashboardEvent, DashboardHistory, DashboardSnapshot, EventPage, HistorySeries,
)

ExportFormat = Literal["md", "json"]
ExportDetail = Literal["short", "full"]
ExportPart = Literal["snapshot", "events", "history"]

FORMAT_ID = "acc-export/v1"
ALL_PARTS: tuple[ExportPart, ...] = ("snapshot", "events", "history")
EVENT_LIMIT: dict[ExportDetail, int] = {"short": 10, "full": 50}
MAX_TABLE_ROWS = 60                          # full Markdown history: more rows are merged (avg / peak)
LOCAL_NAMES = {"127.0.0.1", "localhost", "::1"}

METRIC_LABELS = {
    "gpu_util": "GPU-Last", "vram_used_mib": "VRAM", "temp_c": "Temperatur",
    "power_w": "Leistung", "cpu_percent": "CPU", "ram_percent": "Arbeitsspeicher",
}
LEVEL_WORDS = {"critical": "Kritisch", "warning": "Warnung", "info": "Hinweis"}
RESOLUTION_WORDS = {"raw": "Rohwerte", "minute": "Minutenwerte", "hour": "Stundenwerte"}
RANGE_WORDS = {"1h": "1 Stunde", "6h": "6 Stunden", "24h": "24 Stunden", "7d": "7 Tage", "30d": "30 Tage"}


def estimate_tokens(text: str) -> int:
    """Rough token count, same rule as the /dev export: characters / 4."""
    return math.ceil(len(text) / 4)


# ---- anonymizing ------------------------------------------------------------------------------

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_WIN_USER = re.compile(r"(?i)\b([a-z]:[\\/]+users[\\/]+)[^\\/\s\"']+")
_UNIX_USER = re.compile(r"(?i)(/(?:home|users)/)[^/\s\"']+")

M = TypeVar("M", bound=BaseModel)


def host_names(*urls_or_hosts: str) -> list[str]:
    """Host names to hide: plain names and the host part of URLs, without loopback names."""
    names: set[str] = set()
    for item in urls_or_hosts:
        host = urlparse(item).hostname if "://" in item else item
        if host and host.lower() not in LOCAL_NAMES:
            names.add(host)
    return sorted(names, key=len, reverse=True)          # longest first: "ai-box.lan" before "ai-box"


@dataclass
class Anonymizer:
    """Replaces names, IPs and user folders in every string; off = returns everything unchanged."""
    enabled: bool
    names: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # word boundaries: a host called "ai" must not turn "AI-Rechner" into "<ai-host>-Rechner"
        self._names = [re.compile(rf"(?i)(?<![\w.-]){re.escape(n)}(?![\w-])") for n in self.names if len(n) >= 2]

    def text(self, s: str) -> str:
        if not self.enabled:
            return s
        for pattern in self._names:
            s = pattern.sub("<ai-host>", s)
        s = _IPV4.sub("<ip>", s)
        s = _WIN_USER.sub(r"\1<user>", s)
        return _UNIX_USER.sub(r"\1<user>", s)

    def walk(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {k: self.walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.walk(v) for v in value]
        return value

    def model(self, obj: M) -> M:
        """Same model with every string cleaned (round trip through a dict keeps types and validation)."""
        if not self.enabled:
            return obj
        return type(obj).model_validate(self.walk(obj.model_dump()))


# ---- input ----------------------------------------------------------------------------------------

@dataclass
class ExportData:
    generated: datetime
    version: str
    node: str
    detail: ExportDetail
    parts: tuple[ExportPart, ...]
    anonymized: bool
    snapshot: DashboardSnapshot | None = None        # None = no measurement yet (or part not asked for)
    events: EventPage | None = None
    history: DashboardHistory | None = None


def prepare(data: ExportData, anon: Anonymizer) -> ExportData:
    """Applies the anonymizer and the detail level once, so both renderers see the same data."""
    snap = data.snapshot
    if snap is not None:
        snap = anon.model(snap)
        if anon.enabled and snap.host is not None:
            # command lines can carry paths, tokens, ports - with anonymize they never leave the box
            snap.host.processes = [p.model_copy(update={"cmdline": None}) for p in snap.host.processes]
    events = anon.model(data.events) if data.events is not None else None
    if events is not None:
        events = events.model_copy(update={"events": events.events[:EVENT_LIMIT[data.detail]]})
    return ExportData(
        generated=data.generated, version=data.version, node=anon.text(data.node), detail=data.detail,
        parts=data.parts, anonymized=anon.enabled, snapshot=snap, events=events, history=data.history,
    )


# ---- history key figures -------------------------------------------------------------------------

@dataclass
class MetricStats:
    metric: str
    unit: str
    avg: float | None
    max: float | None
    min: float | None
    last: float | None
    warn: float | None
    above_warn_s: int | None          # time with the peak above the warning line; None = no line
    samples: int                      # buckets with a value (gaps = source failed or server off)


def metric_stats(series: HistorySeries, step_s: int) -> MetricStats:
    avgs = [v for v in series.avg if v is not None]
    peaks = [v for v in series.max if v is not None]
    above = None
    if series.warn is not None:
        above = sum(step_s for v in series.max if v is not None and v > series.warn)
    return MetricStats(
        metric=series.metric, unit=series.unit,
        avg=round(sum(avgs) / len(avgs), 2) if avgs else None,
        max=max(peaks) if peaks else None,
        min=min(avgs) if avgs else None,
        last=avgs[-1] if avgs else None,
        warn=series.warn, above_warn_s=above, samples=len(avgs),
    )


def merge_rows(history: DashboardHistory, limit: int = MAX_TABLE_ROWS
               ) -> tuple[int, list[tuple[datetime, dict[str, float | None]]]]:
    """At most `limit` rows: neighbouring buckets are merged (average of averages). Returns (step_s, rows)."""
    n = len(history.ts)
    group = max(1, math.ceil(n / limit))
    rows = []
    for start in range(0, n, group):
        values: dict[str, float | None] = {}
        for s in history.series:
            chunk = [v for v in s.avg[start:start + group] if v is not None]
            values[s.metric] = round(sum(chunk) / len(chunk), 1) if chunk else None
        rows.append((history.ts[start], values))
    return history.step_s * group, rows


# ---- number and time formatting (German, like the UI) ------------------------------------------

def num(value: float | None, digits: int = 0) -> str:
    """1234.5 -> '1.234,5' (digits=1); None -> '–'."""
    if value is None:
        return "–"
    text = f"{value:,.{digits}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def gib(n_bytes: int | None) -> str:
    return "–" if n_bytes is None else f"{num(n_bytes / 1024 ** 3, 1)} GiB"


def size(n_bytes: int | None) -> str:
    """Memory of a process: MiB below 1 GiB (a 20 MB process is not "0,0 GiB")."""
    if n_bytes is None:
        return "–"
    return gib(n_bytes) if n_bytes >= 1024 ** 3 else f"{num(n_bytes / 1024 ** 2)} MiB"


def duration(seconds: float) -> str:
    """Short German duration: 45 s, 12 min, 3 h 20 min, 4 T 2 h."""
    s = int(seconds)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    if s < 86400:
        h, m = divmod(s // 60, 60)
        return f"{h} h {m} min" if m else f"{h} h"
    d, h = divmod(s // 3600, 24)
    return f"{d} T {h} h" if h else f"{d} T"


def cell(text: str) -> str:
    """Table cell: pipes and line breaks would break the Markdown table."""
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def table(head: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows]
    return lines


# ---- Markdown ---------------------------------------------------------------------------------

def to_markdown(data: ExportData, tz: tzinfo | None = None) -> str:
    """Markdown with a YAML header. Times in the server's local time zone (tz for tests)."""
    def local(ts: datetime) -> datetime:
        return ts.astimezone(tz)

    gen = local(data.generated)
    snap = data.snapshot
    out = ["---", f"format: {FORMAT_ID}", "kind: dashboard", f"detail: {data.detail}",
           f"generated: {gen.isoformat(timespec='seconds')}", f"version: {data.version}",
           f"node: \"{data.node}\"", f"anonymized: {'true' if data.anonymized else 'false'}",
           f"parts: [{', '.join(data.parts)}]"]
    if snap and snap.simulated:
        out.append(f"simulated: [{', '.join(snap.simulated)}]")
    if data.history is not None:
        out.append(f"history_range: {data.history.range}")
    out += ["---", "", f"# AI-Rechner {data.node} · {gen:%d.%m.%Y %H:%M}", ""]
    if snap and snap.simulated:
        out += [f"> Simulierte Daten (keine echten Messwerte): {', '.join(snap.simulated)}", ""]

    if "snapshot" in data.parts:
        out += _md_snapshot(snap, data.detail, local)
    if "events" in data.parts:
        out += _md_events(data.events, data.detail, local)
    if "history" in data.parts:
        out += _md_history(data.history, data.detail, local)
    return "\n".join(out).rstrip() + "\n"


def _md_snapshot(snap: DashboardSnapshot | None, detail: ExportDetail, local) -> list[str]:
    if snap is None:
        return ["## Zustand", "", "Noch kein Messwert - das Backend ist gerade gestartet.", ""]
    out = [f"## Zustand ({local(snap.ts):%H:%M:%S})", "", "### Warnungen", ""]
    if snap.warnings:
        out += [f"- **{LEVEL_WORDS[w.level]}:** {w.message}" for w in snap.warnings]
    else:
        out.append("Keine Warnungen.")
    out += ["", "### Geladene Modelle", ""]
    if snap.models:
        rows = []
        for m in snap.models:
            unload = "angepinnt" if m.pinned else (f"{local(m.expires_at):%H:%M}" if m.expires_at else "–")
            ratio = "–" if m.gpu_ratio is None else f"{num(m.gpu_ratio * 100)} %"
            rows.append([m.name, gib(m.size_bytes), ratio, m.placement,
                         num(m.context_length) if m.context_length else "–", unload])
        out += table(["Modell", "Größe", "auf GPU", "Platzierung", "Kontext", "entlädt"], rows)
    else:
        out.append("Keine Modelle geladen.")

    out += ["", "### GPU", ""]
    g = snap.gpu
    if g is None:
        out.append("Keine GPU-Werte (Quelle ausgefallen, siehe Fehler).")
    else:
        free = g.vram_total_mib - g.vram_used_mib
        out += [
            f"- {g.name}" + (f", Treiber {g.driver_version}" if g.driver_version else ""),
            f"- Auslastung {num(g.util_percent)} %, VRAM {num(g.vram_used_mib)} / {num(g.vram_total_mib)} MiB "
            f"(frei {num(free)} MiB)",
            f"- {num(g.temp_c)} °C, {num(g.power_w)} / {num(g.power_limit_w)} W, Lüfter {num(g.fan_percent)} %",
            f"- Drosselung: {', '.join(g.throttle_reasons) if g.throttle_reasons else 'keine'}",
        ]

    out += ["", "### Ollama und Dienste", "",
            f"- Ollama {'online' if snap.ollama_online else 'offline'}"
            + (f", Version {snap.ollama_version}" if snap.ollama_version else ""), ""]
    out += table(["Dienst", "Status", "Antwortzeit"], [
        [s.title, "erreichbar" if s.up else f"nicht erreichbar ({s.error or s.http_status or '?'})",
         f"{num(s.latency_ms)} ms" if s.latency_ms is not None else "–"]
        for s in snap.services
    ])

    out += ["", "### Rechner", ""]
    h = snap.host
    if h is None:
        out.append("Keine Rechnerwerte (Quelle ausgefallen, siehe Fehler).")
    else:
        ram_pct = h.ram_used_bytes / h.ram_total_bytes * 100 if h.ram_total_bytes else None
        out.append(f"- CPU {num(h.cpu_percent)} % ({h.cpu_count} Kerne), RAM {gib(h.ram_used_bytes)} / "
                   f"{gib(h.ram_total_bytes)} ({num(ram_pct)} %), Laufzeit {duration(h.uptime_s)}")
        if detail == "full" and h.processes:
            out += ["", "Prozesse:", ""]
            out += table(["Name", "PID", "CPU", "RAM", "Befehlszeile"], [
                [p.name, str(p.pid), f"{num(p.cpu_percent, 1)} %", size(p.rss_bytes), p.cmdline or "–"]
                for p in h.processes
            ])
    if detail == "full" and snap.disks:
        out += ["", "Laufwerke:", ""]
        out += table(["Laufwerk", "belegt", "frei", "gesamt"], [
            [d.mount, gib(d.used_bytes), gib(d.free_bytes), gib(d.total_bytes)] for d in snap.disks
        ])
    if snap.errors:
        out += ["", "### Quellen mit Fehlern", ""]
        out += [f"- {e.source}: {e.message} (seit {local(e.since):%d.%m. %H:%M})" for e in snap.errors]
    return out + [""]


def _md_events(page: EventPage | None, detail: ExportDetail, local) -> list[str]:
    if page is None:
        return ["## Ereignisse", "", "Ereignisse nicht verfügbar.", ""]
    out = [f"## Ereignisse (neueste zuerst, höchstens {EVENT_LIMIT[detail]})", ""]
    if page.events:
        out += table(["Zeit", "Stufe", "Meldung"], [_event_row(e, local) for e in page.events])
    else:
        out.append("Keine Ereignisse gespeichert.")
    if detail == "full":
        if page.quiet:
            counts = ", ".join(f"{q.name} {q.loads}×" for q in page.quiet)
            out += ["", f"Nur gezählt seit {local(page.quiet_since):%d.%m. %H:%M}: {counts}"]
        cw = page.crash_watch
        out += ["", "Absturz-Erkennung: " + (f"aktiv ({cw.file})" if cw.active else f"aus - {cw.reason}")]
    return out + [""]


def _event_row(e: DashboardEvent, local) -> list[str]:
    return [f"{local(e.ts):%d.%m. %H:%M:%S}", LEVEL_WORDS[e.level], e.message]


def _md_history(history: DashboardHistory | None, detail: ExportDetail, local) -> list[str]:
    if history is None:
        return ["## Verlauf", "", "Verlauf nicht verfügbar.", ""]
    out = [f"## Verlauf: {RANGE_WORDS[history.range]} ({RESOLUTION_WORDS[history.resolution]}, "
           f"alle {duration(history.step_s)})", ""]
    if not history.ts:
        return out + ["Noch keine gespeicherten Werte in diesem Zeitraum.", ""]
    rows = []
    for s in history.series:
        st = metric_stats(s, history.step_s)
        digits = 1 if s.unit in ("°C", "W") else 0
        if st.above_warn_s is None:
            warn = "–"
        else:
            warn = f"{duration(st.above_warn_s) if st.above_warn_s else 'nie'} (Schwelle {num(st.warn)})"
        rows.append([f"{METRIC_LABELS.get(s.metric, s.metric)} ({s.unit})", num(st.avg, digits),
                     num(st.max, digits), num(st.min, digits), num(st.last, digits), warn])
    out += table(["Messwert", "Ø", "Spitze", "Min", "Zuletzt", "über Warnschwelle"], rows)
    count = len(history.ts)
    out += ["", f"{count} {'Abschnitt' if count == 1 else 'Abschnitte'} von {local(history.since):%d.%m. %H:%M} bis "
                f"{local(history.until):%d.%m. %H:%M}. Ø und Min aus den Abschnittsmitteln, Spitze aus den "
                "Abschnittsspitzen."]
    if detail == "full":
        step, merged = merge_rows(history)
        head = ["Zeit"] + [f"{METRIC_LABELS.get(s.metric, s.metric)} {s.unit}" for s in history.series]
        out += ["", f"### Zeitreihe (Mittelwerte je {duration(step)})", ""]
        out += table(head, [[f"{local(ts):%d.%m. %H:%M}"] + [num(v.get(s.metric), 1) for s in history.series]
                            for ts, v in merged])
    return out + [""]


# ---- JSON -------------------------------------------------------------------------------------

def to_json(data: ExportData) -> dict[str, Any]:
    """Same header fields as the Markdown; numbers stay numbers, keys camelCase like the API."""
    doc: dict[str, Any] = {
        "format": FORMAT_ID, "kind": "dashboard", "detail": data.detail,
        "generatedAt": data.generated.isoformat(timespec="seconds"), "version": data.version,
        "node": data.node, "anonymized": data.anonymized, "parts": list(data.parts),
    }
    if "snapshot" in data.parts:
        snap = data.snapshot
        if snap is None:
            doc["snapshot"] = None
        else:
            dumped = snap.model_dump(mode="json", by_alias=True)
            if data.detail == "short":
                if dumped.get("host"):
                    dumped["host"].pop("processes", None)
                dumped.pop("disks", None)
            doc["snapshot"] = dumped
    if "events" in data.parts:
        page = data.events
        if page is None:
            doc["events"] = None
        else:
            ev: dict[str, Any] = {"items": [e.model_dump(mode="json", by_alias=True) for e in page.events]}
            if data.detail == "full":
                ev["quiet"] = [q.model_dump(mode="json", by_alias=True) for q in page.quiet]
                ev["quietSince"] = page.quiet_since.isoformat(timespec="seconds")
                ev["crashWatch"] = page.crash_watch.model_dump(mode="json", by_alias=True)
            doc["events"] = ev
    if "history" in data.parts:
        h = data.history
        if h is None:
            doc["history"] = None
        else:
            hist: dict[str, Any] = {
                "range": h.range, "resolution": h.resolution, "stepS": h.step_s,
                "since": h.since.isoformat(timespec="seconds"), "until": h.until.isoformat(timespec="seconds"),
                "points": len(h.ts),
                "metrics": [_stats_json(metric_stats(s, h.step_s)) for s in h.series],
            }
            if data.detail == "full":
                hist["ts"] = [t.isoformat(timespec="seconds") for t in h.ts]
                hist["series"] = [s.model_dump(mode="json", by_alias=True) for s in h.series]
            doc["history"] = hist
    return doc


def _stats_json(st: MetricStats) -> dict[str, Any]:
    return {"metric": st.metric, "unit": st.unit, "avg": st.avg, "max": st.max, "min": st.min,
            "last": st.last, "warn": st.warn, "aboveWarnS": st.above_warn_s, "samples": st.samples}
