"""Sampler clocks and error bookkeeping, driven by fake adapters and a hand-moved clock."""
import asyncio
from collections import Counter

import httpx
import respx

from control_center.adapters.common import SAMPLES_DIR
from control_center.adapters.registry import Adapters
from control_center.core.config import AdaptersConfig
from control_center.modules.dashboard.sampler import Sampler
from control_center.modules.dashboard.settings import DashboardSettings, ProbeConfig


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def run(fn, scenario="normal", adapters_kw=None, **cfg_kw):
    """Open fake adapters, build a spied sampler, hand both to the async test body."""
    async def go():
        cfg = AdaptersConfig(**{"ollama": "fake", "gpu": "fake", "host": "fake", "fake_scenario": scenario,
                                **(adapters_kw or {})})
        adapters = Adapters(cfg)
        await adapters.open()
        clock = Clock()
        sampler = Sampler(adapters, DashboardSettings(**{"probes": [], **cfg_kw}), clock=clock)
        calls: Counter[str] = Counter()
        for name in ("_host", "_probes", "_ollama_version", "_disks"):
            original = getattr(sampler, name)

            async def spy(original=original, name=name):
                calls[name] += 1
                await original()
            setattr(sampler, name, spy)
        try:
            return await fn(sampler, adapters, clock, calls)
        finally:
            await adapters.close()
    return asyncio.run(go())


def test_clocks_run_what_is_due():
    async def body(s, _a, clock, calls):
        seen = []
        for t in (0, 2, 4, 6, 8, 10, 12, 58, 60, 69.2):
            clock.t = t
            await s.sample()
            await s.settle()                                  # let the background groups finish
            seen.append((t, calls["_host"], calls["_disks"]))
        return seen
    seen = run(body)
    assert seen == [
        (0, 1, 1),                  # first tick: everything
        (2, 1, 1), (4, 1, 1), (6, 1, 1), (8, 1, 1),
        (10, 2, 1),                 # medium every 10 s
        (12, 2, 1),
        (58, 3, 1),                 # 58 - 10 >= 9: medium; slow not yet (58 < 59)
        (60, 3, 2),                 # 60 - 58 < 9: no medium; slow due (60 >= 59)
        (69.2, 4, 2),               # jitter tolerance: 9.2 s count as "10 s are over"
    ]


def test_ollama_down_then_back_refreshes_version_at_once():
    async def body(s, adapters, clock, calls):
        r = await s.sample()
        assert not r.ollama_online and r.models == [] and r.ollama_version is None
        assert set(r.errors) == {"ollama", "ollama_version"}  # the snapshot hides the second one
        first_since = r.errors["ollama"].since
        clock.t = 2
        await s.sample()
        assert r.errors["ollama"].since == first_since        # one outage, one start time
        adapters.ollama.folder = SAMPLES_DIR / "normal"       # Ollama comes back
        clock.t = 4
        await s.sample()
        return r, calls
    r, calls = run(body, scenario="ollama-down")
    assert r.ollama_online and len(r.models) == 2 and r.ollama_latency_ms is not None
    assert r.ollama_version == "0.12.6" and calls["_ollama_version"] == 2   # without waiting for the slow clock
    assert r.errors == {}


def test_slow_source_times_out_alone():
    class SlowOllama:
        simulated = False

        async def running(self):
            await asyncio.sleep(5)

        async def version(self):
            return "x"

    async def body(s, adapters, _clock, _calls):
        adapters._ollama = SlowOllama()
        started = asyncio.get_running_loop().time()
        r = await s.sample()
        return r, asyncio.get_running_loop().time() - started
    r, took = run(body, adapters_kw={"timeout_s": 0.05})
    assert took < 2
    assert r.errors["ollama"].message.startswith("no answer within")
    assert r.gpu is not None and r.host is not None           # the others delivered anyway


def test_bug_in_adapter_is_contained(caplog):
    class BrokenGpu:
        simulated = False

        def read(self):
            raise KeyError("oops")

        def close(self):
            pass

    async def body(s, adapters, _clock, _calls):
        adapters._gpu = BrokenGpu()
        r = await s.sample()
        await s.sample()                                      # second failure: no second log line
        return r
    r = run(body)
    assert r.gpu is None and r.errors["gpu"].message == "KeyError: 'oops'"
    failures = [rec for rec in caplog.records if "failed" in rec.getMessage()]
    assert len(failures) == 1 and failures[0].exc_info              # unexpected -> with traceback


def test_gpu_none_is_not_an_error():
    r = run(lambda s, *_: s.sample(), adapters_kw={"gpu": "none"})
    assert r.gpu is None and "gpu" not in r.errors


@respx.mock
def test_probes_expand_ai_host():
    respx.get("http://box:8000/mcp").respond(406)
    respx.get("http://box:6333/healthz").mock(side_effect=httpx.ConnectError("refused"))
    probes = [ProbeConfig(key="mcp", title="MCP", url="http://{ai_host}:8000/mcp"),
              ProbeConfig(key="qdrant", title="Qdrant", url="http://{ai_host}:6333/healthz")]
    r = run(lambda s, *_: s.sample(), adapters_kw={"ai_host": "box"}, probes=probes)
    assert r.probes["mcp"].up and r.probes["mcp"].http_status == 406
    assert not r.probes["qdrant"].up and "refused" in r.probes["qdrant"].error
    assert "qdrant" not in r.errors                           # a down service is a warning, not a source error


def test_slow_medium_group_does_not_delay_the_fast_tick():
    async def body(s, _a, clock, calls):
        original = s._probes

        async def hanging_probes():                           # e.g. Windows: ~2 s per refused connect
            await asyncio.sleep(0.5)
            await original()
        s._probes = hanging_probes
        loop = asyncio.get_running_loop()
        await s.sample()                                      # first tick waits: complete picture
        clock.t = 10
        started = loop.time()
        await s.sample()                                      # medium launched in the background
        fast = loop.time() - started
        clock.t = 20
        await s.sample()                                      # due again but still busy -> skipped
        busy_skipped = calls["_host"]
        await s.settle()
        clock.t = 22
        await s.sample()                                      # 22 - 10 >= 9 -> runs again
        await s.settle()
        return fast, busy_skipped, calls["_host"]
    fast, busy_skipped, total = run(body)
    assert fast < 0.3
    assert busy_skipped == 2 and total == 3
