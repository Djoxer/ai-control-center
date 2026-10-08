"""Catalog service: keeps the inventory of installed models current and records what was observed.

One background loop, two clocks:
- every observe_interval_s (10 s): /api/ps -> which model runs with which context, how much VRAM
  (an upsert per model x context x GPU = the "measured" values),
- every refresh_interval_s (60 s): /api/tags, and /api/show for every model that is new or changed.

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
from control_center.adapters.ollama import InstalledModel, ModelDetails, OllamaModelMissing, OllamaUnavailable
from control_center.core.context import AppContext
from control_center.modules.catalog.collector import ModelRecord, ServerDefaults, read_server_config, record_from
from control_center.modules.catalog.estimate import (
    GIB, Seen, effective_context, effective_kv_type, estimate_vram, placement, verdict,
)
from control_center.modules.catalog.lineage import build_lineage, changes
from control_center.modules.catalog.repository import CatalogRepository, ModelRow, ObservationRow
from control_center.modules.catalog.schemas import (
    Assumptions, CatalogModel, CatalogOverview, ContextInfo, HardwareInfo, ModelGroup, Observation, OllamaState,
    ParentInfo, RemovedModel, ServerConfig, Verdict, VramEstimate,
)
from control_center.modules.catalog.settings import CatalogSettings

log = logging.getLogger("control_center.modules.catalog")

TOPIC = "catalog.overview"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
SHOW_PARALLEL = 4                   # /api/show reads only GGUF headers, a few at a time is fine
OVERRIDE_KEYS = ("server_context_length", "kv_cache_type", "flash_attention", "num_parallel")


class OllamaDown(Exception):
    """/api/tags failed. German message."""


class UnknownModel(KeyError):
    pass


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

    # ---- lifecycle --------------------------------------------------------------------------------

    async def start_up(self) -> None:
        await self.repo.create()
        try:
            self.rows = {r.record.name: r for r in await self.repo.load_models()}
        except Exception:
            log.exception("catalog: stored inventory unreadable - starting empty")
        await self._read_environment()
        self._task = asyncio.create_task(self._run(), name="catalog")

    async def shut_down(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
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
        """/api/ps -> one upsert per loaded model. A new load (or a new context) counts once."""
        try:
            running = await self.ctx.adapters.ollama.running()
        except OllamaUnavailable:
            if self.loaded:
                self.loaded = {}
                await self._publish()
            return
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

    # ---- overview ---------------------------------------------------------------------------------

    async def overview(self) -> CatalogOverview:
        try:
            observed = await self.repo.load_observations()
        except Exception:
            log.exception("catalog: observations unreadable")
            observed = []
        return self._build(observed)

    def _build(self, observed: list[ObservationRow]) -> CatalogOverview:
        cfg = self.cfg
        rows = [r for r in self.rows.values() if r.removed_at is None]
        records = [r.record for r in rows]
        by_name = {r.name: r for r in records}
        links, groups = build_lineage(records)
        eff = self.effective()
        kv_type, kv_note = effective_kv_type(eff.kv_cache_type, eff.flash_attention)
        hw_key = self.hardware.key or ""
        vram_total = self.hardware.vram_total_bytes
        obs_by_digest: dict[str, list[ObservationRow]] = defaultdict(list)
        for o in observed:
            obs_by_digest[o.digest].append(o)

        models = []
        for row in rows:
            rec = row.record
            ctx = effective_context(rec, eff.context_length, eff.num_parallel, cfg)
            est = estimate_vram(rec, ctx, kv_type, cfg, kv_note)
            seen = None
            obs = []
            for o in obs_by_digest.get(rec.digest, []):
                current = o.num_ctx == ctx.effective and o.hardware == hw_key
                if current and seen is None:
                    seen = Seen(o.size_bytes, o.vram_bytes)
                where = placement(o.size_bytes, o.vram_bytes)
                obs.append(Observation(
                    num_ctx=o.num_ctx or None, hardware=o.hardware or None, size_bytes=o.size_bytes,
                    vram_bytes=o.vram_bytes, placement=where,
                    gpu_ratio=round(min(o.vram_bytes / o.size_bytes, 1.0), 4) if o.size_bytes else None,
                    first_seen=_utc(o.first_seen), last_seen=_utc(o.last_seen), loads=o.loads, current=current))
            embedding = "embedding" in rec.capabilities and "completion" not in rec.capabilities
            v = verdict(est, seen, vram_total, cfg, embedding=embedding)
            link = links[rec.name]
            parent_rec = by_name.get(link.parent) if link.parent else None
            declared_installed = bool(rec.parent_model) and link.via == "declared"
            models.append(CatalogModel(
                name=rec.name, digest=rec.digest, size_bytes=rec.size, modified_at=_parse_time(rec.modified_at),
                family=rec.family, parameter_size=rec.parameter_size, quantization=rec.quantization,
                format=rec.format, architecture=rec.architecture, capabilities=rec.capabilities,
                parameters=rec.parameters, system_chars=rec.system_chars, weights_digest=rec.weights_digest,
                parent=ParentInfo(declared=rec.parent_model, resolved=link.parent, via=link.via,
                                  installed=declared_installed),
                origin=link.origin, depth=link.depth,
                changes=changes(rec, parent_rec) if parent_rec else [],
                context=ContextInfo(effective=ctx.effective, source=ctx.source, own=ctx.own, server=ctx.server,
                                    trained=ctx.trained, clamped=ctx.clamped, parallel=ctx.parallel),
                estimate=VramEstimate(weights_bytes=est.weights_bytes, kv_bytes=est.kv_bytes,
                                      graph_bytes=est.graph_bytes, driver_bytes=est.driver_bytes,
                                      total_bytes=est.total_bytes, kv_type=est.kv_type, tokens=est.tokens,
                                      notes=est.notes, confidence=est.confidence),
                observations=obs,
                verdict=Verdict(state=v.state, basis=v.basis, need_bytes=v.need_bytes, expected_bytes=v.expected_bytes,
                                vram_total_bytes=v.vram_total_bytes, message=v.message),
                loaded=rec.name in self.loaded, first_seen=_utc(row.first_seen), show_error=rec.show_error,
            ))
        order = {name: i for i, name in enumerate(n for g in groups for n in g.members)}
        models.sort(key=lambda m: order.get(m.name, len(order)))
        measured = defaultdict(int)
        for o in observed:
            measured[o.digest] += 1
        removed = sorted((r for r in self.rows.values() if r.removed_at is not None),
                         key=lambda r: r.removed_at or 0, reverse=True)
        return CatalogOverview(
            as_of=datetime.now(timezone.utc), revision=self.revision, refreshed_at=self.refreshed_at,
            ollama=self.ollama, server=self.server_config(),
            hardware=self.hardware,
            assumptions=Assumptions(graph_reserve_bytes=round(cfg.graph_reserve_gib * GIB),
                                    driver_overhead_bytes=round(cfg.driver_overhead_gib * GIB),
                                    tight_ratio=cfg.tight_ratio, fallback_context_length=cfg.fallback_context_length,
                                    clamp_to_trained=cfg.clamp_to_trained),
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
