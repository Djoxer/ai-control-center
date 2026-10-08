"""CatalogService with a stub Ollama and a real SQLite file: refresh, removal, observations, restart."""
import asyncio
from types import SimpleNamespace

import pytest

from control_center.adapters.gpu import GpuReading, GpuUnavailable
from control_center.adapters.ollama import OllamaModelMissing, OllamaUnavailable, RunningModel
from control_center.core.config import AdaptersConfig, Settings
from control_center.core.context import AppContext
from control_center.core.db import Database
from control_center.core.events import EventBus
from control_center.modules.catalog.service import TOPIC, CatalogService, OllamaDown, UnknownModel
from control_center.modules.catalog.settings import CatalogSettings

from .factories import details, tag


class StubOllama:
    simulated = False

    def __init__(self):
        self.tags_list, self.shows, self.ps, self.down, self.show_calls = [], {}, [], False, []

    def install(self, name, **kw):
        self.tags_list.append(tag(name, digest=kw.pop("digest", None), parent=kw.get("parent")))
        self.shows[name] = details(name, **kw)

    async def tags(self):
        if self.down:
            raise OllamaUnavailable("connection refused")
        return sorted(self.tags_list, key=lambda t: t.name)

    async def show(self, name, timeout=None):
        self.show_calls.append(name)
        if self.down:
            raise OllamaUnavailable("connection refused")
        if name not in self.shows:
            raise OllamaModelMissing(name)
        return self.shows[name]

    async def running(self):
        if self.down:
            raise OllamaUnavailable("connection refused")
        return list(self.ps)

    async def version(self):
        return "0.35.0"


class StubGpu:
    def __init__(self, fail=False):
        self.fail = fail

    def read(self):
        if self.fail:
            raise GpuUnavailable("NVML gone")
        return GpuReading(name="RTX 5070 Ti", driver_version="610.62", util_percent=0, vram_used_mib=500,
                          vram_total_mib=16303, temp_c=35, power_w=20.0, power_limit_w=300.0, fan_percent=0,
                          throttle_reasons=())


def running(name, digest=None, ctx=32768, size=10, vram=10):
    return RunningModel(name=name, digest=digest or f"d-{name}", size=size, size_vram=vram, context_length=ctx,
                        expires_at=None, family="qwen2", parameter_size="14.8B", quantization="Q4_K_M")


class Harness:
    def __init__(self, tmp_path, ai_host="127.0.0.1", ollama_mode="http", gpu_mode="nvml", **cfg):
        self.settings = Settings(data_dir=tmp_path / "data",
                                 adapters=AdaptersConfig(ai_host=ai_host, ollama=ollama_mode, gpu=gpu_mode))
        self.cfg = CatalogSettings(**cfg)
        self.ollama = StubOllama()
        self.gpu = StubGpu()
        self.published = []

    async def service(self, read_log=False) -> CatalogService:
        ctx = AppContext(settings=self.settings, db=Database(self.settings.db_path), events=EventBus())
        ctx.adapters = SimpleNamespace(ollama=self.ollama, gpu=self.gpu, simulated=[], cfg=self.settings.adapters)
        await ctx.db.open()
        ctx.events.publish = lambda topic, data: self.published.append((topic, data))
        ctx.events._subscribers.add(asyncio.Queue())             # someone listens -> overview is built and sent
        svc = CatalogService(ctx, self.cfg, read_log=read_log)
        await svc.repo.create()
        svc.rows = {r.record.name: r for r in await svc.repo.load_models()}
        svc.hardware = await svc._read_gpu()
        return svc


def run(coro):
    return asyncio.run(coro)


def test_refresh_collects_and_shows_only_what_changed(tmp_path):
    h = Harness(tmp_path)
    h.ollama.install("base:9b", weights="w")
    h.ollama.install("ctx64k:latest", parent="base:9b", weights="w", num_ctx=65536)

    async def go():
        svc = await h.service()
        await svc.refresh()
        assert sorted(h.ollama.show_calls) == ["base:9b", "ctx64k:latest"]
        await svc.refresh()                                        # nothing changed: no /api/show at all
        assert len(h.ollama.show_calls) == 2
        await svc.refresh("base:9b", force=True)                   # one model on request
        assert h.ollama.show_calls[-1] == "base:9b" and len(h.ollama.show_calls) == 3
        await svc.refresh(force=True)                              # all on request
        assert len(h.ollama.show_calls) == 5
        h.ollama.tags_list[0] = tag("base:9b", digest="new")       # re-pulled -> new digest -> shown again
        await svc.refresh()
        assert h.ollama.show_calls[-1] == "base:9b"
        ov = await svc.overview()
        assert [g.members for g in ov.groups] == [["base:9b", "ctx64k:latest"]]
        assert ov.ollama.online and ov.ollama.version == "0.35.0" and ov.refreshed_at is not None
        assert ov.models[1].changes == ["num_ctx 65536"] and ov.models[1].parent.via == "declared"
        with pytest.raises(UnknownModel):
            await svc.refresh("nope:1", force=True)

    run(go())


