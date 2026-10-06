import asyncio
import os

from control_center.core.config import Settings
from control_center.core.context import AppContext
from control_center.core.db import Database
from control_center.core.events import EventBus
from control_center.modules.logs.service import LogsService
from control_center.modules.logs.settings import LogsSettings, LogSourceConfig


def make_service(tmp_path) -> tuple[LogsService, EventBus]:
    settings = Settings(data_dir=tmp_path / "data")
    bus = EventBus()
    ctx = AppContext(settings=settings, db=Database(tmp_path / "x.db"), events=bus)
    cfg = LogsSettings(tail_interval_s=0.01, sources=[
        LogSourceConfig(key="app", title="App", format="text", paths=[str(tmp_path / "app.log*")]),
    ])
    return LogsService(ctx, cfg), bus


async def drain(q, n, timeout=2.0):
    out = []
    while len(out) < n:
        out.append(await asyncio.wait_for(q.get(), timeout))
    return out


def test_tail_publishes_new_complete_lines_only(tmp_path):
    log = tmp_path / "app.log"
    log.write_text("old line\n", encoding="utf-8")       # existing content is NOT replayed

    async def run():
        svc, bus = make_service(tmp_path)
        with bus.subscription() as q:
            svc.start()
            with log.open("a", encoding="utf-8") as f:
                f.write("first\nsecond-half")
            events = await drain(q, 1)
            with log.open("a", encoding="utf-8") as f:
                f.write(" done\n")
            events += await drain(q, 1)
            await svc.stop()
        return events

    events = asyncio.run(run())
    assert [e.topic for e in events] == ["logs.app", "logs.app"]
    assert [e.data["msg"] for e in events] == ["first", "second-half done"]


def test_tail_follows_rotation(tmp_path):
    log = tmp_path / "app.log"
    log.write_text("before\n", encoding="utf-8")

    async def run():
        svc, bus = make_service(tmp_path)
        with bus.subscription() as q:
            svc.start()
            await asyncio.sleep(0.05)
            os.replace(log, tmp_path / "app.log.1")          # what RotatingFileHandler does
            os.utime(tmp_path / "app.log.1", (1_000, 1_000))
            log.write_text("after rotation\n", encoding="utf-8")
            events = await drain(q, 1)
            await svc.stop()
        return events

    assert [e.data["msg"] for e in asyncio.run(run())] == ["after rotation"]
