"""Exports: anonymizing, key figures, Markdown/JSON shape, and the two endpoints end to end."""
import json
import math
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from control_center.main import create_app
from control_center.modules.dashboard import export as ex
from control_center.modules.dashboard.schemas import (
    CrashWatch, DashboardEvent, DashboardHistory, DashboardSnapshot, EventPage, GpuState, HistorySeries,
    HostState, ProcessInfo, ServiceStatus,
)

UTC = timezone.utc
T0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


# ---- test data ------------------------------------------------------------------------------------

def snapshot(**over) -> DashboardSnapshot:
    base = dict(
        ts=T0, node_id="ai-box", simulated=[], ollama_online=True, ollama_version="0.35.0", models=[],
        gpu=GpuState(name="RTX 5070 Ti", driver_version="610.62", util_percent=41, vram_used_mib=9580,
                     vram_total_mib=16303, temp_c=62, power_w=120.5, power_limit_w=300, fan_percent=30,
                     throttle_reasons=[]),
        host=HostState(cpu_percent=12, cpu_count=16, ram_used_bytes=18 * 1024 ** 3, ram_total_bytes=32 * 1024 ** 3,
                       uptime_s=3 * 86400 + 4 * 3600, processes=[
                           ProcessInfo(name="ollama.exe", pid=1, cpu_percent=1.5, rss_bytes=20 * 1024 ** 2,
                                       cmdline="ollama.exe serve --token abc"),
                       ]),
        disks=[], services=[ServiceStatus(key="qdrant", title="Qdrant", up=False, http_status=None, latency_ms=None,
                                          error="ConnectError http://192.168.5.54:6333 | refused")],
        warnings=[], errors=[],
    )
    return DashboardSnapshot(**{**base, **over})


def history(n: int = 3, step_s: int = 10, warn: float | None = 80.0) -> DashboardHistory:
    ts = [T0 + timedelta(seconds=i * step_s) for i in range(n)]
    temp = HistorySeries(metric="temp_c", unit="°C", scale_max=None, warn=warn,
                         avg=[70.0, None, 90.0][:n] + [75.0] * max(0, n - 3),
                         max=[72.0, None, 95.0][:n] + [76.0] * max(0, n - 3))
    return DashboardHistory(range="1h", since=T0, until=T0 + timedelta(hours=1), resolution="raw", step_s=step_s,
                            ts=ts, series=[temp])


def events(n: int = 3) -> EventPage:
    evs = [DashboardEvent(id=i, ts=T0, kind="model_loaded", level="info", subject="m",
                          message=f"Modell {i} geladen auf ai-box") for i in range(n)]
    return EventPage(events=evs, next_before=None, quiet=[], quiet_since=T0,
                     crash_watch=CrashWatch(active=False, file=None, reason="aus"))


def data(detail: ex.ExportDetail = "short", parts=ex.ALL_PARTS, **over) -> ex.ExportData:
    base = dict(generated=T0, version="0.7.0", node="ai-box", detail=detail, parts=tuple(parts), anonymized=False,
                snapshot=snapshot(), events=events(), history=history())
    return ex.ExportData(**{**base, **over})


# ---- anonymizing ------------------------------------------------------------------------------

def test_anonymizer_replaces_names_ips_and_user_folders():
    anon = ex.Anonymizer(True, ["ai-box", "ai"])
    text = ("ai-box at 192.168.5.54, AI-Rechner, Ollama 0.35.0, "
            r"C:\Users\Djoxer\AppData\Ollama\server.log, /home/djoxer/x, http://ai:3000")
    assert anon.text(text) == (r"<ai-host> at <ip>, AI-Rechner, Ollama 0.35.0, "
                               r"C:\Users\<user>\AppData\Ollama\server.log, /home/<user>/x, http://<ai-host>:3000")


def test_anonymizer_off_changes_nothing():
    anon = ex.Anonymizer(False, ["ai-box"])
    assert anon.text("ai-box 10.0.0.1") == "ai-box 10.0.0.1"
    snap = snapshot()
    assert anon.model(snap) is snap


def test_host_names_from_urls_without_loopback():
    names = ex.host_names("ai-box", "127.0.0.1", "http://localhost:11434", "http://192.168.5.54:3000/health",
                          "http://ai-box.lan:6333")
    assert names == ["192.168.5.54", "ai-box.lan", "ai-box"]       # longest first