def test_ollama_down_keeps_the_last_inventory(tmp_path):
    h = Harness(tmp_path)
    h.ollama.install("base:9b")

    async def go():
        svc = await h.service()
        await svc.refresh()
        h.ollama.down = True
        with pytest.raises(OllamaDown, match="connection refused"):
            await svc.refresh()
        ov = await svc.overview()
        assert not ov.ollama.online and "connection refused" in ov.ollama.error
        assert [m.name for m in ov.models] == ["base:9b"]           # still there
        # a restart while Ollama is down shows the stored inventory
        svc2 = await h.service()
        assert [m.name for m in (await svc2.overview()).models] == ["base:9b"]

    run(go())


def test_show_failure_keeps_tags_data_and_retries(tmp_path):
    h = Harness(tmp_path)
    h.ollama.install("x:1")
    del h.ollama.shows["x:1"]                                       # deleted between tags and show

    async def go():
        svc = await h.service()
        await svc.refresh()
        m = (await svc.overview()).models[0]
        assert "kennt das Modell nicht" in m.show_error and m.size_bytes == 1000
        h.ollama.shows["x:1"] = details("x:1")
        await svc.refresh()                                         # retried without being asked
        assert (await svc.overview()).models[0].show_error is None

    run(go())


def test_removed_models_stay_listed_and_come_back(tmp_path):
    h = Harness(tmp_path)
    h.ollama.install("a:1")
    h.ollama.install("b:1")

    async def go():
        svc = await h.service()
        await svc.refresh()
        first = (await svc.overview()).models[0].first_seen
        h.ollama.tags_list = [t for t in h.ollama.tags_list if t.name != "b:1"]
        await svc.refresh()
        ov = await svc.overview()
        assert [m.name for m in ov.models] == ["a:1"] and [r.name for r in ov.removed] == ["b:1"]
        svc2 = await h.service()                                    # survives a restart
        assert [r.name for r in (await svc2.overview()).removed] == ["b:1"]
        h.ollama.install("b:1")
        await svc2.refresh()
        ov = await svc2.overview()
        assert sorted(m.name for m in ov.models) == ["a:1", "b:1"] and ov.removed == []
        assert ov.models[0].first_seen == first                    # first_seen survives updates

    run(go())


def test_observations_count_loads_not_ticks(tmp_path):
    h = Harness(tmp_path)
    h.ollama.install("coder:14b", num_ctx=32768)

    async def go():
        svc = await h.service()
        await svc.refresh()
        h.ollama.ps = [running("coder:14b", size=0, vram=0)]       # still loading: not recorded
        await svc.observe()
        assert (await svc.overview()).models[0].observations == []
        h.ollama.ps = [running("coder:14b", size=100, vram=84)]
        for _ in range(3):
            await svc.observe()
        m = (await svc.overview()).models[0]
        assert m.loaded and len(m.observations) == 1
        o = m.observations[0]
        assert (o.loads, o.placement, o.gpu_ratio, o.num_ctx, o.hardware) == (1, "split", 0.84, 32768, "RTX 5070 Ti · 16303 MiB")
        assert o.current and m.verdict.state == "split" and m.verdict.basis == "measured"
        h.ollama.ps = []                                            # unloaded ...
        await svc.observe()
        assert not (await svc.overview()).models[0].loaded
        h.ollama.ps = [running("coder:14b", size=100, vram=100)]   # ... and loaded again, now fully on the GPU
        await svc.observe()
        o = (await svc.overview()).models[0].observations[0]
        assert (o.loads, o.placement) == (2, "gpu")
        h.ollama.ps = [running("coder:14b", ctx=8192)]              # a client asked for another context
        await svc.observe()
        obs = (await svc.overview()).models[0].observations
        assert sorted(o.num_ctx for o in obs) == [8192, 32768]
        assert [o.current for o in obs if o.num_ctx == 8192] == [False]   # the effective context is 32768

    run(go())


def test_a_model_loaded_before_a_restart_is_not_counted_twice(tmp_path):
    h = Harness(tmp_path)
    h.ollama.install("coder:14b", num_ctx=32768)
    h.ollama.ps = [running("coder:14b")]

    async def go():
        svc = await h.service()
        await svc.refresh()
        await svc.observe()
        assert (await svc.overview()).models[0].observations[0].loads == 1
        svc2 = await h.service()                                    # control center restarted, model still loaded
        await svc2.refresh()
        await svc2.observe()
        assert (await svc2.overview()).models[0].observations[0].loads == 1
        h.ollama.ps = []
        await svc2.observe()
        h.ollama.ps = [running("coder:14b")]                       # a real reload counts
        await svc2.observe()
        assert (await svc2.overview()).models[0].observations[0].loads == 2

    run(go())


