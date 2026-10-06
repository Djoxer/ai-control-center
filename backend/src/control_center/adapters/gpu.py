"""GPU readings via NVML (the library behind nvidia-smi), without spawning nvidia-smi every tick.

NvmlGpu   real readings; NVML is initialised lazily and re-initialised after a failure
          (driver update, GPU reset), so the server never needs a restart for that.
FakeGpu   replays samples/<scenario>/gpu.json.

All calls are blocking (ctypes) -> callers run read() in a worker thread.
Single optional fields (fan, power, throttle reasons) are None when the driver does not report
them - under Windows/WDDM several are missing. None means "unknown", never 0.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, TypeVar

import pynvml

T = TypeVar("T")

# NVML "clocks event reasons" bitmask -> short names for the API. Order = display order.
THROTTLE_REASONS: dict[int, str] = {
    pynvml.nvmlClocksEventReasonGpuIdle: "idle",
    pynvml.nvmlClocksEventReasonApplicationsClocksSetting: "app_clocks",
    pynvml.nvmlClocksEventReasonSwPowerCap: "power_cap",           # normal under full load
    pynvml.nvmlClocksEventReasonHwSlowdown: "hw_slowdown",
    pynvml.nvmlClocksEventReasonSyncBoost: "sync_boost",
    pynvml.nvmlClocksEventReasonSwThermalSlowdown: "sw_thermal",
    pynvml.nvmlClocksEventReasonHwThermalSlowdown: "hw_thermal",
    pynvml.nvmlClocksEventReasonHwPowerBrakeSlowdown: "power_brake",
    pynvml.nvmlClocksEventReasonDisplayClockSetting: "display_clocks",
}


class GpuUnavailable(Exception):
    """No NVML (no NVIDIA driver, wrong index) or the device stopped answering."""


@dataclass(frozen=True)
class GpuReading:
    name: str                       # "NVIDIA GeForce RTX 5070 Ti"
    driver_version: str | None
    util_percent: int | None        # GPU core load over the driver's last sample period
    vram_used_mib: int              # whole card, all processes (desktop, browser, Ollama ...)
    vram_total_mib: int
    temp_c: int | None
    power_w: float | None
    power_limit_w: float | None     # enforced limit: the value the card actually throttles at
    fan_percent: int | None         # None on many cards in zero-fan mode or under WDDM
    throttle_reasons: tuple[str, ...]


class GpuAdapter(Protocol):
    simulated: bool

    def read(self) -> GpuReading: ...

    def close(self) -> None: ...


def decode_reasons(mask: int | None) -> tuple[str, ...]:
    if not mask:
        return ()
    return tuple(name for bit, name in THROTTLE_REASONS.items() if mask & bit)


def _text(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else value    # older pynvml returned bytes


def _optional(call: Callable[[], T]) -> T | None:
    """One unsupported field must not cost us the whole reading."""
    try:
        return call()
    except pynvml.NVMLError:
        return None


class NvmlGpu:
    simulated = False
    MIB = 1024 * 1024

    def __init__(self, index: int = 0) -> None:
        self.index = index
        self._handle = None
        self._name = "?"
        self._driver: str | None = None
        self._lock = threading.Lock()        # read() runs in worker threads; init/shutdown must not race

    def _ensure(self) -> None:
        if self._handle is not None:
            return
        try:
            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(self.index)
            self._name = _text(pynvml.nvmlDeviceGetName(handle))
            self._driver = _optional(lambda: _text(pynvml.nvmlSystemGetDriverVersion()))
            self._handle = handle
        except pynvml.NVMLError as exc:
            raise GpuUnavailable(f"NVML: {exc}") from exc

    def _reset(self) -> None:
        if self._handle is not None:
            self._handle = None
            _optional(pynvml.nvmlShutdown)       # NVML counts init/shutdown pairs; keep them balanced

    def read(self) -> GpuReading:
        with self._lock:
            self._ensure()
            h = self._handle
            try:
                mem = pynvml.nvmlDeviceGetMemoryInfo(h)     # the one field we cannot do without
            except pynvml.NVMLError as exc:
                self._reset()                               # stale handle after a GPU reset -> next tick re-inits
                raise GpuUnavailable(f"NVML: {exc}") from exc
            reasons = _optional(lambda: pynvml.nvmlDeviceGetCurrentClocksEventReasons(h))
            return GpuReading(
                name=self._name,
                driver_version=self._driver,
                util_percent=_optional(lambda: int(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)),
                vram_used_mib=int(mem.used // self.MIB),
                vram_total_mib=int(mem.total // self.MIB),
                temp_c=_optional(lambda: int(pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU))),
                power_w=_optional(lambda: round(pynvml.nvmlDeviceGetPowerUsage(h) / 1000, 1)),   # mW -> W
                power_limit_w=_optional(lambda: round(pynvml.nvmlDeviceGetEnforcedPowerLimit(h) / 1000, 1)),
                fan_percent=_optional(lambda: int(pynvml.nvmlDeviceGetFanSpeed(h))),
                throttle_reasons=decode_reasons(reasons),
            )

    def close(self) -> None:
        with self._lock:
            self._reset()


class FakeGpu:
    """Replays gpu.json of a scenario folder (re-read on every call). Missing file = no GPU."""
    simulated = True

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def read(self) -> GpuReading:
        path = self.folder / "gpu.json"
        if not path.is_file():
            raise GpuUnavailable(f"fake: gpu.json missing in scenario '{self.folder.name}'")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["throttle_reasons"] = tuple(data.get("throttle_reasons") or ())
        return GpuReading(**data)

    def close(self) -> None:
        pass
