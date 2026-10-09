"""The configured adapter set. Built once per app, opened in the lifespan, shared via ctx.adapters.

    [adapters] ollama = "http" | "fake"    gpu = "nvml" | "fake" | "none"    host = "psutil" | "fake"
               library = "http" | "fake"   (Ollama's model library, for the candidate check of the catalog)

On the second PC: real Ollama on the AI box (ai_host = its IP), fake GPU and host,
because NVML and psutil can only see the machine they run on.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from control_center.adapters.common import SAMPLES_DIR
from control_center.adapters.gpu import FakeGpu, GpuAdapter, NvmlGpu
from control_center.adapters.host import FakeHost, HostAdapter, PsutilHost
from control_center.adapters.library import FakeLibrary, HttpLibrary, LibraryAdapter
from control_center.adapters.ollama import FakeOllama, HttpOllama, OllamaAdapter
from control_center.core.config import AdaptersConfig

log = logging.getLogger("control_center.adapters")


class AdaptersNotOpen(RuntimeError):
    pass


class Adapters:
    def __init__(self, cfg: AdaptersConfig) -> None:
        self.cfg = cfg
        self._http: httpx.AsyncClient | None = None
        self._ollama: OllamaAdapter | None = None
        self._gpu: GpuAdapter | None = None
        self._host: HostAdapter | None = None
        self._library: LibraryAdapter | None = None
        self._open = False

    @property
    def scenario_dir(self) -> Path:
        return SAMPLES_DIR / self.cfg.fake_scenario

    async def open(self) -> None:
        """Build the configured implementations. Never connects: a dead Ollama must not block startup."""
        cfg = self.cfg
        # one connection pool for Ollama and all probes; created here so it lives in the server's event loop
        self._http = httpx.AsyncClient(timeout=cfg.timeout_s)
        self._ollama = (FakeOllama(self.scenario_dir) if cfg.ollama == "fake"
                        else HttpOllama(self._http, cfg.expand(cfg.ollama_url)))
        self._gpu = {"nvml": lambda: NvmlGpu(cfg.gpu_index), "fake": lambda: FakeGpu(self.scenario_dir),
                     "none": lambda: None}[cfg.gpu]()
        self._host = FakeHost(self.scenario_dir) if cfg.host == "fake" else PsutilHost()
        # the library is outside the AI box: not part of a scenario, one sample set for all of them
        self._library = FakeLibrary() if cfg.library == "fake" else HttpLibrary(self._http)
        self._open = True
        if self.simulated:
            log.warning("adapters simulated from scenario '%s': %s", cfg.fake_scenario, ", ".join(self.simulated))
            if not self.scenario_dir.is_dir():
                # not fatal: every fake read reports the missing file, the dashboard shows it as an error
                log.error("fake scenario folder missing: %s", self.scenario_dir)
        if cfg.library == "fake":
            log.warning("model library simulated: candidate checks answer from adapters/library-samples")

    async def close(self) -> None:
        if self._gpu is not None:
            self._gpu.close()
        if self._http is not None:
            await self._http.aclose()
        self._http = self._ollama = self._gpu = self._host = self._library = None
        self._open = False

    def _need(self, value):
        if not self._open:
            raise AdaptersNotOpen("adapters used before the app lifespan opened them")
        return value

    @property
    def http(self) -> httpx.AsyncClient:
        return self._need(self._http)

    @property
    def ollama(self) -> OllamaAdapter:
        return self._need(self._ollama)

    @property
    def gpu(self) -> GpuAdapter | None:
        """None when [adapters] gpu = "none"."""
        return self._need(self._gpu)

    @property
    def host(self) -> HostAdapter:
        return self._need(self._host)

    @property
    def library(self) -> LibraryAdapter:
        """Not in `simulated`: that list labels the dashboard's sources of the AI box; the catalog asks
        library.simulated itself."""
        return self._need(self._library)

    @property
    def simulated(self) -> list[str]:
        """Which sources show recorded data instead of live values - the UI must say so."""
        cfg = self.cfg
        return [name for name, fake in (("ollama", cfg.ollama == "fake"), ("gpu", cfg.gpu == "fake"),
                                         ("host", cfg.host == "fake")) if fake]
