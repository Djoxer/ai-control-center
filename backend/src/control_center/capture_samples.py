"""Record a fake scenario from the live AI box. Run it ON the AI box (NVML and psutil see only the local machine).

    cd backend
    uv run python -m control_center.capture_samples src/control_center/adapters/samples/normal

Writes into the target folder:
    ollama-ps.json, ollama-version.json   raw Ollama answers (missing when Ollama is down -> "ollama-down")
    gpu.json, host.json, disks.json       adapter readings; command lines always "short" (or "off")
    meta.json                             capturedAt, so the fake can shift expiry times to "now"

Always uses the REAL adapters, whatever [adapters] says. Reads control-center.toml for
ai_host/ollama_url/gpu_index and [modules.dashboard] for process_names and disk_paths.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from control_center.adapters.common import describe, to_jsonable
from control_center.adapters.gpu import NvmlGpu
from control_center.adapters.host import PsutilHost
from control_center.core.config import Settings, load_settings
from control_center.modules.dashboard.settings import DashboardSettings

FILES = ("ollama-ps.json", "ollama-version.json", "gpu.json", "host.json", "disks.json", "meta.json")


def _write(target: Path, name: str, data: Any) -> None:
    (target / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8", newline="\n")


async def capture(target: Path, settings: Settings, cpu_window_s: float = 1.0) -> tuple[list[str], list[str]]:
    """Returns (written files, problems). Only our own file names in target are replaced."""
    cfg = settings.adapters
    dash = DashboardSettings.model_validate(settings.modules.section("dashboard"))
    cmdline = "off" if dash.process_cmdline == "off" else "short"     # never store full command lines
    target.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        (target / name).unlink(missing_ok=True)       # a stale ollama-ps.json would turn "down" into "up"
    written: list[str] = []
    problems: list[str] = []

    base = cfg.expand(cfg.ollama_url).rstrip("/")
    async with httpx.AsyncClient(timeout=cfg.timeout_s) as client:
        for path, name in (("/api/ps", "ollama-ps.json"), ("/api/version", "ollama-version.json")):
            try:
                r = await client.get(base + path)
                r.raise_for_status()
                _write(target, name, r.json())
                written.append(name)
            except (httpx.HTTPError, ValueError) as exc:
                problems.append(f"{name}: {describe(exc)}")

    gpu = NvmlGpu(cfg.gpu_index)
    try:
        _write(target, "gpu.json", to_jsonable(gpu.read()))
        written.append("gpu.json")
    except Exception as exc:
        problems.append(f"gpu.json: {describe(exc)}")
    finally:
        gpu.close()

    host = PsutilHost()
    try:
        host.read(dash.process_names, cmdline)        # first read only primes the CPU counters
        await asyncio.sleep(cpu_window_s)
        _write(target, "host.json", to_jsonable(host.read(dash.process_names, cmdline)))
        written.append("host.json")
    except Exception as exc:
        problems.append(f"host.json: {describe(exc)}")
    try:
        _write(target, "disks.json", to_jsonable(host.disks(dash.disk_paths)))
        written.append("disks.json")
    except Exception as exc:
        problems.append(f"disks.json: {describe(exc)}")

    _write(target, "meta.json", {"capturedAt": datetime.now(timezone.utc).isoformat(), "source": "capture",
                                 "note": ""})
    written.append("meta.json")
    return written, problems


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    target = Path(sys.argv[1])
    written, problems = asyncio.run(capture(target, load_settings()))
    print(f"scenario written to {target.resolve()}")
    for name in written:
        print(f"  ok       {name}")
    for p in problems:
        print(f"  missing  {p}")
    print("Check host.json before committing (process names, command lines).")
    return 0 if len(written) > 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
