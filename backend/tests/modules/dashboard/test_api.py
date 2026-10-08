"""Dashboard end to end: real module package, fake adapters, HTTP + SSE + capture round trip."""
import asyncio
import json
import time
from datetime import datetime

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from control_center import capture_samples
from control_center.adapters.common import SAMPLES_DIR
from control_center.adapters.gpu import FakeGpu, GpuUnavailable
from control_center.adapters.host import FakeHost
from control_center.adapters.ollama import FakeOllama
from control_center.core.context import AppContext
from control_center.core.db import Database
from control_center.core.events import EventBus
from control_center.main import create_app
from control_center.modules.dashboard.service import TOPIC, DashboardService
from control_center.modules.dashboard.settings import DashboardSettings


@pytest.fixture
def client_for(settings, tmp_path):
    def make(scenario="normal", **dashboard) -> TestClient:
        adapters = settings.adapters.model_copy(update={"fake_scenario": scenario})
        # own log folder: on the AI box the real %LOCALAPPDATA%/Ollama/server.log would otherwise be found
        dash = {"probes": [], "ollama_log_paths": [str(tmp_path / "ollama" / "server*.log")], **dashboard}
        mods = type(settings.modules).model_validate({"dashboard": dash})
        return TestClient(create_app(settings.model_copy(update={"adapters": adapters, "modules": mods})))
    return make


def test_snapshot_normal(client_for):
    with client_for() as c:
        states = {m["key"]: m["state"] for m in c.get("/api/v1/meta/modules").json()}
        assert states["dashboard"] == "running"
        s = c.get("/api/v1/dashboard/snapshot").json()
    assert s["simulated"] == ["ollama", "gpu", "host"]
    assert s["ollamaOnline"] is True and s["ollamaVersion"] == "0.12.6"
    assert [m["placement"] for m in s["models"]] == ["gpu", "gpu"]
    assert s["gpu"]["vramTotalMib"] == 16303 and s["disks"][0]["mount"] == "C:\\"
    assert s["services"][0]["key"] == "ollama"
    assert s["warnings"] == [] and s["errors"] == []


def test_snapshot_offload(client_for):
    with client_for("offload") as c:
        s = c.get("/api/v1/dashboard/snapshot").json()
    assert s["models"][0]["placement"] == "split" and s["models"][0]["gpuRatio"] == pytest.approx(0.8438, abs=1e-4)
    assert [w["code"] for w in s["warnings"]] == ["model_split", "vram_low"]
    assert s["warnings"][0]["level"] == "critical"


def test_snapshot_ollama_down(client_for):
    with client_for("ollama-down") as c:
        s = c.get("/api/v1/dashboard/snapshot").json()
    assert s["ollamaOnline"] is False and s["models"] == []
    assert s["warnings"][0]["code"] == "ollama_offline"
    assert [e["source"] for e in s["errors"]] == ["ollama"]


def test_invalid_config_fails_only_the_dashboard(client_for):
    with client_for(interval_fast_s=30, interval_medium_s=10) as c:
        mods = {m["key"]: m for m in c.get("/api/v1/health").json()["modules"]}
        assert mods["dashboard"]["state"] == "failed" and "fast <= medium" in mods["dashboard"]["error"]
        assert mods["logs"]["state"] == "running"
        assert c.get("/api/v1/dashboard/snapshot").status_code == 503


def test_contract(client_for):
    with client_for() as c:
        spec = c.get("/openapi.json").json()
    ids = {op["operationId"] for path in spec["paths"].values() for op in path.values()}
    assert "dashboard_snapshot" in ids
    placement = spec["components"]["schemas"]["LoadedModel"]["properties"]["placement"]
    assert set(placement["enum"]) == {"gpu", "split", "cpu", "unknown"}      # TS union, not string


def _service(settings, **cfg) -> tuple[DashboardService, EventBus]:
    bus = EventBus()
    ctx = AppContext(settings=settings, db=Database(settings.db_path), events=bus)
    # never the machine's real Ollama log (tests also run on the AI box)
    no_log = [str(settings.data_dir / "no-ollama-log" / "server*.log")]
    return DashboardService(ctx, DashboardSettings(**{"probes": [], "ollama_log_paths": no_log, **cfg})), bus