def test_prepare_drops_command_lines_and_cuts_events():
    out = ex.prepare(data(events=events(30)), ex.Anonymizer(True, ["ai-box"]))
    assert out.node == "<ai-host>" and out.anonymized is True
    assert out.snapshot.node_id == "<ai-host>"
    assert out.snapshot.host.processes[0].cmdline is None
    assert out.snapshot.services[0].error == "ConnectError http://<ip>:6333 | refused"
    assert len(out.events.events) == ex.EVENT_LIMIT["short"]
    assert out.events.events[0].message == "Modell 0 geladen auf <ai-host>"

    kept = ex.prepare(data(), ex.Anonymizer(False, ["ai-box"]))
    assert kept.snapshot.host.processes[0].cmdline == "ollama.exe serve --token abc"


# ---- key figures ------------------------------------------------------------------------------

def test_metric_stats_ignores_gaps_and_counts_time_above_warning():
    st = ex.metric_stats(history().series[0], step_s=10)
    assert (st.avg, st.max, st.min, st.last, st.samples) == (80.0, 95.0, 70.0, 90.0, 2)
    assert st.above_warn_s == 10                                     # one bucket peaked above 80


def test_metric_stats_without_warning_line_or_values():
    assert ex.metric_stats(history(warn=None).series[0], 10).above_warn_s is None
    empty = HistorySeries(metric="cpu_percent", unit="%", scale_max=100, warn=None, avg=[None], max=[None])
    st = ex.metric_stats(empty, 10)
    assert (st.avg, st.max, st.min, st.last, st.samples) == (None, None, None, None, 0)


def test_merge_rows_keeps_at_most_the_limit():
    h = history(n=360)
    step, rows = ex.merge_rows(h)
    assert len(rows) == 60 and step == 60                            # 6 buckets of 10 s per row
    assert rows[0][0] == T0
    assert rows[0][1]["temp_c"] == pytest.approx((70 + 90 + 75 * 3) / 5, abs=0.05)   # gap skipped
    assert ex.merge_rows(history(n=3)) == (10, [(t, {"temp_c": v}) for t, v in zip(h.ts[:3], [70.0, None, 90.0])])


@pytest.mark.parametrize("value, digits, text", [(1234.5, 1, "1.234,5"), (9580, 0, "9.580"), (None, 0, "–")])
def test_num_german(value, digits, text):
    assert ex.num(value, digits) == text


@pytest.mark.parametrize("seconds, text", [(45, "45 s"), (600, "10 min"), (3600, "1 h"), (12000, "3 h 20 min"),
                                           (3 * 86400 + 4 * 3600, "3 T 4 h"), (86400, "1 T")])
def test_duration(seconds, text):
    assert ex.duration(seconds) == text


def test_size_switches_to_mib_below_one_gib():
    assert ex.size(20 * 1024 ** 2) == "20 MiB"
    assert ex.size(int(4.2 * 1024 ** 3)) == "4,2 GiB"


# ---- Markdown ---------------------------------------------------------------------------------

def test_markdown_starts_with_yaml_header():
    md = ex.to_markdown(data(), tz=UTC)
    head = md.split("---\n")[1]
    assert head.splitlines() == [
        "format: acc-export/v1", "kind: dashboard", "detail: short", "generated: 2026-10-07T12:00:00+00:00",
        "version: 0.7.0", 'node: "ai-box"', "anonymized: false", "parts: [snapshot, events, history]",
        "history_range: 1h",
    ]
    assert "# AI-Rechner ai-box · 07.10.2026 12:00" in md


def test_markdown_short_and_full():
    short = ex.to_markdown(data(), tz=UTC)
    assert "Keine Warnungen." in short and "Keine Modelle geladen." in short
    assert "| Temperatur (°C) | 80,0 | 95,0 | 70,0 | 90,0 | 10 s (Schwelle 80) |" in short
    assert "Prozesse:" not in short and "### Zeitreihe" not in short
    assert "ConnectError http://192.168.5.54:6333 \\| refused" in short       # pipe escaped inside the table

    full = ex.to_markdown(data("full"), tz=UTC)
    assert "Prozesse:" in full and "ollama.exe serve --token abc" in full and "20 MiB" in full
    assert "### Zeitreihe (Mittelwerte je 10 s)" in full
    assert "Absturz-Erkennung: aus - aus" in full


