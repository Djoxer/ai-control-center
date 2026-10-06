"""History and event storage against a real SQLite file."""
import asyncio

import pytest

from control_center.core.db import Database
from control_center.modules.dashboard.repository import DashboardRepository

HOUR = 1_760_000_400.0                       # an hour boundary (divisible by 3600)


@pytest.fixture
def repo_run(tmp_path):
    """Run an async body against a fresh repository."""
    def run(body):
        async def go():
            db = Database(tmp_path / "t.db")
            await db.open()
            repo = DashboardRepository(db)
            await repo.create()
            try:
                return await body(repo)
            finally:
                await db.close()
        return asyncio.run(go())
    return run


async def fill(repo, start, count, step=10.0, **fixed):
    for i in range(count):
        await repo.add_sample(start + i * step, {"gpu_util": float(i % 10), "vram_used_mib": 1000.0 + i, **fixed})


def test_condense_minutes_with_average_and_peak(repo_run):
    async def body(repo):
        await fill(repo, HOUR, 18, temp_c=None)              # 3 minutes of 10 s samples
        await repo.condense(HOUR + 185)                      # minute 3 still running
        return await repo.history("minute", HOUR)
    rows = repo_run(body)
    # 3 complete minutes from the table + the running one filled on the fly
    assert [r.ts - HOUR for r in rows] == [0, 60, 120]
    assert rows[0].avg["gpu_util"] == 2.5 and rows[0].max["gpu_util"] == 5    # i = 0..5
    assert rows[0].max["vram_used_mib"] == 1005
    assert rows[0].avg["temp_c"] is None                      # a gap stays a gap


def test_condense_is_idempotent_and_catches_up(repo_run):
    async def body(repo):
        await fill(repo, HOUR, 12)
        await repo.condense(HOUR + 130)
        await repo.condense(HOUR + 130)                      # second run: nothing doubled
        await fill(repo, HOUR + 120, 12)                     # later samples after a pause
        await repo.condense(HOUR + 400)
        return await repo.history("minute", HOUR)
    rows = repo_run(body)
    assert [r.ts - HOUR for r in rows] == [0, 60, 120, 180]          # 2 + 2 minutes, nothing doubled


def test_hour_rows_and_open_hour_fill(repo_run):
    async def body(repo):
        await fill(repo, HOUR, 6 * 70)                       # 70 minutes
        await repo.condense(HOUR + 70 * 60)
        return await repo.history("hour", HOUR - 7 * 86400)
    rows = repo_run(body)
    assert [r.ts - HOUR for r in rows] == [0, 3600]          # complete hour + running hour from minutes
    assert rows[0].max["vram_used_mib"] == 1000 + 359


def test_prune_by_age(repo_run):
    async def body(repo):
        await fill(repo, HOUR, 6)
        await repo.condense(HOUR + 120)
        await repo.add_event(HOUR, "ollama_down", "critical", None, "weg")
        await repo.prune(HOUR + 2 * 86400, raw_h=24, minute_d=30, hour_d=365, events_d=1)
        return await repo.history("raw", 0), await repo.history("minute", 0), await repo.list_events(10)
    raw, minute, events = repo_run(body)
    assert raw == [] and len(minute) == 1 and events == []


def test_event_pages_newest_first(repo_run):
    async def body(repo):
        ids = [await repo.add_event(HOUR + i, "model_loaded", "info", f"m{i}", f"m{i} geladen") for i in range(5)]
        first = await repo.list_events(2)
        older = await repo.list_events(2, before_id=first[-1].id)
        return ids, first, older
    ids, first, older = repo_run(body)
    assert [e.id for e in first] == [ids[4], ids[3]]
    assert [e.subject for e in older] == ["m2", "m1"]
