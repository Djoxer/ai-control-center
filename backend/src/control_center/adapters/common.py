"""Small helpers shared by all adapters."""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SAMPLES_DIR = Path(__file__).parent / "samples"     # built-in fake scenarios, one folder each


def describe(exc: BaseException) -> str:
    """Short, log-friendly text. Some errors (httpx timeouts) have an empty str(); the class name always helps."""
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def to_jsonable(value: Any) -> Any:
    """Dataclass readings -> JSON-ready dicts (datetimes as ISO strings). Used by capture_samples."""
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
