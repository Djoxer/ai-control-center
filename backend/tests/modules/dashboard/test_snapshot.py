"""Pure snapshot logic: placement, pinned/unloading, warnings and their thresholds."""
from datetime import datetime, timedelta, timezone

import pytest

from control_center.adapters.gpu import GpuReading
from control_center.adapters.host import DiskReading, HostReading, ProcessReading
from control_center.adapters.ollama import RunningModel
from control_center.adapters.probe import ProbeResult
from control_center.modules.dashboard.sampler import ErrorState, Readings
from control_center.modules.dashboard.service import _num, build_snapshot, classify, to_loaded_model
from control_center.modules.dashboard.settings import DashboardSettings, ProbeConfig

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
GIB = 1024 ** 3
CFG = DashboardSettings(probes=[ProbeConfig(key="openwebui", title="OpenWebUI", url="http://{ai_host}:3000")])


def model(name="m:latest", size=10 * GIB, vram=10 * GIB, expires=NOW + timedelta(minutes=5)) -> RunningModel:
    return RunningModel(name=name, digest="d-" + name, size=size, size_vram=vram, context_length=8192,
                        expires_at=expires, family="qwen3", parameter_size="9.0B", quantization="Q4_K_M")


def gpu(**kw) -> GpuReading:
    base = dict(name="RTX", driver_version="581.42", util_percent=50, vram_used_mib=8000, vram_total_mib=16303,
                temp_c=60, power_w=100.0, power_limit_w=300.0, fan_percent=30, throttle_reasons=())
    return GpuReading(**{**base, **kw})


def host(used_gib=10.0) -> HostReading:
    return HostReading(cpu_percent=5.0, cpu_count=16, ram_total_bytes=32 * GIB, ram_used_bytes=int(used_gib * GIB),
                       boot_time=NOW - timedelta(hours=2),
                       processes=(ProcessReading("ollama.exe", 1, 1.0, 100, "ollama.exe serve"),))


def readings(**kw) -> Readings:
    base = dict(ollama_online=True, ollama_latency_ms=3.2, models=[], ollama_version="0.12.6", gpu=gpu(),
                host=host(), disks=[DiskReading("C:\\", 2000 * GIB, 1000 * GIB, 1000 * GIB)],
                probes={"openwebui": ProbeResult(True, 200, 4.0, None)})
    return Readings(**{**base, **kw})


def snap(r: Readings, cfg: DashboardSettings = CFG):
    return build_snapshot(r, cfg, now=NOW, node_id="box", simulated=[])


def codes(s) -> list[str]:
    return [w.code for w in s.warnings]


@pytest.mark.parametrize("size, vram, threshold, expected", [
    (100, 100, 1.0, (1.0, "gpu")),
    (100, 84, 1.0, (0.84, "split")),
    (100, 0, 1.0, (0.0, "cpu")),
    (0, 0, 1.0, (None, "unknown")),             # model still loading: Ollama reports size 0
    (1000, 995, 0.99, (0.995, "gpu")),          # relaxed threshold tolerates rounding
    (100, 120, 1.0, (1.0, "gpu")),              # nonsense from the API is capped, not > 100 %
])
def test_classify(size, vram, threshold, expected):
    assert classify(size, vram, threshold) == expected


def test_pinned_and_unloading():
    pinned = to_loaded_model(model(expires=datetime(2318, 1, 1, tzinfo=timezone.utc)), 1.0, NOW)
    assert pinned.pinned and pinned.expires_at is None and not pinned.unloading
    gone = to_loaded_model(model(expires=NOW - timedelta(seconds=1)), 1.0, NOW)
    assert gone.unloading and not gone.pinned
    unknown = to_loaded_model(model(expires=None), 1.0, NOW)
    assert not unknown.pinned and not unknown.unloading


def test_healthy_box_has_no_warnings_and_camel_case_json():
    s = snap(readings(models=[model("small", 2 * GIB, 2 * GIB), model("big", 9 * GIB, 9 * GIB)]))
    assert s.warnings == [] and s.errors == []
    assert [m.name for m in s.models] == ["big", "small"]           # biggest first, stable order
    assert [x.key for x in s.services] == ["ollama", "openwebui"]
    assert s.host.uptime_s == 7200
    data = s.model_dump(mode="json", by_alias=True)
    assert {"ollamaOnline", "nodeId"} <= data.keys()
    assert "gpuRatio" in data["models"][0] and "vramUsedMib" in data["gpu"]


def test_split_model_is_critical_and_first():
    s = snap(readings(gpu=gpu(vram_used_mib=15900),
                      models=[model("coder:14b", 16 * GIB, int(13.5 * GIB))]))
    assert codes(s) == ["model_split", "vram_low"]
    w = s.warnings[0]
    assert w.level == "critical" and w.subject == "coder:14b" and "84 %" in w.message
    assert "403 MiB" in s.warnings[1].message                        # 16303 - 15900


def test_cpu_only_model_is_a_warning_not_critical():
    s = snap(readings(models=[model("embed", GIB, 0)]))
    assert [(w.code, w.level) for w in s.warnings] == [("model_cpu", "warning")]


def test_ollama_offline():
    r = readings(ollama_online=False, ollama_latency_ms=None,
                 errors={"ollama": ErrorState("ConnectError: refused", NOW - timedelta(minutes=1))})
    s = snap(r)
    assert codes(s) == ["ollama_offline"]
    assert s.services[0].up is False and s.services[0].error == "ConnectError: refused"
    assert s.errors[0].source == "ollama" and s.errors[0].since == NOW - timedelta(minutes=1)
    assert s.ollama_version == "0.12.6"                              # last known stays visible


@pytest.mark.parametrize("kw, expected", [
    (dict(temp_c=82), []),
    (dict(temp_c=83), ["gpu_hot"]),
    (dict(temp_c=None), []),                                          # unknown is not hot
    (dict(throttle_reasons=("idle", "power_cap")), []),               # normal under load
    (dict(throttle_reasons=("hw_thermal",)), ["gpu_throttled"]),
    (dict(vram_used_mib=16303 - 1500), []),                           # exactly at the headroom: ok
    (dict(vram_used_mib=16303 - 1499), ["vram_low"]),
])
def test_gpu_thresholds(kw, expected):
    assert codes(snap(readings(gpu=gpu(**kw)))) == expected


def test_host_disk_and_service_warnings():
    r = readings(host=host(used_gib=29.0),
                 disks=[DiskReading("D:\\", 1000 * GIB, 990 * GIB, 10 * GIB)],
                 probes={"openwebui": ProbeResult(False, None, None, "ConnectError")})
    s = snap(r)
    assert codes(s) == ["ram_high", "disk_low", "service_down"]
    assert s.warnings[1].subject == "D:\\" and "10,0 GiB" in s.warnings[1].message
    assert s.warnings[2].subject == "openwebui"


def test_failed_sources_are_null_not_zero():
    since = NOW - timedelta(seconds=30)
    r = readings(gpu=None, host=None, disks=None,
                 errors={"gpu": ErrorState("NVML: lost", since), "disks": ErrorState("x", since)})
    s = snap(r)
    assert s.gpu is None and s.host is None and s.disks is None
    assert [e.source for e in s.errors] == ["disks", "gpu"]
    assert s.warnings == []                                           # no data -> no made-up warnings


def test_german_number_format():
    assert _num(1500) == "1.500"
    assert _num(12.26, 1) == "12,3"
    assert _num(1234567.5, 1) == "1.234.567,5"
