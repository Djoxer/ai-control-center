"""Logs service: knows the configured sources, serves pages, and tails the newest file of each source."""
from __future__ import annotations

import asyncio
import glob
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from control_center.core.context import AppContext
from control_center.modules.logs.reader import EntryFilter, parse_line, read_page, resolve_files
from control_center.modules.logs.schemas import LogEntry, LogFileInfo, LogPage, LogSourceInfo
from control_center.modules.logs.settings import LogsSettings, LogSourceConfig

log = logging.getLogger("control_center.modules.logs")

OWN_SOURCE = "control-center"
MAX_TAIL_BYTES = 1024 * 1024        # per source and tick: a burst is delivered over several ticks


class UnknownSource(KeyError):
    pass


@dataclass
class TailState:
    file: Path | None = None
    file_id: tuple[int, int] | None = None   # (device, inode): changes when a rotation creates a new file
    offset: int = 0
    failing: bool = False                    # log a broken source once, not every tick


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
        self._tail: dict[str, TailState] = {}
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
            state = TailState()
            self._attach_to_newest(src, state, from_end=True)       # no replay of old lines on startup
            self._tail[key] = state
        self._task = asyncio.create_task(self._run(), name="logs-tail")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _attach_to_newest(self, src: LogSourceConfig, state: TailState, from_end: bool) -> None:
        files = resolve_files(src.paths)
        if not files:
            state.file, state.file_id, state.offset = None, None, 0
            return
        st = files[0].stat()
        state.file, state.file_id = files[0], (st.st_dev, st.st_ino)
        state.offset = st.st_size if from_end else 0

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.tail_interval_s)
            for key, src in self.sources.items():
                state = self._tail[key]
                try:
                    entries = await asyncio.to_thread(self._poll, src, state)
                    if state.failing:
                        log.info("log source %s readable again", key)
                        state.failing = False
                except Exception:
                    if not state.failing:                            # once per outage, not every second
                        log.exception("log source %s cannot be tailed", key)
                        state.failing = True
                    continue
                if entries and self.ctx.events.subscriber_count:
                    for e in entries:
                        self.ctx.events.publish(f"logs.{key}", e.model_dump(mode="json", by_alias=True))

    def _poll(self, src: LogSourceConfig, state: TailState) -> list[LogEntry]:
        """Read complete new lines of the newest file. Runs in a worker thread."""
        files = resolve_files(src.paths)
        if not files:
            state.file, state.file_id, state.offset = None, None, 0
            return []
        newest = files[0]
        st = newest.stat()
        fid = (st.st_dev, st.st_ino)
        if state.file is None or fid != state.file_id or st.st_size < state.offset:
            # new file (first appearance or rotation) or truncated: read it from the start
            state.file, state.file_id, state.offset = newest, fid, 0
        if st.st_size == state.offset:
            return []
        with newest.open("rb") as f:                            # open, read, close: rotation must not be blocked
            f.seek(state.offset)
            data = f.read(min(st.st_size - state.offset, MAX_TAIL_BYTES))
        last_nl = data.rfind(b"\n")
        if last_nl < 0:
            return []                                           # no complete line yet
        complete = data[: last_nl + 1]
        state.offset += len(complete)
        out = []
        for raw in complete.split(b"\n"):
            text = raw.rstrip(b"\r").decode("utf-8", errors="replace")
            if text.strip():
                out.append(parse_line(text, src.format, src.key, newest.name))
        return out