def test_publishes_full_overview_on_change_only(tmp_path):
    h = Harness(tmp_path)
    h.ollama.install("a:1")

    async def go():
        svc = await h.service()
        await svc.refresh()
        assert len(h.published) == 1 and h.published[0][0] == TOPIC
        assert h.published[0][1]["revision"] == 1 and h.published[0][1]["models"][0]["name"] == "a:1"
        await svc.refresh()                                          # nothing new
        await svc.observe()                                          # nothing loaded, nothing changed
        assert len(h.published) == 1
        h.ollama.ps = [running("a:1")]
        await svc.observe()
        assert len(h.published) == 2 and h.published[1][1]["models"][0]["loaded"]

    run(go())


def test_remote_ollama_has_no_gpu_and_no_log(tmp_path):
    h = Harness(tmp_path, ai_host="192.0.2.20")

    async def go():
        svc = await h.service(read_log=None)
        assert svc.read_log is False and svc.gpu_visible is False
        await svc._read_environment()
        ov = await svc.overview()
        assert ov.hardware.key is None and "192.0.2.20" in ov.hardware.note
        assert ov.server.source == "unknown" and "192.0.2.20" in ov.server.note

    run(go())


def test_second_pc_with_a_fake_gpu_uses_the_scenario_and_says_so(tmp_path):
    h = Harness(tmp_path, ai_host="192.0.2.20", gpu_mode="fake")

    async def go():
        svc = await h.service(read_log=None)
        assert svc.read_log is False and svc.gpu_visible is True
        hw = (await svc.overview()).hardware
        assert hw.vram_total_bytes == 16303 * 1024 * 1024 and "simuliert" in hw.note and "normal" in hw.note

    run(go())


def test_server_defaults_from_log_and_config(tmp_path):
    log = tmp_path / "server.log"
    log.write_text('msg="server config" env="map[OLLAMA_CONTEXT_LENGTH:65536 OLLAMA_KV_CACHE_TYPE:q8_0 '
                   'OLLAMA_FLASH_ATTENTION:true OLLAMA_NUM_PARALLEL:1 OLLAMA_MAX_LOADED_MODELS:1]"\n', encoding="utf-8")
    h = Harness(tmp_path, ollama_log_paths=[str(tmp_path / "server*.log")])
    h.ollama.install("coder:14b")

    async def go():
        svc = await h.service(read_log=True)
        await svc.refresh()
        ov = await svc.overview()
        assert (ov.server.source, ov.server.log_file, ov.server.context_length, ov.server.max_loaded_models) == (
            "log", "server.log", 65536, 1)
        m = ov.models[0]
        assert (m.context.effective, m.context.source, m.context.clamped) == (32768, "server", True)
        assert m.estimate.kv_type == "q8_0"
        h.cfg = h.cfg.model_copy(update={"server_context_length": 8192, "flash_attention": False})
        svc.cfg = h.cfg
        ov = await svc.overview()
        assert ov.server.source == "mixed" and ov.server.overridden == ["server_context_length", "flash_attention"]
        m = ov.models[0]
        assert m.context.effective == 8192 and m.estimate.kv_type == "f16"   # q8_0 needs flash attention

    run(go())


@pytest.mark.parametrize("gpu, note", [(StubGpu(fail=True), "NVML gone"), (None, 'gpu = "none"')])
def test_gpu_problems_are_a_note(tmp_path, gpu, note):
    h = Harness(tmp_path)
    h.gpu = gpu

    async def go():
        svc = await h.service()
        assert svc.hardware.key is None and note in svc.hardware.note
        ov = await svc.overview()
        assert ov.hardware.vram_total_bytes is None

    run(go())


def test_local_http_reads_the_log_fake_does_not(tmp_path):
    async def go():
        assert (await Harness(tmp_path).service(read_log=None)).read_log is True
        fake = await Harness(tmp_path, ollama_mode="fake").service(read_log=None)
        assert fake.read_log is False and fake.gpu_visible is True       # a scenario brings its own GPU

    run(go())


def test_loop_survives_ollama_down(tmp_path):
    h = Harness(tmp_path, observe_interval_s=2, refresh_interval_s=10)
    h.ollama.down = True

    async def go():
        svc = await h.service()
        svc._task = asyncio.create_task(svc._run())
        await asyncio.sleep(0.05)
        assert not svc._task.done()                                 # OllamaDown did not end the loop
        await svc.shut_down()
        assert svc._task is None

    run(go())
