"""SQLite storage of the catalog: installed models, what was observed while they ran, test runs.

Five tables, deliberately plain:
- catalog_models: one row per model name. Key columns for queries, the rest as JSON in `data`
  (ModelRecord). New fields in the record need no migration - older rows just lack them.
- catalog_observations: one row per model digest x context length x GPU. "Measured" in the catalog
  means: Ollama reported this size and VRAM share in /api/ps while the model was loaded.
- catalog_benches: one row per test run, the full report as JSON (like the models).
- catalog_state: small key/value facts that must survive a restart (VRAM of other programs).
- catalog_usage: what the team uses a model for (tags + note), by NAME - clients address models by name,
  and a re-pulled model (new digest) keeps its job.

Removed models keep their row (removed_at set) for keep_removed_days - their measurements stay
visible and come back if the model is pulled again.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import (
    Column, Float, Integer, MetaData, String, Table, Text, UniqueConstraint, delete, select, update,
)
from sqlalchemy.dialects.sqlite import insert

from control_center.core.db import Database
from control_center.modules.catalog.collector import ModelRecord

metadata = MetaData()

models = Table(
    "catalog_models", metadata,
    Column("name", String, primary_key=True),
    Column("digest", String, nullable=False),
    Column("first_seen", Float, nullable=False),        # unix seconds UTC
    Column("last_seen", Float, nullable=False),
    Column("removed_at", Float),                        # NULL = installed
    Column("data", Text, nullable=False),               # ModelRecord as JSON
)

observations = Table(
    "catalog_observations", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("digest", String, nullable=False),
    Column("name", String, nullable=False),             # name at the time (a digest can have several)
    Column("num_ctx", Integer, nullable=False),         # 0 = Ollama did not report it
    Column("hardware", String, nullable=False),         # GPU profile, "" = unknown (remote Ollama)
    Column("size_bytes", Integer, nullable=False),
    Column("vram_bytes", Integer, nullable=False),
    Column("first_seen", Float, nullable=False),
    Column("last_seen", Float, nullable=False),
    Column("loads", Integer, nullable=False),           # how often it was seen being loaded this way
    UniqueConstraint("digest", "num_ctx", "hardware", name="uq_catalog_observation"),
)


benches = Table(
    "catalog_benches", metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("digest", String, nullable=False),
    Column("created", Float, nullable=False, index=True),
    Column("data", Text, nullable=False),               # BenchStatus as JSON
)

state = Table(
    "catalog_state", metadata,
    Column("key", String, primary_key=True),
    Column("value", Text, nullable=False),              # JSON
    Column("updated", Float, nullable=False),
)


usage = Table(
    "catalog_usage", metadata,
    Column("name", String, primary_key=True),
    Column("tags", Text, nullable=False),              # JSON list of tag keys
    Column("note", Text),
    Column("updated", Float, nullable=False),
)


@dataclass(frozen=True)
class UsageRow:
    name: str
    tags: list[str]
    note: str | None
    updated: float


@dataclass(frozen=True)
class ModelRow:
    record: ModelRecord
    first_seen: float
    last_seen: float
    removed_at: float | None


@dataclass(frozen=True)
class ObservationRow:
    digest: str
    name: str
    num_ctx: int
    hardware: str
    size_bytes: int
    vram_bytes: int
    first_seen: float
    last_seen: float
    loads: int


class CatalogRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    @property
    def _engine(self):
        if self.db.engine is None:
            raise RuntimeError("database not open")
        return self.db.engine

    async def create(self) -> None:
        """Create missing tables. Existing ones are left alone (no migrations needed so far)."""
        async with self._engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

    # ---- models -----------------------------------------------------------------------------

    async def load_models(self) -> list[ModelRow]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(select(models))).mappings().all()
        out = []
        for r in rows:
            try:
                rec = ModelRecord.from_json(json.loads(r["data"]))
            except (ValueError, TypeError):
                continue                                  # unreadable row: the next refresh rewrites it
            out.append(ModelRow(rec, r["first_seen"], r["last_seen"], r["removed_at"]))
        return out

    async def save_models(self, records: list[ModelRecord], now: float) -> None:
        """Insert or update; an update keeps first_seen and clears removed_at (pulled again)."""
        if not records:
            return
        rows = [{"name": r.name, "digest": r.digest, "first_seen": now, "last_seen": now, "removed_at": None,
                 "data": json.dumps(r.to_json(), ensure_ascii=False)} for r in records]
        stmt = insert(models)
        stmt = stmt.on_conflict_do_update(index_elements=[models.c.name], set_={
            "digest": stmt.excluded.digest, "last_seen": stmt.excluded.last_seen, "removed_at": None,
            "data": stmt.excluded.data})
        async with self._engine.begin() as conn:
            await conn.execute(stmt, rows)

    async def touch(self, names: list[str], now: float) -> None:
        if names:
            async with self._engine.begin() as conn:
                await conn.execute(update(models).where(models.c.name.in_(names)).values(last_seen=now))

    async def mark_removed(self, names: list[str], now: float) -> None:
        if names:
            async with self._engine.begin() as conn:
                await conn.execute(update(models).where(models.c.name.in_(names) & models.c.removed_at.is_(None))
                                   .values(removed_at=now))

    async def prune(self, now: float, keep_days: int) -> None:
        """Forget models removed longer ago than keep_days, and observations nobody refers to any more."""
        limit = now - keep_days * 86400
        async with self._engine.begin() as conn:
            await conn.execute(delete(models).where(models.c.removed_at.is_not(None) & (models.c.removed_at < limit)))
            known = select(models.c.digest)
            await conn.execute(delete(observations).where(observations.c.digest.not_in(known)
                                                          & (observations.c.last_seen < limit)))

    # ---- observations -----------------------------------------------------------------------

    async def observe(self, digest: str, name: str, num_ctx: int, hardware: str, size_bytes: int,
                      vram_bytes: int, now: float, new_load: bool) -> None:
        """Upsert one sighting. new_load=True counts a load (the model was not loaded this way before)."""
        stmt = insert(observations).values(
            digest=digest, name=name, num_ctx=num_ctx, hardware=hardware, size_bytes=size_bytes,
            vram_bytes=vram_bytes, first_seen=now, last_seen=now, loads=1)
        stmt = stmt.on_conflict_do_update(
            index_elements=[observations.c.digest, observations.c.num_ctx, observations.c.hardware],
            set_={"name": stmt.excluded.name, "size_bytes": stmt.excluded.size_bytes,
                  "vram_bytes": stmt.excluded.vram_bytes, "last_seen": stmt.excluded.last_seen,
                  "loads": observations.c.loads + (1 if new_load else 0)})
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def load_observations(self) -> list[ObservationRow]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(select(observations).order_by(observations.c.last_seen.desc()))).mappings()
            return [ObservationRow(**{k: r[k] for k in ObservationRow.__dataclass_fields__}) for r in rows]

    # ---- test runs ----------------------------------------------------------------------------

    async def save_bench(self, bench_id: str, name: str, digest: str, created: float, data: dict) -> None:
        stmt = insert(benches).values(id=bench_id, name=name, digest=digest, created=created,
                                      data=json.dumps(data, ensure_ascii=False))
        stmt = stmt.on_conflict_do_update(index_elements=[benches.c.id], set_={"data": stmt.excluded.data})
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def load_benches(self, limit: int) -> list[dict]:
        """Newest first; unreadable rows are skipped."""
        async with self._engine.connect() as conn:
            rows = (await conn.execute(select(benches.c.data).order_by(benches.c.created.desc()).limit(limit))).all()
        out = []
        for (data,) in rows:
            try:
                out.append(json.loads(data))
            except ValueError:
                continue
        return out

    async def prune_benches(self, keep: int) -> None:
        async with self._engine.begin() as conn:
            newest = select(benches.c.id).order_by(benches.c.created.desc()).limit(keep)
            await conn.execute(delete(benches).where(benches.c.id.not_in(newest)))

    # ---- usage -------------------------------------------------------------------------------

    async def load_usage(self) -> dict[str, UsageRow]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(select(usage))).mappings().all()
        out = {}
        for r in rows:
            try:
                tags = [t for t in json.loads(r["tags"]) if isinstance(t, str)]
            except (ValueError, TypeError):
                tags = []
            out[r["name"]] = UsageRow(r["name"], tags, r["note"], r["updated"])
        return out

    async def save_usage(self, name: str, tags: list[str], note: str | None, now: float) -> None:
        """No tags and no note = forget the row."""
        async with self._engine.begin() as conn:
            if not tags and not note:
                await conn.execute(delete(usage).where(usage.c.name == name))
                return
            stmt = insert(usage).values(name=name, tags=json.dumps(tags), note=note, updated=now)
            stmt = stmt.on_conflict_do_update(index_elements=[usage.c.name], set_={
                "tags": stmt.excluded.tags, "note": stmt.excluded.note, "updated": stmt.excluded.updated})
            await conn.execute(stmt)

    # ---- small facts --------------------------------------------------------------------------

    async def get_state(self, key: str) -> tuple[object, float] | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(select(state.c.value, state.c.updated).where(state.c.key == key))).first()
        if row is None:
            return None
        try:
            return json.loads(row[0]), row[1]
        except ValueError:
            return None

    async def set_state(self, key: str, value: object, now: float) -> None:
        stmt = insert(state).values(key=key, value=json.dumps(value), updated=now)
        stmt = stmt.on_conflict_do_update(index_elements=[state.c.key],
                                          set_={"value": stmt.excluded.value, "updated": stmt.excluded.updated})
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
