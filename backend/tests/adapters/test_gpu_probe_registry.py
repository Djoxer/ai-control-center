import asyncio
from types import SimpleNamespace

import httpx
import pynvml
import pytest
import respx

from control_center.adapters import gpu as gpu_mod
from control_center.adapters.common import SAMPLES_DIR
from control_center.adapters.gpu import FakeGpu, GpuUnavailable, NvmlGpu, decode_reasons
from control_center.adapters.probe import probe
from control_center.adapters.registry import Adapters, AdaptersNotOpen
from control_center.core.config import AdaptersConfig


# ---- GPU -------------------------------------------------------------------------------------

def test_decode_reasons():
    assert decode_reasons(None) == ()
    assert decode_reasons(0x4 | 0x20) == ("power_cap", "sw_thermal")


class FakeNvml:
    """Just enough of pynvml to drive NvmlGpu without a GPU."""

    def __init__(self):
        self.inits = self.shutdowns = 0
        self.memory_fails = False

    def install(self, monkeypatch):
        not_supported = pynvml.NVMLError(pynvml.NVML_ERROR_NOT_SUPPORTED)

        def unsupported(*_):
            raise not_supported

        def memory(_h):
            if self.memory_fails:
                raise pynvml.NVMLError(pynvml.NVML_ERROR_GPU_IS_LOST)
            return SimpleNamespace(used=2048 * 1024 * 1024, total=16303 * 1024 * 1024)

        for name, fn in {
            "nvmlInit": lambda: setattr(self, "inits", self.inits + 1),
            "nvmlShutdown": lambda: setattr(self, "shutdowns", self.shutdowns + 1),
            "nvmlDeviceGetHandleByIndex": lambda i: f"h{i}",
            "nvmlDeviceGetName": lambda h: b"NVIDIA GeForce RTX 5070 Ti",          # old pynvml: bytes
            "nvmlSystemGetDriverVersion": lambda: "581.42",
            "nvmlDeviceGetMemoryInfo": memory,
            "nvmlDeviceGetUtilizationRates": lambda h: SimpleNamespace(gpu=87),
            "nvmlDeviceGetTemperature": lambda h, s: 64,
            "nvmlDeviceGetPowerUsage": lambda h: 241_337,
            "nvmlDeviceGetEnforcedPowerLimit": lambda h: 300_000,
            "nvmlDeviceGetFanSpeed": unsupported,                                   # zero-fan mode
            "nvmlDeviceGetCurrentClocksEventReasons": lambda h: 0x4,
        }.items():
            monkeypatch.setattr(gpu_mod.pynvml, name, fn)
        return self


def test_nvml_reading_with_unsupported_field(monkeypatch):
    FakeNvml().install(monkeypatch)
    r = NvmlGpu().read()
    assert (r.name, r.vram_used_mib, r.vram_total_mib, r.power_w, r.power_limit_w) == \
           ("NVIDIA GeForce RTX 5070 Ti", 2048, 16303, 241.3, 300.0)
    assert r.fan_percent is None                                # unknown, not 0
    assert r.throttle_reasons == ("power_cap",)


def test_nvml_reinitialises_after_device_loss(monkeypatch):
    nvml = FakeNvml().install(monkeypatch)
    g = NvmlGpu()
    g.read()
    nvml.memory_fails = True
    with pytest.raises(GpuUnavailable):
        g.read()
    assert nvml.shutdowns == 1                                  # balanced init/shutdown
    nvml.memory_fails = False
    g.read()
    assert nvml.inits == 2


def test_nvml_missing_library(monkeypatch):
    def no_lib():
        raise pynvml.NVMLError(pynvml.NVML_ERROR_LIBRARY_NOT_FOUND)
    monkeypatch.setattr(gpu_mod.pynvml, "nvmlInit", no_lib)
    with pytest.raises(GpuUnavailable, match="NVML"):
        NvmlGpu().read()


def test_fake_gpu():
    assert FakeGpu(SAMPLES_DIR / "offload").read().throttle_reasons == ("power_cap",)
    with pytest.raises(GpuUnavailable):
        FakeGpu(SAMPLES_DIR / "does-not-exist").read()


# ---- probes ----------------------------------------------------------------------------------

@pytest.mark.parametrize("status, up", [(200, True), (405, True), (503, False)])
@respx.mock
def test_probe_status(status, up):
    respx.get("http://box:8000/mcp").respond(status)

    async def go():
        async with httpx.AsyncClient() as c:
            return await probe(c, "http://box:8000/mcp")
    r = asyncio.run(go())
    assert (r.up, r.http_status) == (up, status)
    assert (r.error is None) is up


@respx.mock
def test_probe_connection_error():
    respx.get("http://box:3000/health").mock(side_effect=httpx.ConnectTimeout(""))

    async def go():
        async with httpx.AsyncClient() as c:
            return await probe(c, "http://box:3000/health")
    r = asyncio.run(go())
    assert not r.up and r.http_status is None and r.error == "ConnectTimeout"


# ---- registry --------------------------------------------------------------------------------

def test_registry_modes_and_lifecycle():
    async def go():
        a = Adapters(AdaptersConfig(ollama="http", gpu="none", host="fake", ai_host="10.0.0.5"))
        with pytest.raises(AdaptersNotOpen):
            _ = a.ollama
        await a.open()
        try:
            assert a.gpu is None
            assert a.simulated == ["host"]
            assert a.ollama._base == "http://10.0.0.5:11434"    # {ai_host} expanded
        finally:
            await a.close()
    asyncio.run(go())


def test_expand_placeholder():
    assert AdaptersConfig(ai_host="192.168.1.20").expand("http://{ai_host}:3000/health") == \
        "http://192.168.1.20:3000/health"
