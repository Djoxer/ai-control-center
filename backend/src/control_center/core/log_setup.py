"""Logging: readable lines on the console, JSON lines in a rotating file.

JSON lines make the later logs module trivial: filter by level, logger (= module) and time
without regex-parsing free text.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from control_center.core.config import Settings

ROOT_LOGGER = "control_center"   # modules log as control_center.modules.<key>


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)   # full traceback, one JSON field
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(settings: Settings) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    level = logging.getLevelName(settings.log.level.upper())

    file_handler = RotatingFileHandler(
        settings.log_dir / settings.log.file_name,
        maxBytes=settings.log.max_bytes,
        backupCount=settings.log.backup_count,
        encoding="utf-8",                                  # Windows default would be cp1252
    )
    file_handler.setFormatter(JsonLineFormatter())
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))

    # our own loggers plus uvicorn's, so access/errors land in the same searchable file
    for name in (ROOT_LOGGER, "uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        for old in list(logger.handlers):
            # close, not just detach: on Windows an open log file cannot be deleted or rotated
            logger.removeHandler(old)
            old.close()
        logger.addHandler(file_handler)
        logger.addHandler(console)
        logger.setLevel(level)
        logger.propagate = False                           # avoid duplicate lines via the root logger
