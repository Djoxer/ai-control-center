"""Catalog service: keeps the inventory of installed models current and records what was observed.

One background loop, two clocks:
- every observe_interval_s (10 s): /api/ps -> which model runs with which context, how much VRAM
  (an upsert per model x context x GPU = the "measured" values). When nothing is loaded, the GPU's used
  memory is what OTHER programs occupy - the budget of the card shrinks by that.
- every refresh_interval_s (60 s): /api/tags, and /api/show for every model that is new or changed.

Test runs (bench.py) load one model on request; the preflight here refuses what would split.

The page gets the whole overview via GET and, after every meaningful change, via SSE (topic
catalog.overview, with a revision against overtaking). Ollama down = last known inventory from SQLite
plus an error, never an empty page.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from control_center.adapters.gpu import GpuUnavailable
from control_center.adapters.ollama import (
    InstalledModel, ModelDetails, OllamaModelMissing, OllamaUnavailable, RunningModel,
)
from control_center.core.context import AppContext
from control_center.modules.catalog.bench import BenchJob, new_job, run_bench
from control_center.modules.catalog.collector import ModelRecord, ServerDefaults, read_server_config, record_from
from control_center.modules.catalog.estimate import (
    GIB, VISION_NOTE, Budget, ContextResult, Estimate, Point, Seen, effective_context, effective_kv_type,
    estimate_vram, extra_beyond_reserve, placement, verdict,
)
from control_center.modules.catalog.estimate import Verdict as Judgement
from control_center.modules.catalog.lineage import build_lineage, changes, norm
from control_center.modules.catalog.repository import CatalogRepository, ModelRow, ObservationRow, UsageRow
from control_center.modules.catalog.schemas import (
    Assumptions, BenchAccess, BenchStatus, BudgetInfo, CatalogModel, CatalogOverview, ContextInfo, HardwareInfo,
    DerivedTag, ModelGroup, Observation, OllamaState, OpencodeFit, OverheadInfo, ParentInfo, Preflight,
    RemovedModel, ServerConfig, UsageInfo, Verdict, VramEstimate,
)
from control_center.modules.catalog.settings import CatalogSettings
from control_center.modules.catalog.suitability import opencode_fit

log = logging.getLogger("control_center.modules.catalog")

TOPIC = "catalog.overview"
BENCH_TOPIC = "catalog.bench"       # full BenchStatus on every phase change
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
SHOW_PARALLEL = 4                   # /api/show reads only GGUF headers, a few at a time is fine
OVERRIDE_KEYS = ("server_context_length", "kv_cache_type", "flash_attention", "num_parallel")
OTHER_KEY = "other_usage"           # catalog_state: VRAM of other programs, measured while Ollama was empty
OTHER_SAVE_STEP = 64 * 1024 ** 2    # write the measurement only when it moved by more than this
BENCHES_PER_MODEL = 5               # shown on the page; SQLite keeps [modules.catalog] keep_tests
CTX_STEPS = (2048, 4096, 8192, 16384, 24576, 32768, 49152, 65536, 98304, 131072, 262144)


class OllamaDown(Exception):
    """/api/tags failed. German message."""


class UnknownModel(KeyError):
    pass


class Busy(Exception):
    """A test run (or a RAG reindex) is running. German message."""


class Refused(Exception):
    """The preflight says no (would split, embedding model, locked here). German message."""


class NeedsConfirm(Exception):
    """'knapp' - allowed only with confirm=true. German message."""


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class Effective:
    """Server defaults after merging [modules.catalog] over server.log."""
    context_length: int | None
    kv_cache_type: str | None
    flash_attention: bool | None
    num_parallel: int | None


class CatalogService:
    def __init__(self, ctx: AppContext, cfg: CatalogSettings, read_log: bool | None = None) -> None:
        self.ctx = ctx
        self.cfg = cfg
        self.repo = CatalogRepository(ctx.db)
        acfg = ctx.settings.adapters
        local = acfg.ai_host in LOCAL_HOSTS
        # server.log only says something about an Ollama on THIS machine; a fake scenario has no log
        self.read_log = (local and acfg.ollama == "http") if read_log is None else read_log
        # NVML sees only this machine: with a remote Ollama its GPU is not ours to report. A fake GPU replays
        # a recorded scenario of the AI box - good enough for the verdict on the second PC, marked as simulated.
        self.gpu_visible = local or acfg.ollama == "fake" or acfg.gpu == "fake"
        self.gpu_note = f"GPU simuliert (Szenario „{acfg.fake_scenario}“)" if acfg.gpu == "fake" else None
        self.rows: dict[str, ModelRow] = {}               # every known model, removed ones included
        self.loaded: dict[str, tuple[str, int, str]] = {}  # name -> (digest, num_ctx, hardware) in /api/ps
        self.ollama = OllamaState(online=False, simulated="ollama" in ctx.adapters.simulated)
        self.server_log: tuple[ServerDefaults, str] | None = None
        self.server_note: str | None = None
        self.hardware = HardwareInfo()
        self.revision = 0
        self.refreshed_at: datetime | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._last_refresh: float | None = None
        # the first /api/ps after a start sees models loaded before it - they were counted back then
        self._observed_once = False
        # VRAM of other programs (bytes, unix time), measured while Ollama had nothing loaded
        self.other: tuple[int, float] | None = None
        # last NVML reading of an empty /api/ps: "other programs" counts only when the next one agrees
        self._idle_used: int | None = None
        self.bench: BenchJob | None = None                # the running test run
        self.benches: list[BenchStatus] = []              # finished ones, newest first
        self.usage: dict[str, UsageRow] = {}              # what the team uses a model for, by name

    # ---- lifecycle --------------------------------------------------------------------------------

    async def start_up(self) -> None:
        await self.repo.create()
        try:
            self.rows = {r.record.name: r for r in await self.repo.load_models()}
        except Exception:
            log.exception("catalog: stored inventory unreadable - starting empty")
        await self._load_state()
        await self._read_environment()
        self._task = asyncio.create_task(self._run(), name="catalog")

    async def _load_state(self) -> None:
        try:
            stored = await self.repo.get_state(OTHER_KEY)
            if stored is not None and isinstance(stored[0], int):
                self.other = (stored[0], stored[1])
            self.usage = await self.repo.load_usage()
            for raw in await self.repo.load_benches(self.cfg.keep_tests):
                try:
                    self.benches.append(BenchStatus.model_validate(raw))
                except ValueError:
                    continue                                  # written by another version: skip it
        except Exception:
            log.exception("catalog: stored state unreadable")

    async def shut_down(self) -> None:
        tasks = [t for t in (self._task, self.bench.task if self.bench else None) if t is not None]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                now = time.monotonic()
                if self._last_refresh is None or now - self._last_refresh >= self.cfg.refresh_interval_s:
                    self._last_refresh = now
                    try:
                        await self.refresh()
                    except OllamaDown:
                        pass                                  # state and error are in the overview already
                await self.observe()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("catalog tick failed")          # a bug must not end the loop
            await asyncio.sleep(self.cfg.observe_interval_s)

    # ---- environment: server defaults and GPU ------------------------------------------------------

    async def _read_environment(self) -> bool:
        """Re-read server.log and the GPU. True if anything the page shows changed."""
        before = (self.server_log, self.server_note, self.hardware)
        if self.read_log:
            try:
                self.server_log = await asyncio.to_thread(read_server_config, self.cfg.ollama_log_paths)
                self.server_note = None if self.server_log else "Keine „server config“-Zeile im Ollama-Log gefunden."
            except Exception as exc:                          # unreadable log: say so, keep running
                self.server_log, self.server_note = None, f"Ollama-Log nicht lesbar: {exc}"
        else:
            host = self.ctx.settings.adapters.ai_host
            self.server_note = ("Simulation: kein Ollama-Log." if self.ctx.settings.adapters.ollama == "fake"
                                else f"Ollama läuft auf {host} – sein Log ist von hier aus nicht lesbar.")
        self.hardware = await self._read_gpu()
        return before != (self.server_log, self.server_note, self.hardware)

    async def gpu_used(self) -> int | None:
        """Used VRAM of the whole card (bytes) right now, None when the GPU is not ours to read."""
        gpu = self.ctx.adapters.gpu
        if not self.gpu_visible or gpu is None:
            return None
        try:
            return (await asyncio.to_thread(gpu.read)).vram_used_mib * 1024 * 1024
        except (GpuUnavailable, OSError):
            return None

    async def record_other_usage(self, used: int) -> None:
        """Ollama holds nothing: whatever the card shows belongs to other programs. Saved when it moved."""
        now = time.time()
        moved = self.other is None or abs(self.other[0] - used) > OTHER_SAVE_STEP
        self.other = (used, now)
        if moved:
            try:
                await self.repo.set_state(OTHER_KEY, used, now)
            except Exception:
                log.exception("catalog: cannot store the VRAM of other programs")

    def budget(self) -> Budget:
        if self.other is not None:
            other, source = self.other[0], "measured"
        else:
            other, source = round(self.cfg.other_usage_gib * GIB), "assumed"
        return Budget(total_bytes=self.hardware.vram_total_bytes, other_bytes=other, other_source=source,
                      reserve_bytes=round(self.cfg.ollama_reserve_gib * GIB))

    def kv_type(self) -> str:
        eff = self.effective()
        return effective_kv_type(eff.kv_cache_type, eff.flash_attention)[0]

    async def _read_gpu(self) -> HardwareInfo:
        if not self.gpu_visible:
            host = self.ctx.settings.adapters.ai_host
            return HardwareInfo(note=f"Ollama läuft auf {host} – die GPU dort ist von hier aus nicht sichtbar.")
        gpu = self.ctx.adapters.gpu
        if gpu is None:
            return HardwareInfo(note="Keine GPU eingerichtet ([adapters] gpu = \"none\").")
        try:
            g = await asyncio.to_thread(gpu.read)
        except (GpuUnavailable, OSError) as exc:
            return HardwareInfo(note=f"GPU nicht lesbar: {exc}")
        return HardwareInfo(key=f"{g.name} · {g.vram_total_mib} MiB", gpu_name=g.name,
                            vram_total_bytes=g.vram_total_mib * 1024 * 1024, note=self.gpu_note)

    def effective(self) -> Effective:
        log_values = self.server_log[0] if self.server_log else ServerDefaults()
        c = self.cfg
        return Effective(
            context_length=c.server_context_length or log_values.context_length,
            kv_cache_type=c.kv_cache_type or log_values.kv_cache_type,
            flash_attention=c.flash_attention if c.flash_attention is not None else log_values.flash_attention,
            num_parallel=c.num_parallel or log_values.num_parallel,
        )

    def server_config(self) -> ServerConfig:
        log_values, log_file = self.server_log if self.server_log else (ServerDefaults(), None)
        eff = self.effective()
        overridden = [k for k in OVERRIDE_KEYS if getattr(self.cfg, k) is not None]
        if overridden and self.server_log:
            source = "mixed"
        elif overridden:
            source = "config"
        elif self.server_log:
            source = "log"
        else:
            source = "unknown"
        return ServerConfig(
            source=source, log_file=log_file, note=None if self.server_log else self.server_note,
            context_length=eff.context_length, kv_cache_type=eff.kv_cache_type,
            flash_attention=eff.flash_attention, num_parallel=eff.num_parallel,
            max_loaded_models=log_values.max_loaded_models, keep_alive=log_values.keep_alive,
            overridden=overridden,
        )

    # ---- inventory --------------------------------------------------------------------------------

    async def _show(self, tag: InstalledModel, sem: asyncio.Semaphore) -> tuple[ModelDetails | None, str | None]:
        async with sem:
            try:
                return await self.ctx.adapters.ollama.show(tag.name, timeout=self.cfg.show_timeout_s), None
            except OllamaModelMissing:
                return None, "Ollama kennt das Modell nicht mehr (gerade gelöscht?)"
            except OllamaUnavailable as exc:
                return None, f"/api/show fehlgeschlagen: {exc}"

    async def refresh(self, name: str | None = None, force: bool = False) -> None:
        """/api/tags, then /api/show for new/changed models (force: all; name: just that one).

        Raises OllamaDown (Ollama unreachable) or UnknownModel (name not installed). Publishes after
        the lock is released, so a waiting second refresh never delays the SSE message.
        """
        async with self._lock:
            publish, error = await self._refresh_locked(name, force)
        if publish:
            await self._publish()
        if error is not None:
            raise error

    async def _refresh_locked(self, name: str | None, force: bool) -> tuple[bool, Exception | None]:
        ollama = self.ctx.adapters.ollama
        try:
            tags = await ollama.tags()
        except OllamaUnavailable as exc:
            changed = self.ollama.online or self.ollama.error != str(exc)
            self.ollama = OllamaState(online=False, error=str(exc), version=self.ollama.version,
                                      simulated=self.ollama.simulated)
            return changed, OllamaDown(f"Ollama nicht erreichbar: {exc}")
        installed = {t.name: t for t in tags}
        if name is not None and name not in installed:
            return False, UnknownModel(name)
        try:
            version = await ollama.version()
        except OllamaUnavailable:
            version = None
        now = datetime.now(timezone.utc)
        todo = []
        for t in tags:
            row = self.rows.get(t.name)
            stale = (row is None or row.removed_at is not None or row.record.digest != t.digest
                     or row.record.show_error is not None)
            if stale or name == t.name or (force and name is None):
                todo.append(t)
        sem = asyncio.Semaphore(SHOW_PARALLEL)
        shown = await asyncio.gather(*(self._show(t, sem) for t in todo))
        fresh = [record_from(t, details, now, err) for t, (details, err) in zip(todo, shown)]
        changed = [r for r in fresh if not self._same(r)]
        gone = [n for n, row in self.rows.items() if row.removed_at is None and n not in installed]
        ts = now.timestamp()
        fresh_names = {r.name for r in fresh}
        await self._store_inventory(fresh, gone, [n for n in installed if n not in fresh_names], ts)
        for r in fresh:
            old = self.rows.get(r.name)
            self.rows[r.name] = ModelRow(r, old.first_seen if old else ts, ts, None)
        for n in gone:
            old = self.rows[n]
            self.rows[n] = ModelRow(old.record, old.first_seen, old.last_seen, ts)
        self._forget_old(ts)
        env_changed = await self._read_environment()
        online_changed = not self.ollama.online or self.ollama.version != version
        self.ollama = OllamaState(online=True, version=version, simulated=self.ollama.simulated)
        self.refreshed_at = now
        if changed or gone:
            log.info("catalog: %d new/changed, %d removed", len(changed), len(gone))
        return bool(changed or gone or env_changed or online_changed or force or name is not None), None

    def _same(self, rec: ModelRecord) -> bool:
        """Unchanged since the last /api/show? The collection time itself does not count."""
        row = self.rows.get(rec.name)
        if row is None or row.removed_at is not None:
            return False
        return replace(row.record, collected_at=None) == replace(rec, collected_at=None)

    async def _store_inventory(self, fresh: list[ModelRecord], gone: list[str], unchanged: list[str],
                               ts: float) -> None:
        """A full disk or a locked file must not stop the catalog: memory stays right, SQLite catches up."""
        try:
            await self.repo.save_models(fresh, ts)
            await self.repo.mark_removed(gone, ts)
            await self.repo.touch(unchanged, ts)
            await self.repo.prune(ts, self.cfg.keep_removed_days)
        except Exception:
            log.exception("catalog: cannot write the inventory to SQLite")

    def _forget_old(self, ts: float) -> None:
        limit = ts - self.cfg.keep_removed_days * 86400
        for n in [n for n, r in self.rows.items() if r.removed_at is not None and r.removed_at < limit]:
            del self.rows[n]

    # ---- observations -----------------------------------------------------------------------------

    async def observe(self) -> None:
        """/api/ps -> one upsert per loaded model. A new load (or a new context) counts once.

        Paused during a test run: the run records its own measurement and would be counted twice.
        """
        if self.bench is not None:
            return
        try:
            running = await self.ctx.adapters.ollama.running()
        except OllamaUnavailable:
            self._idle_used = None
            if self.loaded:
                self.loaded = {}
                await self._publish()
            return
        await self._watch_idle_card(running)
        hardware = self.hardware.key or ""
        ts = time.time()
        current: dict[str, tuple[str, int, str]] = {}
        before = set(self.loaded.values())
        first, self._observed_once = not self._observed_once, True
        for m in running:
            if m.size <= 0:
                continue                                    # still loading: Ollama reports 0
            key = (m.digest, m.context_length or 0, hardware)
            current[m.name] = key
            try:
                # a brand-new row always starts at 1; an existing one counts only real (re)loads
                await self.repo.observe(m.digest, m.name, key[1], hardware, m.size, m.size_vram, ts,
                                        new_load=key not in before and not first)
            except Exception:
                log.exception("catalog: cannot store the observation of %s", m.name)
        if current != self.loaded:
            self.loaded = current
            await self._publish()

    async def _watch_idle_card(self, running: list[RunningModel]) -> None:
        """Ollama holds nothing -> what the card shows belongs to other programs (desktop, browser ...).

        Counted only when two empty ticks in a row agree: a model Ollama is just loading can hold VRAM
        before /api/ps lists it, and that must not end up as "other programs" for hours.
        """
        if running:
            self._idle_used = None
            return
        used = await self.gpu_used()
        prev, self._idle_used = self._idle_used, used
        if used is None or prev is None or abs(prev - used) > OTHER_SAVE_STEP:
            return
        before = self.other
        await self.record_other_usage(used)
        if before is None or abs(before[0] - used) > OTHER_SAVE_STEP:
            await self._publish()

    async def record_observation(self, m: RunningModel, new_load: bool) -> None:
        """Called by a test run: the measurement goes where /api/ps sightings go."""
        try:
            await self.repo.observe(m.digest, m.name, m.context_length or 0, self.hardware.key or "", m.size,
                                    m.size_vram, time.time(), new_load=new_load)
        except Exception:
            log.exception("catalog: cannot store the observation of %s", m.name)

    # ---- preflight and test runs ------------------------------------------------------------------

    def bench_access(self) -> BenchAccess:
        acfg = self.ctx.settings.adapters
        if acfg.ollama == "fake" or acfg.ai_host in LOCAL_HOSTS or self.cfg.allow_remote_tests:
            return BenchAccess(allowed=True)
        return BenchAccess(allowed=False, reason=(
            f"Testläufe gesperrt: Ollama läuft auf {acfg.ai_host}. Ein Test entlädt dort das Modell, mit dem "
            f"gerade jemand arbeitet. Freigeben mit allow_remote_tests = true in [modules.catalog]."))

    def _model(self, name: str) -> ModelRecord:
        row = self.rows.get(name)
        if row is None or row.removed_at is not None:
            raise UnknownModel(name)
        return row.record

    async def preflight(self, name: str, num_ctx: int | None = None) -> Preflight:
        """What loading `name` with `num_ctx` (None = effective) would cost, and whether a test may run."""
        rec = self._model(name)
        observed = await self._observations()
        eff = self.effective()
        kv_type, kv_note = effective_kv_type(eff.kv_cache_type, eff.flash_attention)
        ctx = effective_context(rec, eff.context_length, eff.num_parallel, self.cfg, requested=num_ctx)
        est, v = self._estimate(rec, ctx, kv_type, kv_note, observed)
        reason, allowed, confirm = None, True, False
        access = self.bench_access()
        if "embedding" in rec.capabilities and "completion" not in rec.capabilities:
            allowed, reason = False, "Einbettungsmodelle haben (noch) keinen Testlauf."
        elif not access.allowed:
            allowed, reason = False, access.reason
        elif v.state == "split":
            # the verdict message above it says the numbers; this says why the button stays grey
            allowed, reason = False, ("Gesperrt: Kippt das Modell in den Teil-Offload, stürzt der Runner ab. "
                                      "Einen kleineren Kontext wählen.")
        elif v.state in ("tight", "unknown"):
            confirm = True
            reason = v.message if v.state == "tight" else "Prognose unklar – Absturzgefahr bei Teil-Offload."
        upper = ctx.trained or max(CTX_STEPS)
        return Preflight(
            name=name, context=self._context_info(ctx), estimate=self._estimate_info(est), verdict=self._verdict(v),
            allowed=allowed, needs_confirm=confirm and allowed, reason=reason,
            will_unload=sorted(n for n in self.loaded if n != name),
            suggestions=sorted({c for c in CTX_STEPS if c <= upper} | {ctx.effective}),
        )

    async def start_bench(self, name: str, num_ctx: int | None, confirm: bool) -> BenchStatus:
        if self.bench is not None:
            raise Busy("Es läuft schon ein Testlauf – erst abwarten.")
        rag = self.ctx.service("rag.service")
        if getattr(rag, "current", None) is not None:
            raise Busy("Gerade läuft eine RAG-Indexierung – sie braucht das Einbettungsmodell. Danach testen.")
        pre = await self.preflight(name, num_ctx)
        if not pre.allowed:
            raise Refused(pre.reason or "Testlauf nicht möglich.")
        if pre.needs_confirm and not confirm:
            raise NeedsConfirm(pre.reason or "Nur mit Bestätigung.")
        job = new_job(name, self._model(name).digest, pre.context.effective)
        self.bench = job                                    # set before the task runs: a double click gets Busy
        job.task = asyncio.create_task(run_bench(self, job), name=f"catalog-bench-{job.status.id}")
        await self.publish_bench(job)
        return job.status

    async def publish_bench(self, job: BenchJob) -> None:
        st = job.status
        st.revision += 1
        st.as_of = datetime.now(timezone.utc)
        self.ctx.events.publish(BENCH_TOPIC, st.model_dump(mode="json", by_alias=True))

    async def finish_bench(self, job: BenchJob) -> None:
        """Called by run_bench at the very end: keep the report, free the slot, tell the page."""
        st = job.status
        # free the slot before the first await: whoever sees "done" (GET /bench) may start the next run
        self.benches.insert(0, st)
        del self.benches[self.cfg.keep_tests:]
        self.bench = None
        self.loaded = {}                                    # the next /api/ps tick sees the real state
        try:
            await self.repo.save_bench(st.id, st.name, job.digest, st.created_at.timestamp(),
                                       st.model_dump(mode="json"))
            await self.repo.prune_benches(self.cfg.keep_tests)
        except Exception:
            log.exception("catalog: cannot store test run %s", st.id)
        await self.publish_bench(job)
        await self._publish()

    # ---- usage ---------------------------------------------------------------------------------------

    async def set_usage(self, name: str, tags: list[str], note: str | None) -> CatalogOverview:
        """Tags in a fixed order, no doubles; empty tags and note = forget. Kept by name, also when the
        model is pulled again."""
        self._model(name)
        order = ["opencode", "openwebui", "rag", "test", "remove"]
        clean = [t for t in order if t in set(tags)]
        text = (note or "").strip() or None
        now = time.time()
        await self.repo.save_usage(name, clean, text, now)
        if clean or text:
            self.usage[name] = UsageRow(name, clean, text, now)
        else:
            self.usage.pop(name, None)
        await self._publish()
        return await self.overview()

    def _usage_info(self, rec: ModelRecord) -> UsageInfo:
        row = self.usage.get(rec.name)
        derived = []
        rag = self.ctx.service("rag.service")
        rag_cfg = getattr(rag, "cfg", None)
        model = getattr(rag_cfg, "embedding_model", None)
        if model and getattr(rag_cfg, "embedder", "ollama") == "ollama" and norm(model) == norm(rec.name):
            derived.append(DerivedTag(tag="rag", source="RAG-Modul (embedding_model)"))
        return UsageInfo(tags=row.tags if row else [], note=row.note if row else None,
                         updated_at=_utc(row.updated) if row else None, derived=derived)

    def _opencode(self, rec: ModelRecord, ctx: ContextResult, v: Judgement, kv_type: str, kv_note: str | None,
                  observed: list[ObservationRow], overheads: dict[str, BenchStatus]) -> OpencodeFit | None:
        if "embedding" in rec.capabilities and "completion" not in rec.capabilities:
            return None
        tools_run = next((b for b in self.bench_list() if b.digest == rec.digest and b.state == "done"
                          and b.result is not None and b.result.tools is not None), None)
        speed_run = next((b for b in self.bench_list() if b.digest == rec.digest and b.state == "done"
                          and b.result is not None and b.result.eval_tps is not None), None)
        at_min = None
        if ctx.effective < self.cfg.opencode_min_context:
            eff = self.effective()
            min_ctx = effective_context(rec, eff.context_length, eff.num_parallel, self.cfg,
                                        requested=self.cfg.opencode_min_context)
            at_min = (min_ctx, self._estimate(rec, min_ctx, kv_type, kv_note, observed, overheads)[1])
        return opencode_fit(
            rec.name, ctx, v, at_min, tools_run.result.tools if tools_run else None,
            (tools_run.finished_at or tools_run.created_at) if tools_run else None,
            speed_run.result.eval_tps if speed_run else None, self.cfg, self._verdict,
            tool_capability="tools" in rec.capabilities)

    def bench_list(self, name: str | None = None) -> list[BenchStatus]:
        running = [self.bench.status] if self.bench else []
        out = running + self.benches
        return [b for b in out if name is None or b.name == name]

    # ---- overview ---------------------------------------------------------------------------------

    async def _observations(self) -> list[ObservationRow]:
        try:
            return await self.repo.load_observations()
        except Exception:
            log.exception("catalog: observations unreadable")
            return []

    async def overview(self) -> CatalogOverview:
        return self._build(await self._observations())

    def _weights_of(self) -> dict[str, str]:
        """digest -> weights digest (or the digest itself), also for removed models: their measurements
        still calibrate models with the same weights."""
        return {r.record.digest: r.record.weights_digest or r.record.digest for r in self.rows.values()}

    def _overheads(self) -> dict[str, BenchStatus]:
        """weights digest -> newest test run on this GPU that measured what the runner holds beyond Ollama's
        count. Only test runs know it: they read the empty card right before loading."""
        hw = self.hardware.key
        weights = self._weights_of()
        out: dict[str, BenchStatus] = {}
        for b in self.bench_list():                     # newest first
            r = b.result
            if b.state == "done" and r is not None and r.runner_overhead_bytes is not None and r.hardware == hw:
                out.setdefault(weights.get(b.digest, b.digest), b)
        return out

    def _overhead_info(self, run: BenchStatus | None) -> OverheadInfo | None:
        if run is None or run.result is None or run.result.runner_overhead_bytes is None:
            return None
        budget = self.budget()
        measured = run.result.runner_overhead_bytes
        return OverheadInfo(measured_bytes=measured, reserve_bytes=budget.reserve_bytes,
                            extra_bytes=extra_beyond_reserve(measured, budget), model=run.name,
                            measured_at=run.finished_at or run.created_at)

    def _estimate(self, rec: ModelRecord, ctx: ContextResult, kv_type: str, kv_note: str | None,
                  observed: list[ObservationRow],
                  overheads: dict[str, BenchStatus] | None = None) -> tuple[Estimate, Judgement]:
        """Estimate (calibrated by measurements of the same weights on this GPU) and verdict for one context,
        plus what a test run of the same weights measured beyond Ollama's count."""
        hw = self.hardware.key or ""
        weights = self._weights_of()
        mine = rec.weights_digest or rec.digest
        points, seen = [], None
        for o in observed:
            if o.hardware != hw or weights.get(o.digest, o.digest) != mine or o.size_bytes <= 0:
                continue
            points.append(Point(num_ctx=o.num_ctx, size_bytes=o.size_bytes))
            if seen is None and o.digest == rec.digest and o.num_ctx == ctx.effective:
                seen = Seen(o.size_bytes, o.vram_bytes)
        est = estimate_vram(rec, ctx, kv_type, self.cfg, kv_note, points)
        run = (overheads if overheads is not None else self._overheads()).get(mine)
        budget = self.budget()
        extra = extra_beyond_reserve(run.result.runner_overhead_bytes, budget) if run and run.result else 0
        if "vision" in rec.capabilities and (est.calibrated or seen is not None):
            # the number is Ollama's count now: the encoder left it; a test run brings it back as "extra"
            est.notes = [n for n in est.notes if n != VISION_NOTE]
            if run is None:
                est.notes.append("Bild-Encoder: fehlt in dieser Zahl (Ollamas Zählung) – erst ein Testlauf zeigt, "
                                 "wie viel die Karte zusätzlich belegt")
        embedding = "embedding" in rec.capabilities and "completion" not in rec.capabilities
        return est, verdict(est, seen, budget, self.cfg, embedding=embedding, extra_bytes=extra)

    @staticmethod
    def _context_info(ctx: ContextResult) -> ContextInfo:
        return ContextInfo(effective=ctx.effective, source=ctx.source, own=ctx.own, server=ctx.server,
                           trained=ctx.trained, clamped=ctx.clamped, parallel=ctx.parallel)

    @staticmethod
    def _estimate_info(est: Estimate) -> VramEstimate:
        return VramEstimate(weights_bytes=est.weights_bytes, kv_bytes=est.kv_bytes, graph_bytes=est.graph_bytes,
                            formula_bytes=est.formula_bytes, need_bytes=est.need_bytes, calibrated=est.calibrated,
                            kv_type=est.kv_type, tokens=est.tokens, notes=est.notes, confidence=est.confidence)

    @staticmethod
    def _verdict(v: Judgement) -> Verdict:
        return Verdict(state=v.state, basis=v.basis, need_bytes=v.need_bytes, available_bytes=v.available_bytes,
                       extra_bytes=v.extra_bytes, message=v.message)

    def _build(self, observed: list[ObservationRow]) -> CatalogOverview:
        cfg = self.cfg
        rows = [r for r in self.rows.values() if r.removed_at is None]
        records = [r.record for r in rows]
        by_name = {r.name: r for r in records}
        links, groups = build_lineage(records)
        eff = self.effective()
        kv_type, kv_note = effective_kv_type(eff.kv_cache_type, eff.flash_attention)
        hw_key = self.hardware.key or ""
        obs_by_digest: dict[str, list[ObservationRow]] = defaultdict(list)
        for o in observed:
            obs_by_digest[o.digest].append(o)
        benches_by_name: dict[str, list[BenchStatus]] = defaultdict(list)
        for b in self.bench_list():
            if len(benches_by_name[b.name]) < BENCHES_PER_MODEL:
                benches_by_name[b.name].append(b)
        overheads = self._overheads()

        models = []
        for row in rows:
            rec = row.record
            ctx = effective_context(rec, eff.context_length, eff.num_parallel, cfg)
            est, v = self._estimate(rec, ctx, kv_type, kv_note, observed, overheads)
            obs = []
            for o in obs_by_digest.get(rec.digest, []):
                obs.append(Observation(
                    num_ctx=o.num_ctx or None, hardware=o.hardware or None, size_bytes=o.size_bytes,
                    vram_bytes=o.vram_bytes, placement=placement(o.size_bytes, o.vram_bytes),
                    gpu_ratio=round(min(o.vram_bytes / o.size_bytes, 1.0), 4) if o.size_bytes else None,
                    first_seen=_utc(o.first_seen), last_seen=_utc(o.last_seen), loads=o.loads,
                    current=o.num_ctx == ctx.effective and o.hardware == hw_key))
            link = links[rec.name]
            parent_rec = by_name.get(link.parent) if link.parent else None
            declared_installed = bool(rec.parent_model) and link.via == "declared"
            embedding = "embedding" in rec.capabilities and "completion" not in rec.capabilities
            models.append(CatalogModel(
                name=rec.name, digest=rec.digest, size_bytes=rec.size, modified_at=_parse_time(rec.modified_at),
                family=rec.family, parameter_size=rec.parameter_size, quantization=rec.quantization,
                format=rec.format, architecture=rec.architecture, capabilities=rec.capabilities,
                parameters=rec.parameters, system_chars=rec.system_chars, weights_digest=rec.weights_digest,
                parent=ParentInfo(declared=rec.parent_model, resolved=link.parent, via=link.via,
                                  installed=declared_installed),
                origin=link.origin, depth=link.depth,
                changes=changes(rec, parent_rec) if parent_rec else [],
                context=self._context_info(ctx), estimate=self._estimate_info(est), observations=obs,
                verdict=self._verdict(v), overhead=self._overhead_info(overheads.get(rec.weights_digest or rec.digest)),
                usage=self._usage_info(rec),
                opencode=self._opencode(rec, ctx, v, kv_type, kv_note, observed, overheads),
                benches=benches_by_name.get(rec.name, []), testable=not embedding,
                loaded=rec.name in self.loaded, first_seen=_utc(row.first_seen), show_error=rec.show_error,
            ))
        order = {name: i for i, name in enumerate(n for g in groups for n in g.members)}
        models.sort(key=lambda m: order.get(m.name, len(order)))
        measured = defaultdict(int)
        for o in observed:
            measured[o.digest] += 1
        removed = sorted((r for r in self.rows.values() if r.removed_at is not None),
                         key=lambda r: r.removed_at or 0, reverse=True)
        b = self.budget()
        return CatalogOverview(
            as_of=datetime.now(timezone.utc), revision=self.revision, refreshed_at=self.refreshed_at,
            ollama=self.ollama, server=self.server_config(), hardware=self.hardware,
            budget=BudgetInfo(total_bytes=b.total_bytes, other_bytes=b.other_bytes, other_source=b.other_source,
                              other_measured_at=_utc(self.other[1]) if self.other else None,
                              reserve_bytes=b.reserve_bytes, available_bytes=b.available_bytes),
            assumptions=Assumptions(graph_reserve_bytes=round(cfg.graph_reserve_gib * GIB),
                                    tight_ratio=cfg.tight_ratio, fallback_context_length=cfg.fallback_context_length,
                                    clamp_to_trained=cfg.clamp_to_trained,
                                    opencode_min_context=cfg.opencode_min_context),
            tests=self.bench_access(), bench=self.bench.status if self.bench else None,
            models=models,
            groups=[ModelGroup(origin=g.origin, installed=g.installed, members=g.members) for g in groups],
            removed=[RemovedModel(name=r.record.name, digest=r.record.digest, removed_at=_utc(r.removed_at or 0),
                                  measurements=measured.get(r.record.digest, 0)) for r in removed],
        )

    async def _publish(self) -> None:
        self.revision += 1
        if self.ctx.events.subscriber_count:            # nobody listening -> skip building it
            ov = await self.overview()
            self.ctx.events.publish(TOPIC, ov.model_dump(mode="json", by_alias=True))