def test_sse_carries_the_full_snapshot(settings):
    async def go():
        svc, bus = _service(settings)
        await svc.ctx.adapters.open()
        try:
            with bus.subscription() as q:
                svc.start()
                ev = await asyncio.wait_for(q.get(), 3)
                await svc.stop()
        finally:
            await svc.ctx.adapters.close()
        return ev, svc.latest
    ev, latest = asyncio.run(go())
    assert ev.topic == TOPIC == "dashboard.snapshot"
    assert ev.data == latest.model_dump(mode="json", by_alias=True)
    json.dumps(ev.data)                                                       # must be JSON-serialisable


def test_loop_survives_a_failing_tick(settings):
    async def go():
        svc, _bus = _service(settings, interval_fast_s=0.5, interval_medium_s=1, interval_slow_s=5)
        await svc.ctx.adapters.open()
        original, calls = svc.sampler.sample, []

        async def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("first tick explodes")
            return await original()
        svc.sampler.sample = flaky
        try:
            svc.start()
            snap = await svc.snapshot(wait_s=3)
            await svc.stop()
        finally:
            await svc.ctx.adapters.close()
        return snap, len(calls)
    snap, n = asyncio.run(go())
    assert snap is not None and n >= 2


@respx.mock
def test_capture_round_trip(settings, tmp_path, monkeypatch):
    """What capture_samples writes must be readable by the fake adapters."""
    respx.get("http://capture-box:11434/api/ps").respond(
        json=json.loads((SAMPLES_DIR / "offload" / "ollama-ps.json").read_text(encoding="utf-8")))
    respx.get("http://capture-box:11434/api/version").respond(json={"version": "9.9.9"})
    respx.get("http://capture-box:11434/api/tags").respond(
        json=json.loads((SAMPLES_DIR / "offload" / "ollama-tags.json").read_text(encoding="utf-8")))
    shows = json.loads((SAMPLES_DIR / "offload" / "ollama-show.json").read_text(encoding="utf-8"))
    respx.post("http://capture-box:11434/api/show").mock(
        side_effect=lambda request: httpx.Response(200, json=shows[json.loads(request.content)["model"]]))
    sample_gpu = FakeGpu(SAMPLES_DIR / "offload")

    class NoGpu:                                     # deterministic on machines with and without NVIDIA
        def __init__(self, _index):
            pass

        def read(self):
            raise GpuUnavailable("NVML: test")

        def close(self):
            pass
    monkeypatch.setattr(capture_samples, "NvmlGpu", NoGpu)

    target = tmp_path / "scenario"
    target.mkdir()
    (target / "gpu.json").write_text("stale")       # must not survive a capture without GPU
    s = settings.model_copy(update={
        "adapters": settings.adapters.model_copy(update={"ai_host": "capture-box"}),
        "modules": type(settings.modules).model_validate({"dashboard": {"disk_paths": [str(tmp_path)]}}),
    })
    written, problems = asyncio.run(capture_samples.capture(target, s, cpu_window_s=0.05))

    assert set(written) == {"ollama-ps.json", "ollama-version.json", "ollama-tags.json", "ollama-show.json",
                            "host.json", "disks.json", "meta.json"}
    assert problems == ["gpu.json: GpuUnavailable: NVML: test"] and not (target / "gpu.json").exists()
    assert asyncio.run(FakeOllama(target).version()) == "9.9.9"
    assert asyncio.run(FakeOllama(target).running())[0].name == "qwen2.5-coder:14b-instruct-q4_K_M"
    installed = asyncio.run(FakeOllama(target).tags())
    assert len(installed) == 8 and all(asyncio.run(FakeOllama(target).show(m.name)).name == m.name for m in installed)
    host = FakeHost(target)
    assert host.read([], "short").cpu_count >= 1 and host.disks([])[0].total_bytes > 0
    assert sample_gpu.read().vram_total_mib == 16303


