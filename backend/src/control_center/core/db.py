"""SQLite access via SQLAlchemy (async). Tables arrive with the first module that needs them."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.engine: AsyncEngine | None = None
        self.session: async_sessionmaker | None = None

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.path.as_posix()}")

        @event.listens_for(self.engine.sync_engine, "connect")
        def _pragmas(dbapi_conn, _record) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")    # sampler writes while the API reads
            cur.execute("PRAGMA foreign_keys=ON")     # SQLite ignores FKs unless told otherwise
            cur.execute("PRAGMA busy_timeout=5000")   # wait up to 5 s on a lock instead of failing
            cur.close()

        self.session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def ping(self) -> bool:
        if self.engine is None:
            return False
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()
            self.engine = None
