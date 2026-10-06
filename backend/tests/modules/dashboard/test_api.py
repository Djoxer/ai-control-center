"""Dashboard end to end: real module package, fake adapters, HTTP + SSE + capture round trip."""
import asyncio
import json

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
def client_for(settings):
    def make(scenario="normal", **dashboard) -> TestClient:
        adapters = settings.adapters.model_copy(update={"fake_scenario": scenario})
        mods = type(settings.modules).model_validate({"dashboard": {"probes": [], **dashboard}})
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
    return DashboardService(ctx, DashboardSettings(probes=[], **cfg)), bus


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

    assert set(written) == {"ollama-ps.json", "ollama-version.json", "host.json", "disks.json", "meta.json"}
    assert problems == ["gpu.json: GpuUnavailable: NVML: test"] and not (target / "gpu.json").exists()
    assert asyncio.run(FakeOllama(target).version()) == "9.9.9"
    assert asyncio.run(FakeOllama(target).running())[0].name == "qwen2.5-coder:14b-instruct-q4_K_M"
    host = FakeHost(target)
    assert host.read([], "short").cpu_count >= 1 and host.disks([])[0].total_bytes > 0
    assert sample_gpu.read().vram_total_mib == 16303
