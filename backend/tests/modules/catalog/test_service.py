"""CatalogService with a stub Ollama and a real SQLite file: refresh, removal, observations, restart,
budget of the card, preflight and test runs."""
import asyncio
from types import SimpleNamespace

import pytest

from control_center.adapters.gpu import GpuReading, GpuUnavailable
from control_center.adapters.library import FakeLibrary
from control_center.adapters.ollama import (
    ChatResult, GenerateResult, OllamaModelMissing, OllamaRequestFailed, OllamaUnavailable, RunningModel, ToolCall,
)
from control_center.core.config import AdaptersConfig, Settings
from control_center.core.context import AppContext
from control_center.core.db import Database
from control_center.core.events import EventBus
from control_center.modules.catalog import bench
from control_center.modules.catalog.toolcheck import CASES
from control_center.modules.catalog.service import (
    BENCH_TOPIC, TOPIC, Busy, CatalogService, NeedsConfirm, OllamaDown, Refused, UnknownModel,
)
from control_center.modules.catalog.settings import CatalogSettings

from .factories import details, tag


def _answer(*calls, content="", done="stop", thinking=0):
    return ChatResult(content=content, thinking_chars=thinking, tool_calls=tuple(calls), total_s=1.5,
                      eval_tokens=30, eval_s=0.5, done_reason=done)


GOOD = {"read": _answer(ToolCall("read_file", {"path": "./src/app/app.config.ts"})),
        "choose": _answer(ToolCall("run_command", {"command": "npm test"})),
        "types": _answer(ToolCall("search", {"pattern": "TODO", "path": "src/", "max_results": 5}))}


class StubOllama:
    simulated = False

    def __init__(self):
        self.tags_list, self.shows, self.ps, self.down, self.show_calls = [], {}, [], False, []
        self.sizes: dict[str, tuple[int, int]] = {}             # name -> (size, size_vram) once loaded
        self.generated, self.unloaded, self.fail_generate = [], [], None
        self.chats: list[tuple[str, dict]] = []
        # request text -> answer; default: every case answered correctly with a structured call
        self.answers: dict[str, ChatResult] = {c.request: GOOD[c.key] for c in CASES}
        self.chat_error: str | None = None

    def install(self, name, **kw):
        size = kw.pop("size", 8 * GIB)                          # weights file; the factories' qwen2 shape for KV
        self.tags_list.append(tag(name, digest=kw.pop("digest", None), parent=kw.get("parent"), size=size))
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

    async def generate(self, name, prompt, options, keep_alive, timeout):
        self.generated.append((name, options))
        if self.fail_generate:
            raise OllamaRequestFailed(self.fail_generate)
        if name not in self.shows:
            raise OllamaModelMissing(name)
        size, vram = self.sizes.get(name, (8 * GIB, 8 * GIB))
        t = next(t for t in self.tags_list if t.name == name)
        self.ps = [running(name, digest=t.digest, ctx=options["num_ctx"], size=size, vram=vram)]
        return GenerateResult(total_s=9.0, load_s=4.5, prompt_tokens=60, prompt_s=0.1, eval_tokens=128,
                              eval_s=2.0, done_reason="length")

    async def chat(self, name, messages, tools, options, keep_alive, timeout):
        self.chats.append((name, options))
        if self.chat_error:
            raise OllamaRequestFailed(self.chat_error)
        return self.answers[messages[-1]["content"]]

    async def unload(self, name, timeout=30):
        self.unloaded.append(name)
        self.ps = [m for m in self.ps if m.name != name]


MIB = 1024 ** 2
GIB = 1024 ** 3


