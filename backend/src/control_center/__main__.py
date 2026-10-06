"""Entry point: python -m control_center"""
from __future__ import annotations

import asyncio

import uvicorn

from control_center.core.config import load_settings
from control_center.core.events import close_all_buses


class Server(uvicorn.Server):
    """uvicorn waits for all connections to finish before it shuts down - SSE streams never do.

    On Ctrl+C we first tell every event bus to close, so open streams end within milliseconds.
    """

    def handle_exit(self, sig, frame) -> None:
        try:
            # signal handlers interrupt the loop thread; schedule the close as a regular callback
            asyncio.get_running_loop().call_soon_threadsafe(close_all_buses)
        except RuntimeError:
            pass                                       # no running loop yet/anymore: nothing to close
        super().handle_exit(sig, frame)


def main() -> None:
    settings = load_settings()
    config = uvicorn.Config(
        "control_center.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        workers=1,          # MUST stay 1: sampler, event bus and SQLite writer live in this one process
        log_config=None,    # keep uvicorn from overwriting our logging setup
        timeout_graceful_shutdown=5,   # safety net if a connection still hangs
    )
    try:
        Server(config).run()
    except KeyboardInterrupt:
        pass                # uvicorn re-raises Ctrl+C after a clean shutdown; nothing left to do


if __name__ == "__main__":
    main()