# ---- history + events (step 4c) ----------------------------------------------------------------

def test_history_endpoint(client_for):
    with client_for() as c:
        c.get("/api/v1/dashboard/snapshot")                         # waits for the first tick
        for _ in range(50):                                         # its row is written right after the snapshot
            h = c.get("/api/v1/dashboard/history", params={"range": "1h"}).json()
            if h["ts"]:
                break
            time.sleep(0.05)
        assert c.get("/api/v1/dashboard/history", params={"range": "2h"}).status_code == 422
        only = c.get("/api/v1/dashboard/history", params=[("range", "7d"), ("metrics", "temp_c")]).json()
    assert h["resolution"] == "raw" and h["stepS"] == 10
    since, until = datetime.fromisoformat(h["since"]), datetime.fromisoformat(h["until"])
    assert (until - since).total_seconds() == pytest.approx(3600)
    assert len(h["ts"]) >= 1                                     # first tick is stored right away
    series = {s["metric"]: s for s in h["series"]}
    assert set(series) == {"gpu_util", "vram_used_mib", "temp_c", "power_w", "cpu_percent", "ram_percent"}
    assert series["gpu_util"]["scaleMax"] == 100
    assert series["vram_used_mib"]["scaleMax"] == 16303 and series["vram_used_mib"]["warn"] == 16303 - 1500
    assert series["temp_c"]["warn"] == 83 and len(series["temp_c"]["avg"]) == len(h["ts"])
    assert only["resolution"] == "hour" and [s["metric"] for s in only["series"]] == ["temp_c"]


def test_events_endpoint_reports_crash_watch(client_for, tmp_path):
    with client_for() as c:                                     # no log file yet
        page = c.get("/api/v1/dashboard/events").json()
    assert page["events"] == [] and page["nextBefore"] is None
    assert page["crashWatch"] == {"active": False, "file": None, "reason": "keine Ollama-Logdatei gefunden"}
    (tmp_path / "ollama").mkdir()
    (tmp_path / "ollama" / "server.log").write_text("time=x level=INFO msg=started\n", encoding="utf-8")
    with client_for() as c:                                     # log present: watching it
        page = c.get("/api/v1/dashboard/events").json()
    assert page["crashWatch"] == {"active": True, "file": "server.log", "reason": None}


def _open_service(settings, **cfg):
    svc, bus = _service(settings, **cfg)

    async def open_all():
        await svc.ctx.db.open()
        await svc.ctx.adapters.open()
        await svc.init_storage()

    async def close_all():
        await svc.ctx.adapters.close()
        await svc.ctx.db.close()
    return svc, bus, open_all, close_all


def test_events_flow_into_database_and_stream(settings):
    async def go():
        svc, bus, open_all, close_all = _open_service(settings)
        await open_all()
        try:
            with bus.subscription() as q:
                await svc.tick()                                       # baseline: normal scenario
                svc.ctx.adapters.ollama.folder = SAMPLES_DIR / "offload"
                await svc.tick()
                live = []
                while not q.empty():
                    live.append(q.get_nowait())
            page = await svc.events(10, None)
            history = await svc.history("1h", [])
        finally:
            await close_all()
        return live, page, history
    live, page, history = asyncio.run(go())
    kinds = [ev.data["kind"] for ev in live if ev.topic == "dashboard.event"]
    assert kinds == ["model_loaded", "offload_started", "model_unloaded"]   # nomic leaves silently (quiet)
    assert [e.kind for e in page.events] == list(reversed(kinds))           # stored, newest first
    assert all(e.id is not None for e in page.events)
    assert len(history.ts) == 1                                             # tick 1 stored, tick 2 not (every 5th)


def test_database_failure_is_visible_not_fatal(settings):
    async def go():
        svc, _bus, open_all, close_all = _open_service(settings)
        await open_all()
        try:
            await svc.ctx.db.close()                                   # simulate a broken database
            await svc.tick()
            return await svc.tick()
        finally:
            await close_all()
    snap = asyncio.run(go())
    assert "history" in [e.source for e in snap.errors]
    assert snap.models                                                  # live data unaffected
