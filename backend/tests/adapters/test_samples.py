"""Every scenario folder - synthetic or captured - must replay through the fake adapters.

Value-free on purpose: real captures (real-*) may be re-recorded at any time.
"""
import asyncio
import json
import re

import pytest

from control_center.adapters.common import SAMPLES_DIR
from control_center.adapters.gpu import FakeGpu
from control_center.adapters.host import FakeHost
from control_center.adapters.ollama import FakeOllama, OllamaUnavailable
from control_center.core.config import SCENARIO_NAME_PATTERN

SCENARIOS = sorted(p.name for p in SAMPLES_DIR.iterdir() if p.is_dir() and not p.name.startswith("__"))


def test_known_scenarios_exist():
    assert {"normal", "offload", "idle", "ollama-down"} <= set(SCENARIOS)


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario_replays(name):
    folder = SAMPLES_DIR / name
    assert re.fullmatch(SCENARIO_NAME_PATTERN, name), "fake_scenario could not select this folder"
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["source"] in {"synthetic", "capture"} and "capturedAt" in meta

    host = FakeHost(folder).read(["*"], "short")
    assert host.cpu_count > 0 and 0 < host.ram_used_bytes <= host.ram_total_bytes
    assert all(d.total_bytes > 0 for d in FakeHost(folder).disks([]))
    if (folder / "gpu.json").is_file():
        gpu = FakeGpu(folder).read()
        assert gpu.vram_total_mib and 0 <= gpu.vram_used_mib <= gpu.vram_total_mib

    ollama = FakeOllama(folder)
    if (folder / "ollama-ps.json").is_file():
        assert asyncio.run(ollama.version())
        for m in asyncio.run(ollama.running()):
            assert m.name and m.size >= m.size_vram >= 0
    else:                                            # the "Ollama down" shape
        with pytest.raises(OllamaUnavailable):
            asyncio.run(ollama.running())
    if (folder / "ollama-tags.json").is_file():       # installed models (catalog); older captures lack it
        assert (folder / "ollama-show.json").is_file(), "tags without show: capture both or neither"
        for m in asyncio.run(ollama.tags()):
            details = asyncio.run(ollama.show(m.name))
            assert m.name and m.size >= 0 and details.name == m.name
