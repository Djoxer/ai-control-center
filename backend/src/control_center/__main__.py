"""Entry point: python -m control_center"""
from __future__ import annotations

import uvicorn

from control_center.core.config import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        "control_center.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        workers=1,          # MUST stay 1: sampler, event bus and SQLite writer live in this one process
        log_config=None,    # keep uvicorn from overwriting our logging setup
    )


if __name__ == "__main__":
    main()
