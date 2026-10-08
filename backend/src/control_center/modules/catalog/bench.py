"""Test runs ("Testlauf"): load one model with a chosen context, let it answer a fixed prompt, measure.

Like a test drive with a measuring wheel:
1. clear the road   - unload what is loaded (OLLAMA_MAX_LOADED_MODELS=1 would do it anyway, but now the
                      empty card can be read: that is what other programs occupy),
2. drive            - /api/generate with the chosen num_ctx; Ollama reports load time and token speeds,
3. measure          - /api/ps: size, GPU share and the context Ollama really used; NVML: the card's view,
4. park             - unload again, so the GPU is as free as before.
One run at a time. The preflight (service) refuses runs whose estimate says "Teil-Offload" - that is the
crash case on this card - and wants a confirmation for "knapp".
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from control_center.adapters.ollama import (
    OllamaModelMissing,
    OllamaRequestFailed,
    OllamaUnavailable,
    RunningModel,
)
from control_center.modules.catalog.estimate import placement
from control_center.modules.catalog.schemas import BenchPhase, BenchResult, BenchStatus

if TYPE_CHECKING:
    from control_center.modules.catalog.service import CatalogService

log = logging.getLogger("control_center.modules.catalog.bench")

UNLOAD_WAIT_S = 20                  # Ollama frees the memory asynchronously; wait at most this long
SETTLE_S = 5                        # ... and the runner process a moment after /api/ps forgot it
SETTLE_STEP = 64 * 1024 ** 2        # two NVML readings this close = the card has settled
POLL_S = 0.5
SEED = 42                           # same answer every run -> comparable token counts


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rate(tokens: int | None, seconds: float | None) -> float | None:
    if not tokens or not seconds:
        return None
    return round(tokens / seconds, 1)


@dataclass
class BenchJob:
    status: BenchStatus
    digest: str
    task: asyncio.Task | None = None


def new_job(name: str, digest: str, num_ctx: int) -> BenchJob:
    now = _now()
    return BenchJob(status=BenchStatus(id=uuid.uuid4().hex[:12], as_of=now, name=name, digest=digest,
                                       num_ctx=num_ctx, state="queued", created_at=now), digest=digest)


async def _wait_until_unloaded(svc: CatalogService, names: set[str]) -> list[RunningModel]:
    """Poll /api/ps until none of `names` is loaded any more (or the time is up). Returns the last answer."""
    end = time.monotonic() + UNLOAD_WAIT_S
    running = await svc.ctx.adapters.ollama.running()
    while any(m.name in names for m in running) and time.monotonic() < end:
        await asyncio.sleep(POLL_S)
        running = await svc.ctx.adapters.ollama.running()
    return running


async def _settled_gpu(svc: CatalogService) -> int | None:
    """NVML after an unload: read until two readings POLL_S apart agree (at most SETTLE_S)."""
    end = time.monotonic() + SETTLE_S
    last = await svc.gpu_used()
    while last is not None and time.monotonic() < end:
        await asyncio.sleep(POLL_S)
        now = await svc.gpu_used()
        if now is None or abs(now - last) <= SETTLE_STEP:
            return now
        last = now
    return last


async def run_bench(svc: CatalogService, job: BenchJob) -> None:
    """The whole run. Every expected failure ends as state "failed" with a German error, never as an exception."""
    st = job.status
    ollama = svc.ctx.adapters.ollama
    cfg = svc.cfg
    result = BenchResult(requested_ctx=st.num_ctx, hardware=svc.hardware.key, kv_type=svc.kv_type(),
                         ollama_version=svc.ollama.version)
    st.state, st.started_at, st.result = "running", _now(), result

    async def phase(p: BenchPhase) -> None:
        st.phase = p
        await svc.publish_bench(job)

    try:
        # 1) clear the road
        await phase("unload")
        loaded = [m.name for m in await ollama.running()]
        for name in loaded:
            await ollama.unload(name)
        st.unloaded = loaded
        # a recorded scenario never unloads anything: do not wait for it
        running = [] if ollama.simulated else await _wait_until_unloaded(svc, set(loaded))

        # 2) the empty card = other programs (only meaningful when Ollama really holds nothing).
        #    A recorded scenario's card never changes: no memory values from it at all.
        await phase("baseline")
        before = None if ollama.simulated else await _settled_gpu(svc)
        result.gpu_before_bytes = before
        if before is not None and not running:
            await svc.record_other_usage(before)

        # 3) drive
        await phase("load")
        options = {"num_ctx": st.num_ctx, "num_predict": cfg.test_num_predict, "temperature": 0, "seed": SEED}
        gen = await ollama.generate(st.name, cfg.test_prompt, options, keep_alive="5m", timeout=cfg.test_timeout_s)
        result.load_s = round(gen.load_s, 2) if gen.load_s is not None else None
        result.total_s = round(gen.total_s, 2) if gen.total_s is not None else None
        result.prompt_tokens, result.eval_tokens = gen.prompt_tokens, gen.eval_tokens
        result.prompt_tps = _rate(gen.prompt_tokens, gen.prompt_s)
        result.eval_tps = _rate(gen.eval_tokens, gen.eval_s)

        # 4) measure
        await phase("measure")
        after = None if ollama.simulated else await svc.gpu_used()
        result.gpu_after_bytes = after
        mine = None if ollama.simulated else next((m for m in await ollama.running() if m.name == st.name), None)
        if ollama.simulated:
            result.note = "Simulation: Zeiten aus dem Fake-Adapter, keine Speicherwerte."
        elif mine is None:
            result.note = "Nach dem Antworten nicht in /api/ps gesehen – Speicherwerte fehlen."
        else:
            result.size_bytes, result.vram_bytes = mine.size, mine.size_vram
            result.actual_ctx = mine.context_length
            result.placement = placement(mine.size, mine.size_vram)
            if before is not None and after is not None:
                result.runner_overhead_bytes = after - before - mine.size_vram
            if mine.size > 0:
                await svc.record_observation(mine, new_load=True)
            if result.actual_ctx and result.actual_ctx != st.num_ctx:
                result.note = (f"Angefragt {st.num_ctx:,} Token, geladen mit {result.actual_ctx:,} – "
                               f"Ollama hat den Kontext angepasst.").replace(",", ".")

        # 5) park
        if cfg.unload_after_test:
            await phase("cleanup")
            await ollama.unload(st.name)
        st.state = "done"
    except OllamaRequestFailed as exc:
        st.state, st.error = "failed", f"Ollama meldet einen Fehler: {exc}"
    except OllamaModelMissing:
        st.state, st.error = "failed", "Ollama kennt das Modell nicht (gerade gelöscht?)."
    except OllamaUnavailable as exc:
        st.state, st.error = "failed", f"Ollama nicht erreichbar: {exc}"
    except asyncio.CancelledError:
        st.state, st.error = "cancelled", "Abgebrochen (Control Center wurde beendet)."
        raise
    except Exception:
        log.exception("test run %s crashed", st.id)
        st.state, st.error = "failed", "Interner Fehler – Details im Protokoll."
    finally:
        st.phase = None
        st.finished_at = _now()
        log.info("test run %s %s: %s ctx %s -> %s", st.id, st.state, st.name, st.num_ctx,
                 st.error or f"{result.eval_tps} tok/s")
        await svc.finish_bench(job)