class StubGpu:
    """The card as NVML sees it: other programs (idle_mib) + what Ollama loaded + 300 MiB per runner.
    `extra` (MiB) is added to the next readings one by one: a runner that frees its memory late, or a
    model Ollama is loading before /api/ps lists it."""

    def __init__(self, fail=False, ollama=None, idle_mib=500, runner_mib=300):
        self.fail, self.ollama, self.idle_mib, self.runner_mib, self.extra = fail, ollama, idle_mib, runner_mib, []

    def read(self):
        if self.fail:
            raise GpuUnavailable("NVML gone")
        loaded = sum(m.size_vram for m in self.ollama.ps) if self.ollama else 0
        used = self.idle_mib + loaded // MIB + (self.runner_mib if loaded else 0) + (self.extra.pop(0) if self.extra else 0)
        return GpuReading(name="RTX 5070 Ti", driver_version="610.62", util_percent=0, vram_used_mib=used,
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
        self.gpu = StubGpu(ollama=self.ollama)
        self.library = FakeLibrary()
        self.published = []
        self.services: dict = {}

    async def service(self, read_log=False) -> CatalogService:
        ctx = AppContext(settings=self.settings, db=Database(self.settings.db_path), events=EventBus())
        ctx.adapters = SimpleNamespace(ollama=self.ollama, gpu=self.gpu, library=self.library, simulated=[],
                                       cfg=self.settings.adapters)
        ctx.services = self.services
        await ctx.db.open()
        OPEN_DBS.append(ctx.db)
        ctx.events.publish = lambda topic, data: self.published.append((topic, data))
        ctx.events._subscribers.add(asyncio.Queue())             # someone listens -> overview is built and sent
        svc = CatalogService(ctx, self.cfg, read_log=read_log)
        await svc.repo.create()
        svc.rows = {r.record.name: r for r in await svc.repo.load_models()}
        await svc._load_state()
        svc.hardware = await svc._read_gpu()
        return svc


OPEN_DBS: list[Database] = []


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(bench, "POLL_S", 0)                     # settling the card: no real sleeps


def run(coro):
    """asyncio.run + close every database the test opened: a live aiosqlite thread outlasting its loop
    would raise in the background (PytestUnhandledThreadExceptionWarning)."""
    async def main():
        try:
            return await coro
        finally:
            while OPEN_DBS:
                await OPEN_DBS.pop().close()
    return asyncio.run(main())


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
        assert "kennt das Modell nicht" in m.show_error and m.size_bytes == 8 * GIB
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
        await svc.observe()                                          # empty card: first reading, not yet trusted
        assert len(h.published) == 1
        await svc.observe()                                          # the same again: other programs measured
        assert len(h.published) == 2
        await svc.refresh()                                          # nothing new
        await svc.observe()                                          # still empty, same value: nothing to say
        assert len(h.published) == 2
        h.ollama.ps = [running("a:1")]
        await svc.observe()
        assert len(h.published) == 3 and h.published[2][1]["models"][0]["loaded"]

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


# ---- budget of the card -------------------------------------------------------------------------------

def test_an_empty_ollama_measures_the_other_programs(tmp_path):
    h = Harness(tmp_path)
    h.gpu.idle_mib = 1424

    async def go():
        svc = await h.service()
        b = (await svc.overview()).budget
        assert (b.other_source, b.other_bytes) == ("assumed", round(1.2 * GIB))       # nothing measured yet
        await svc.observe()                                                          # /api/ps empty, once
        assert svc.budget().other_source == "assumed"
        await svc.observe()                                                          # twice the same: counts
        b = (await svc.overview()).budget
        assert (b.other_source, b.other_bytes) == ("measured", 1424 * MIB) and b.other_measured_at is not None
        assert b.available_bytes == 16303 * MIB - 1424 * MIB - round(0.45 * GIB)
        h.ollama.ps = [running("x:1", size=GIB, vram=GIB)]
        h.gpu.idle_mib = 9000                                    # busy card while a model runs: not "other"
        await svc.observe()
        assert (await svc.overview()).budget.other_bytes == 1424 * MIB
        svc2 = await h.service()                                 # survives a restart
        assert svc2.budget().other_bytes == 1424 * MIB and svc2.budget().other_source == "measured"

    run(go())


def test_a_model_that_is_still_loading_is_not_other_programs(tmp_path):
    """VRAM is allocated before /api/ps lists the model: a rising card must not become "other programs"."""
    h = Harness(tmp_path)
    h.gpu.idle_mib = 1424

    async def go():
        svc = await h.service()
        await svc.observe()
        await svc.observe()
        assert svc.budget().other_bytes == 1424 * MIB
        h.gpu.extra = [3000, 9000]                               # loading: 4.4 GiB, then 10.2 GiB, ps still empty
        await svc.observe()
        await svc.observe()
        assert svc.budget().other_bytes == 1424 * MIB
        h.ollama.ps = [running("x:1", size=GIB, vram=GIB)]       # now listed: the reading pair starts over
        await svc.observe()
        h.ollama.ps = []
        h.gpu.extra = [700]                                      # one odd reading after the unload ...
        await svc.observe()
        await svc.observe()                                      # ... does not pair with the settled one
        assert svc.budget().other_bytes == 1424 * MIB
        h.gpu.idle_mib = 2000                                    # a browser opened: two equal readings count
        await svc.observe()
        await svc.observe()
        assert svc.budget().other_bytes == 2000 * MIB

    run(go())


def test_calibration_reaches_relatives_with_the_same_weights(tmp_path):
    h = Harness(tmp_path)
    h.ollama.install("base:9b", weights="w", num_ctx=32768)
    h.ollama.install("small:latest", parent="base:9b", weights="w", num_ctx=8192)

    async def go():
        svc = await h.service()
        await svc.refresh()
        before = {m.name: m for m in (await svc.overview()).models}
        assert before["small:latest"].estimate.calibrated is None
        h.ollama.ps = [running("base:9b", digest="d-base:9b", ctx=32768, size=5 * GIB, vram=5 * GIB)]
        await svc.observe()
        after = {m.name: m for m in (await svc.overview()).models}
        base, small = after["base:9b"], after["small:latest"]
        assert base.verdict.basis == "measured" and base.verdict.need_bytes == 5 * GIB
        assert small.verdict.basis == "estimated" and "1 Messung" in small.estimate.calibrated
        kv = small.estimate.kv_bytes
        kv_32k = 48 * 8 * 256 * 2 * 32768                       # factories: qwen2 shape, stub runs f16
        assert small.estimate.need_bytes == 5 * GIB - kv_32k + kv

    run(go())


# ---- preflight and test runs ----------------------------------------------------------------------------

async def _ready(h, **models):
    svc = await h.service()
    await svc.refresh()
    await svc.observe()                                         # empty card, twice: other programs measured
    await svc.observe()
    return svc


def test_preflight_says_what_a_context_costs(tmp_path):
    h = Harness(tmp_path)
    h.gpu.idle_mib = 1424
    h.ollama.install("coder:14b", weights="w")                 # 8 GiB + 48 layers x 8 KV heads, f16: 192 KiB/token
    h.ollama.install("embed:1", caps=("embedding",))

    async def go():
        svc = await _ready(h)
        h.ollama.ps = [running("chat:1", size=GIB, vram=GIB)]
        await svc.observe()
        ok = await svc.preflight("coder:14b", 4096)
        assert ok.allowed and not ok.needs_confirm and ok.verdict.state == "fits"
        assert ok.context.source == "request" and ok.will_unload == ["chat:1"]
        assert ok.suggestions[0] == 2048 and max(ok.suggestions) == 32768      # trained context of the model
        split = await svc.preflight("coder:14b", 32768)                         # 8 + 6 + 0.4 GiB > 14.08
        assert not split.allowed and split.verdict.state == "split" and "kleineren Kontext" in split.reason
        emb = await svc.preflight("embed:1")
        assert not emb.allowed and "Einbettungsmodelle" in emb.reason
        with pytest.raises(UnknownModel):
            await svc.preflight("nope:1")

    run(go())


def test_test_run_measures_and_cleans_up(tmp_path):
    h = Harness(tmp_path)
    h.gpu.idle_mib = 1424
    h.ollama.install("coder:14b", weights="w", num_ctx=8192)
    h.ollama.sizes["coder:14b"] = (9 * GIB, 9 * GIB)

    async def go():
        svc = await _ready(h)
        save, slot_free = svc.repo.save_bench, []

        async def watched_save(*a):                     # "done" is visible as soon as it is stored ...
            slot_free.append(svc.bench is None)         # ... so the next run must be startable by then
            await save(*a)
        svc.repo.save_bench = watched_save
        h.ollama.ps = [running("chat:1", size=GIB, vram=GIB)]
        st = await svc.start_bench("coder:14b", None, confirm=False)
        assert st.state == "queued" and st.num_ctx == 8192
        await svc.bench.task
        done = svc.bench_list("coder:14b")[0]
        assert done.state == "done" and done.unloaded == ["chat:1"] and svc.bench is None and slot_free == [True]
        r = done.result
        assert (r.requested_ctx, r.actual_ctx, r.placement) == (8192, 8192, "gpu")
        assert (r.load_s, r.eval_tps, r.prompt_tps, r.size_bytes) == (4.5, 64.0, 600.0, 9 * GIB)
        assert r.gpu_before_bytes == 1424 * MIB and r.runner_overhead_bytes == 300 * MIB
        assert h.ollama.unloaded == ["chat:1", "coder:14b"] and h.ollama.ps == []
        assert h.ollama.generated[0][1] == {"num_ctx": 8192, "num_predict": 128, "temperature": 0, "seed": 42}
        phases = [d["phase"] for t, d in h.published if t == BENCH_TOPIC]
        assert phases == [None, "unload", "baseline", "load", "measure", "tools", "cleanup", None]
        assert (r.tools.passed, r.tools.total, r.tools.skipped) == (3, 3, None)
        assert {o["num_ctx"] for _, o in h.ollama.chats} == {8192}           # same context: nothing reloads
        m = (await svc.overview()).models[0]
        assert m.verdict.basis == "measured" and m.verdict.need_bytes == 9 * GIB      # the run is a measurement
        assert m.benches[0].id == done.id and m.observations[0].loads == 1
        svc2 = await h.service()                                                     # stored in SQLite
        assert svc2.bench_list("coder:14b")[0].result.eval_tps == 64.0

    run(go())


VISION = ("completion", "vision")


def test_what_the_runner_holds_beyond_ollamas_count_goes_into_the_verdict(tmp_path):
    """qwen3.5 on the AI box, 08.10.: 6.7 GiB laut Ollama, the card held 1.2 GiB more (vision encoder)."""
    h = Harness(tmp_path)
    h.gpu.idle_mib, h.gpu.runner_mib = 1424, 1229
    h.ollama.install("vis:9b", weights="v", num_ctx=8192, caps=VISION)
    h.ollama.install("vis-64k:latest", parent="vis:9b", weights="v", num_ctx=8192, caps=VISION)
    h.ollama.install("text:14b", weights="t", num_ctx=8192)
    h.ollama.sizes["vis:9b"] = (12 * GIB, 12 * GIB)

    async def go():
        svc = await _ready(h)
        await svc.start_bench("vis:9b", None, confirm=False)
        await svc.bench.task
        assert svc.bench_list()[0].result.runner_overhead_bytes == 1229 * MIB
        m = {x.name: x for x in (await svc.overview()).models}
        extra = 1229 * MIB - round(0.45 * GIB)
        vis, relative, text = m["vis:9b"], m["vis-64k:latest"], m["text:14b"]
        assert (vis.verdict.basis, vis.verdict.need_bytes, vis.verdict.extra_bytes) == ("measured", 12 * GIB, extra)
        assert vis.verdict.state == "tight"                      # 12 GiB fit Ollama's 90 %, + 0.75 GiB do not
        assert vis.overhead.measured_bytes == 1229 * MIB and vis.overhead.extra_bytes == extra
        assert relative.verdict.extra_bytes == extra and relative.overhead.model == "vis:9b"   # same weights
        assert not any("Bild-Encoder" in n for n in relative.estimate.notes)                   # measured now
        assert text.overhead is None and text.verdict.extra_bytes == 0
        pre = await svc.preflight("vis-64k:latest")
        assert pre.verdict.extra_bytes == extra and "außerhalb Ollamas Zählung" in pre.verdict.message
        assert pre.needs_confirm                                  # tight because of the extra
        svc.hardware = svc.hardware.model_copy(update={"key": "RTX 4090 · 24564 MiB"})   # another card
        other = {x.name: x for x in (await svc.overview()).models}["vis:9b"]
        assert other.overhead is None and other.verdict.extra_bytes == 0   # measured on the 5070 Ti only

    run(go())


def test_a_vision_model_measured_by_ollama_only_says_what_is_missing(tmp_path):
    h = Harness(tmp_path)
    h.ollama.install("vis:9b", weights="v", num_ctx=8192, caps=VISION)

    async def go():
        svc = await _ready(h)
        notes = (await svc.overview()).models[0].estimate.notes
        assert any(n.startswith("Bild-Encoder: steckt in der Dateigröße") for n in notes)    # formula only
        h.ollama.ps = [running("vis:9b", digest="d-vis:9b", ctx=8192, size=7 * GIB, vram=7 * GIB)]
        await svc.observe()
        m = (await svc.overview()).models[0]
        assert m.verdict.basis == "measured" and m.overhead is None
        assert [n for n in m.estimate.notes if "Bild-Encoder" in n] == [
            "Bild-Encoder: fehlt in dieser Zahl (Ollamas Zählung) – erst ein Testlauf zeigt, wie viel die Karte "
            "zusätzlich belegt"]

    run(go())


def test_test_run_waits_until_the_card_has_settled(tmp_path):
    """The unloaded runner frees its memory a moment after /api/ps forgot it: the baseline waits for that."""
    h = Harness(tmp_path)
    h.gpu.idle_mib = 1424
    h.ollama.install("coder:14b", weights="w", num_ctx=8192)

    async def go():
        svc = await _ready(h)
        h.ollama.ps = [running("chat:1", size=GIB, vram=GIB)]
        h.gpu.extra = [6000, 2000]                              # chat:1 still (partly) in VRAM for two readings
        await svc.start_bench("coder:14b", None, confirm=False)
        await svc.bench.task
        r = svc.bench_list()[0].result
        assert r.gpu_before_bytes == 1424 * MIB and svc.budget().other_bytes == 1424 * MIB

    run(go())


def test_test_run_refusals(tmp_path):
    h = Harness(tmp_path)
    h.gpu.idle_mib = 1424
    h.ollama.install("coder:14b", weights="w")

    async def go():
        svc = await _ready(h)
        with pytest.raises(Refused, match="kleineren Kontext"):
            await svc.start_bench("coder:14b", 32768, confirm=True)       # split: not even with confirm
        tight_ctx = 24576                                                 # 8 + 4.5 + 0.4 GiB -> 92 % of 14.08
        pre = await svc.preflight("coder:14b", tight_ctx)
        assert pre.allowed and pre.needs_confirm, pre.verdict
        with pytest.raises(NeedsConfirm):
            await svc.start_bench("coder:14b", tight_ctx, confirm=False)
        h.services["rag.service"] = SimpleNamespace(current=object())
        with pytest.raises(Busy, match="RAG-Indexierung"):
            await svc.start_bench("coder:14b", 4096, confirm=False)
        h.services.clear()
        h.ollama.fail_generate = "HTTP 500: llama runner process has terminated: exit status 0xc0000409"
        await svc.start_bench("coder:14b", 4096, confirm=False)
        with pytest.raises(Busy, match="schon ein Testlauf"):
            await svc.start_bench("coder:14b", 4096, confirm=False)
        await svc.bench.task
        failed = svc.bench_list()[0]
        assert failed.state == "failed" and "0xc0000409" in failed.error and svc.bench is None

    run(go())


def test_remote_ollama_locks_test_runs_unless_allowed(tmp_path):
    h = Harness(tmp_path, ai_host="192.0.2.20", gpu_mode="fake")
    h.ollama.install("coder:14b", weights="w")

    async def go():
        svc = await _ready(h)
        assert not svc.bench_access().allowed and "allow_remote_tests" in svc.bench_access().reason
        assert not (await svc.preflight("coder:14b", 4096)).allowed
        svc.cfg = svc.cfg.model_copy(update={"allow_remote_tests": True})
        assert (await svc.preflight("coder:14b", 4096)).allowed

    run(go())


def test_observation_pauses_during_a_test_run(tmp_path):
    h = Harness(tmp_path)
    h.ollama.install("coder:14b", weights="w")

    async def go():
        svc = await _ready(h)
        svc.bench = SimpleNamespace(status=None, task=None)       # a run is going on
        h.ollama.ps = [running("coder:14b", digest="d-coder:14b", ctx=4096, size=GIB, vram=GIB)]
        await svc.observe()
        assert await svc.repo.load_observations() == []

    run(go())


def test_shutdown_cancels_a_running_test_and_keeps_the_report(tmp_path):
    h = Harness(tmp_path)
    h.ollama.install("coder:14b", weights="w", num_ctx=8192)
    started = asyncio.Event()

    async def hanging_generate(name, prompt, options, keep_alive, timeout):
        started.set()
        await asyncio.sleep(3600)                               # a big model still loading from disk

    async def go():
        svc = await _ready(h)
        h.ollama.generate = hanging_generate
        await svc.start_bench("coder:14b", None, confirm=False)
        await started.wait()
        await svc.shut_down()                                   # Ctrl+C on the AI box
        assert svc.bench is None
        stored = (await h.service()).bench_list("coder:14b")[0]
        assert stored.state == "cancelled" and "beendet" in stored.error

    run(go())


# ---- tool calls, usage, OpenCode ------------------------------------------------------------------------

def test_tool_check_variants(tmp_path):
    h = Harness(tmp_path)
    h.ollama.install("coder:14b", weights="w", num_ctx=8192)                 # factories: tools capability
    h.ollama.install("plain:7b", weights="p", num_ctx=8192, caps=("completion",))

    async def go():
        svc = await _ready(h)
        await svc.start_bench("plain:7b", None, confirm=False)               # Ollama reports no tools
        await svc.bench.task
        t = svc.bench_list()[0].result.tools
        assert t.skipped.startswith("Ollama meldet") and t.passed == 0 and h.ollama.chats == []
        h.ollama.chat_error = "HTTP 400: registry.ollama.ai/library/coder:14b does not support tools"
        await svc.start_bench("coder:14b", None, confirm=False)              # template refuses: same answer
        await svc.bench.task
        assert "lehnt Tools" in svc.bench_list()[0].result.tools.skipped
        h.ollama.chat_error = "HTTP 500: llama runner process has terminated"
        await svc.start_bench("coder:14b", None, confirm=False)              # a crash costs the cases, not the run
        await svc.bench.task
        run = svc.bench_list()[0]
        assert run.state == "done" and run.result.eval_tps == 64.0
        assert run.result.tools.passed == 0 and all("terminated" in c.detail for c in run.result.tools.cases)
        h.ollama.chat_error = None
        h.ollama.answers[CASES[2].request] = _answer(ToolCall("search", {"pattern": "TODO", "path": "src",
                                                                        "max_results": "5"}))
        await svc.start_bench("coder:14b", None, confirm=False)
        await svc.bench.task
        t = svc.bench_list()[0].result.tools
        assert (t.passed, [c.ok for c in t.cases]) == (2, [True, True, False]) and "Text statt Zahl" in t.cases[2].detail
        svc.cfg = svc.cfg.model_copy(update={"test_tools": False})
        h.ollama.chats.clear()
        await svc.start_bench("coder:14b", None, confirm=False)
        await svc.bench.task
        assert svc.bench_list()[0].result.tools is None and h.ollama.chats == []

    run(go())


def test_opencode_fit_follows_the_newest_tool_check_of_the_same_digest(tmp_path):
    h = Harness(tmp_path, opencode_min_context=8192)
    h.ollama.install("coder:14b", weights="w", num_ctx=8192)
    h.ollama.install("small:latest", parent="coder:14b", weights="w", num_ctx=4096)
    h.ollama.install("embed:1", caps=("embedding",))

    async def go():
        svc = await _ready(h)
        m = {x.name: x for x in (await svc.overview()).models}
        assert m["coder:14b"].opencode.state == "unknown" and m["embed:1"].opencode is None
        assert '"context": 8192' in m["coder:14b"].opencode.block
        small = m["small:latest"].opencode                                    # 4096 < 8192: variant needed
        assert small.at_min is not None and any("Variante mit num_ctx 8192" in r for r in small.reasons)
        await svc.start_bench("coder:14b", None, confirm=False)
        await svc.bench.task
        m = {x.name: x for x in (await svc.overview()).models}
        assert m["coder:14b"].opencode.state == "fits" and m["coder:14b"].opencode.eval_tps == 64.0
        assert m["small:latest"].opencode.tools_passed is None               # other digest: its own test
        h.ollama.answers = {c.request: _answer(content='{"name": "read_file"}') for c in CASES}
        await svc.start_bench("coder:14b", None, confirm=False)
        await svc.bench.task
        assert {x.name: x for x in (await svc.overview()).models}["coder:14b"].opencode.state == "no"

    run(go())


def test_usage_tags_by_name_and_what_the_rag_module_says(tmp_path):
    h = Harness(tmp_path)
    h.ollama.install("coder:14b", weights="w")
    h.ollama.install("nomic-embed-text:latest", caps=("embedding",))
    h.services["rag.service"] = SimpleNamespace(cfg=SimpleNamespace(embedding_model="nomic-embed-text",
                                                                     embedder="ollama"))

    async def go():
        svc = await _ready(h)
        before = len(h.published)
        ov = await svc.set_usage("coder:14b", ["test", "opencode", "opencode"], "  Standard für OpenCode  ")
        assert len(h.published) == before + 1                               # every tab sees it
        m = {x.name: x for x in ov.models}
        assert (m["coder:14b"].usage.tags, m["coder:14b"].usage.note) == (["opencode", "test"], "Standard für OpenCode")
        assert [(d.tag, d.source) for d in m["nomic-embed-text:latest"].usage.derived] == [
            ("rag", "RAG-Modul (embedding_model)")]
        svc2 = await h.service()                                             # kept in SQLite
        assert svc2.usage["coder:14b"].tags == ["opencode", "test"]
        await svc.set_usage("coder:14b", [], " ")                            # empty = forget
        assert "coder:14b" not in svc.usage and (await h.service()).usage == {}
        with pytest.raises(UnknownModel):
            await svc.set_usage("gone:1", ["test"], None)

    run(go())