def test_markdown_only_the_requested_parts():
    md = ex.to_markdown(data(parts=("history",)), tz=UTC)
    assert "## Verlauf" in md and "## Zustand" not in md and "## Ereignisse" not in md


def test_markdown_before_the_first_measurement():
    md = ex.to_markdown(data(snapshot=None), tz=UTC)
    assert "Noch kein Messwert" in md


# ---- JSON -------------------------------------------------------------------------------------

def test_json_short_leaves_out_processes_disks_and_series():
    doc = ex.to_json(data())
    assert doc["format"] == "acc-export/v1" and doc["parts"] == ["snapshot", "events", "history"]
    assert "processes" not in doc["snapshot"]["host"] and "disks" not in doc["snapshot"]
    assert doc["history"]["metrics"][0]["aboveWarnS"] == 10 and "series" not in doc["history"]
    assert "crashWatch" not in doc["events"]


def test_json_full_has_everything():
    doc = ex.to_json(data("full"))
    assert doc["snapshot"]["host"]["processes"][0]["cmdline"] == "ollama.exe serve --token abc"
    assert doc["history"]["series"][0]["avg"] == [70.0, None, 90.0]
    assert len(doc["history"]["ts"]) == 3 and doc["events"]["crashWatch"]["reason"] == "aus"
    json.dumps(doc)                                                 # serializable as it is


# ---- endpoints --------------------------------------------------------------------------------

@pytest.fixture
def client(settings, tmp_path):
    dash = {"node_id": "ai-box-test", "probes": [], "ollama_log_paths": [str(tmp_path / "ollama" / "server*.log")]}
    mods = type(settings.modules).model_validate({"dashboard": dash})
    adapters = settings.adapters.model_copy(update={"fake_scenario": "offload"})
    with TestClient(create_app(settings.model_copy(update={"adapters": adapters, "modules": mods}))) as c:
        yield c


def test_export_default_is_anonymized_short_markdown(client):
    doc = client.get("/api/v1/dashboard/export").json()
    assert doc["format"] == "md" and doc["detail"] == "short" and doc["mediaType"].startswith("text/markdown")
    assert doc["filename"].startswith("acc-export-") and doc["filename"].endswith(".md")
    assert doc["tokens"] == math.ceil(len(doc["content"]) / 4)
    text = doc["content"]
    assert "ai-box-test" not in text and 'node: "<ai-host>"' in text
    assert "anonymized: true" in text and "simulated: [ollama, gpu, host]" in text
    assert "**Kritisch:**" in text and "| split |" in text                    # offload scenario
    assert "## Zustand" in text and "## Ereignisse" in text and "## Verlauf" in text


def test_export_json_full_not_anonymized(client):
    doc = client.get("/api/v1/dashboard/export", params={"format": "json", "detail": "full",
                                                          "anonymize": "false", "range": "6h"}).json()
    body = json.loads(doc["content"])
    assert body["node"] == "ai-box-test" and body["anonymized"] is False
    assert body["history"]["range"] == "6h" and "series" in body["history"]
    assert any(p["cmdline"] for p in body["snapshot"]["host"]["processes"])


def test_export_parts_in_fixed_order_without_duplicates(client):
    doc = client.get("/api/v1/dashboard/export",
                     params=[("parts", "history"), ("parts", "snapshot"), ("parts", "history")]).json()
    assert "parts: [snapshot, history]" in doc["content"] and "## Ereignisse" not in doc["content"]


def test_export_raw_is_plain_text_with_file_name(client):
    r = client.get("/api/v1/dashboard/export/raw", params={"format": "json"})
    assert r.headers["content-type"] == "application/json"
    assert r.headers["content-disposition"].startswith('inline; filename="acc-export-')
    assert r.json()["format"] == "acc-export/v1"
    r = client.get("/api/v1/dashboard/export/raw")
    assert r.headers["content-type"] == "text/markdown; charset=utf-8" and r.text.startswith("---\nformat:")


def test_export_rejects_unknown_values(client):
    assert client.get("/api/v1/dashboard/export", params={"format": "pdf"}).status_code == 422
    assert client.get("/api/v1/dashboard/export", params={"parts": "logs"}).status_code == 422
