"""SQLite storage of the dashboard: metric samples (raw, minute, hour) and events.

Samples use a wide table: one row per moment, every metric a column, plus a *_max column for the
peak within a condensed bucket. A chart query is a single range scan on the (res, ts) primary key.

Think of it as a logbook with three handwritings: every 10 s a pencil note (raw, kept 24 h),
once a minute a clean copy of the last minute (kept 30 days), once an hour a summary of the last
hour (kept a year). The average says "how busy", the max says "how close to the edge" - for VRAM
the max is the number that matters.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import (
    Column, Float, Integer, MetaData, String, Table, Text, cast, delete, func, insert, literal, select,
)
from sqlalchemy.sql import Select

from control_center.core.db import Database

Resolution = Literal["raw", "minute", "hour"]
METRICS = ("gpu_util", "vram_used_mib", "temp_c", "power_w", "cpu_percent", "ram_percent")
STEP_S = {"minute": 60, "hour": 3600}
SOURCE_OF = {"minute": "raw", "hour": "minute"}           # what each level is condensed from

metadata = MetaData()

samples = Table(
    "dashboard_samples", metadata,
    Column("res", String(6), primary_key=True),          # raw | minute | hour
    Column("ts", Float, primary_key=True),               # unix seconds UTC; bucket start for minute/hour
    *[Column(m, Float) for m in METRICS],                # value (raw) or average (condensed)
    *[Column(f"{m}_max", Float) for m in METRICS],       # peak within the bucket; NULL for raw rows
)

events = Table(
    "dashboard_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", Float, nullable=False, index=True),
    Column("kind", String(32), nullable=False),
    Column("level", String(8), nullable=False),
    Column("subject", Text),
    Column("message", Text, nullable=False),
)


@dataclass(frozen=True)
class SampleRow:
    ts: float
    avg: dict[str, float | None]
    max: dict[str, float | None]


@dataclass(frozen=True)
class EventRow:
    id: int
    ts: float
    kind: str
    level: str
    subject: str | None
    message: str


def _condense(res: Literal["minute", "hour"], start: float, end: float | None = None) -> Select:
    """SELECT of buckets of `res` built from the finer level, for start <= ts < end."""
    step = STEP_S[res]
    src = samples.c
    bucket = (cast(src.ts / step, Integer) * step).label("ts")
    cond = (src.res == SOURCE_OF[res]) & (src.ts >= start)
    if end is not None:
        cond = cond & (src.ts < end)
    cols = [literal(res).label("res"), bucket]
    cols += [func.avg(src[m]).label(m) for m in METRICS]
    # raw rows have no *_max: their value is the peak
    cols += [func.max(func.coalesce(src[f"{m}_max"], src[m])).label(f"{m}_max") for m in METRICS]
    return select(*cols).where(cond).group_by(bucket)


class DashboardRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    @property
    def _engine(self):
        if self.db.engine is None:
            raise RuntimeError("database not open")
        return self.db.engine

    async def create(self) -> None:
        """Create missing tables. Existing ones are left alone (schema changes need a migration later)."""
        async with self._engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

    # ---- samples ----------------------------------------------------------------------------

    async def add_sample(self, ts: float, values: dict[str, float | None]) -> None:
        row = {"res": "raw", "ts": ts, **{m: values.get(m) for m in METRICS}}
        async with self._engine.begin() as conn:
            await conn.execute(insert(samples).prefix_with("OR REPLACE"), row)

    async def condense(self, now: float) -> None:
        """Build minute rows from raw and hour rows from minute rows - complete buckets only.

        Starts after the newest existing bucket, so a restart after a pause catches up by itself.
        OR REPLACE makes a repeated run harmless.
        """
        async with self._engine.begin() as conn:
            for res in ("minute", "hour"):
                step = STEP_S[res]
                last = (await conn.execute(select(func.max(samples.c.ts)).where(samples.c.res == res))).scalar()
                start = 0.0 if last is None else last + step
                end = math.floor(now / step) * step               # the running bucket is not complete yet
                if end <= start:
                    continue
                query = _condense(res, start, end)
                names = ["res", "ts", *METRICS, *[f"{m}_max" for m in METRICS]]
                await conn.execute(insert(samples).prefix_with("OR REPLACE").from_select(names, query))

    async def prune(self, now: float, raw_h: float, minute_d: float, hour_d: float, events_d: float) -> None:
        limits = {"raw": raw_h * 3600, "minute": minute_d * 86400, "hour": hour_d * 86400}
        async with self._engine.begin() as conn:
            for res, age in limits.items():
                await conn.execute(delete(samples).where((samples.c.res == res) & (samples.c.ts < now - age)))
            await conn.execute(delete(events).where(events.c.ts < now - events_d * 86400))

    async def history(self, res: Resolution, since: float) -> list[SampleRow]:
        """Rows of one resolution since `since`, oldest first.

        Condensed levels lag behind (the running minute/hour is not written yet), so the open end is
        filled on the fly from the finer level - the 7-day chart shows the current hour too.
        """
        async with self._engine.connect() as conn:
            rows = list((await conn.execute(
                select(samples).where((samples.c.res == res) & (samples.c.ts >= since)).order_by(samples.c.ts)
            )).mappings())
            if res != "raw":
                step = STEP_S[res]
                tail_from = rows[-1]["ts"] + step if rows else math.floor(since / step) * step
                rows += list((await conn.execute(_condense(res, tail_from).order_by("ts"))).mappings())
        return [SampleRow(ts=r["ts"], avg={m: r[m] for m in METRICS},
                          max={m: r[f"{m}_max"] for m in METRICS}) for r in rows]

    # ---- events -----------------------------------------------------------------------------

    async def add_event(self, ts: float, kind: str, level: str, subject: str | None, message: str) -> int:
        async with self._engine.begin() as conn:
            result = await conn.execute(insert(events).values(
                ts=ts, kind=kind, level=level, subject=subject, message=message))
            return int(result.inserted_primary_key[0])

    async def list_events(self, limit: int, before_id: int | None = None) -> list[EventRow]:
        """Newest first. before_id = id of the oldest event already shown (cursor for "load older")."""
        query = select(events).order_by(events.c.id.desc()).limit(limit)
        if before_id is not None:
            query = query.where(events.c.id < before_id)
        async with self._engine.connect() as conn:
            return [EventRow(**r) for r in (await conn.execute(query)).mappings()]
