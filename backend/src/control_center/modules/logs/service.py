"""Logs service: knows the configured sources, serves pages, and tails the newest file of each source."""
from __future__ import annotations

import asyncio
import glob
import logging
import os
from datetime import datetime, timezone

from control_center.core.context import AppContext
from control_center.core.tail import FileTail
from control_center.modules.logs.reader import EntryFilter, parse_line, read_page, resolve_files
from control_center.modules.logs.schemas import LogEntry, LogFileInfo, LogPage, LogSourceInfo
from control_center.modules.logs.settings import LogsSettings, LogSourceConfig

log = logging.getLogger("control_center.modules.logs")

OWN_SOURCE = "control-center"


class UnknownSource(KeyError):
    pass


class LogsService:
    def __init__(self, ctx: AppContext, cfg: LogsSettings) -> None:
        self.ctx = ctx
        self.cfg = cfg
        s = ctx.settings
        own = LogSourceConfig(
            key=OWN_SOURCE, title="AI Control Center", format="json",
            # escape the directory: '[' or '*' in a Windows user path would otherwise act as glob syntax
            paths=[os.path.join(glob.escape(str(s.log_dir)), glob.escape(s.log.file_name) + "*")],
        )
        extra = [src for src in cfg.sources if src.key != OWN_SOURCE]
        self.sources: dict[str, LogSourceConfig] = {src.key: src for src in [own, *extra]}
        self._tail: dict[str, FileTail] = {}
        self._failing: set[str] = set()          # log a broken source once, not every tick
        self._task: asyncio.Task | None = None

    # ---- read API -------------------------------------------------------------------------

    def _source(self, key: str) -> LogSourceConfig:
        try:
            return self.sources[key]
        except KeyError:
            raise UnknownSource(key) from None

    def _list_sources(self) -> list[LogSourceInfo]:
        out = []
        for src in self.sources.values():
            files = []
            for p in resolve_files(src.paths):
                st = p.stat()
                files.append(LogFileInfo(name=p.name, size_bytes=st.st_size,
                                         modified=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)))
            out.append(LogSourceInfo(key=src.key, title=src.title, format=src.format,
                                     available=bool(files), files=files))
        return out

    async def list_sources(self) -> list[LogSourceInfo]:
        return await asyncio.to_thread(self._list_sources)       # file system access off the event loop

    async def page(self, source: str, flt: EntryFilter, limit: int, cursor: str | None) -> LogPage:
        src = self._source(source)
        limit = max(1, min(limit, self.cfg.max_page_size))

        def work() -> LogPage:
            entries, nxt = read_page(resolve_files(src.paths), src.format, src.key, flt, limit, cursor)
            return LogPage(entries=entries, next_cursor=nxt)

        return await asyncio.to_thread(work)

    # ---- live tail ------------------------------------------------------------------------

    def start(self) -> None:
        for key, src in self.sources.items():
            tail = FileTail(src.paths)
            tail.attach(from_end=True)                               # no replay of old lines on startup
            self._tail[key] = tail
        self._task = asyncio.create_task(self._run(), name="logs-tail")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.tail_interval_s)
            for key, src in self.sources.items():
                tail = self._tail[key]
                try:
                    entries = await asyncio.to_thread(self._poll, src, tail)
                    if key in self._failing:
                        log.info("log source %s readable again", key)
                        self._failing.discard(key)
                except Exception:
                    if key not in self._failing:                     # once per outage, not every second
                        log.exception("log source %s cannot be tailed", key)
                        self._failing.add(key)
                    continue
                if entries and self.ctx.events.subscriber_count:
                    for e in entries:
                        self.ctx.events.publish(f"logs.{key}", e.model_dump(mode="json", by_alias=True))

    @staticmethod
    def _poll(src: LogSourceConfig, tail: FileTail) -> list[LogEntry]:
        """New complete lines of the newest file, parsed. Runs in a worker thread."""
        lines = tail.poll()
        name = tail.file.name if tail.file else ""
        return [parse_line(text, src.format, src.key, name) for text in lines]
