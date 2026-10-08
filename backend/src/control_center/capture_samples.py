"""Record a fake scenario from the live AI box. Run it ON the AI box (NVML and psutil see only the local machine).

    cd backend
    uv run python -m control_center.capture_samples real-normal
    uv run python -m control_center.capture_samples real-idle --note "nothing loaded"

The argument is a scenario NAME, not a path: files always go to
src/control_center/adapters/samples/<name>/, the only place the fake adapters read from.
Use a "real-" prefix for recordings. The synthetic folders (normal, offload, idle, ollama-down)
are test data with exact values - the tool refuses to overwrite them unless --force is given.

Writes into the scenario folder:
    ollama-ps.json, ollama-version.json   raw Ollama answers (missing when Ollama is down -> "ollama-down")
    ollama-tags.json                      installed models (raw /api/tags)
    ollama-show.json                      /api/show per installed model, REDUCED (see sanitize_show):
                                          no system prompts, no messages, no license texts, no file paths
    gpu.json, host.json, disks.json       adapter readings; command lines always "short" (or "off")
    meta.json                             capturedAt, so the fake can shift expiry times to "now"

Always uses the REAL adapters, whatever [adapters] says. Reads control-center.toml for
ai_host/ollama_url/gpu_index and [modules.dashboard] for process_names and disk_paths.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from control_center.adapters.common import SAMPLES_DIR, describe, to_jsonable
from control_center.adapters.gpu import NvmlGpu
from control_center.adapters.host import PsutilHost
from control_center.adapters.ollama import system_placeholder
from control_center.core.config import SCENARIO_NAME_PATTERN, Settings, load_settings
from control_center.modules.dashboard.settings import DashboardSettings

FILES = ("ollama-ps.json", "ollama-version.json", "ollama-tags.json", "ollama-show.json",
         "gpu.json", "host.json", "disks.json", "meta.json")
EXIT_USAGE = 2                                   # argparse uses 2 as well

# /api/show fields that go into a sample as they are. Everything else is dropped or reduced below:
# system prompts and MESSAGE lines can name customer projects, license texts are kilobytes of noise,
# and Modelfile FROM lines carry the Windows user folder.
SHOW_KEEP = ("details", "model_info", "capabilities", "parameters", "template", "modified_at", "projector_info")
_BLOB = re.compile(r"sha256[-:]([0-9a-fA-F]{64})")
_PATHLIKE = re.compile(r"^(?:[A-Za-z]:[\\/]|[/~.]|.*\\)")


def sanitize_modelfile(text: object) -> str | None:
    """Keep only FROM/ADAPTER (blob reference or model name) and PARAMETER lines of a Modelfile.

    "FROM C:\\Users\\x\\.ollama\\models\\blobs\\sha256-ab…" -> "FROM <blobs>/sha256-ab…"; a file path that is
    not a blob becomes "<path>"; a model name ("FROM qwen3:8b", "FROM hf.co/x/y:Q4") stays.
    """
    if not isinstance(text, str):
        return None
    out = ["# reduced by capture_samples: FROM/ADAPTER/PARAMETER lines only"]
    in_block = False                                   # inside a """…""" text (SYSTEM, TEMPLATE, LICENSE)
    for line in text.splitlines():
        starts_inside = in_block
        if line.count('"""') % 2:
            in_block = not in_block
        if starts_inside:
            continue                                   # "FROM now on, answer briefly" in a prompt is no FROM line
        head, _, rest = line.strip().partition(" ")
        word = head.upper()
        if word in ("FROM", "ADAPTER"):
            rest = rest.strip().strip('"')
            blob = _BLOB.search(rest)
            if blob:
                rest = f"<blobs>/sha256-{blob.group(1).lower()}"
            elif _PATHLIKE.match(rest):
                rest = "<path>"
            out.append(f"{word} {rest}")
        elif word == "PARAMETER":
            out.append(line.strip())
    return "\n".join(out) + "\n"


def sanitize_show(raw: object) -> dict:
    """/api/show answer -> what a sample may contain (see SHOW_KEEP). Keeps what the catalog parses."""
    if not isinstance(raw, dict):
        return {}
    out = {k: raw[k] for k in SHOW_KEEP if k in raw}
    lic = raw.get("license")
    if isinstance(lic, str) and lic.strip():
        out["license"] = lic.strip().splitlines()[0][:80] + " …"   # first line names the license
    system = raw.get("system")
    if isinstance(system, str) and system:
        out["system"] = system_placeholder(len(system))            # the length survives, the text does not
    out["modelfile"] = sanitize_modelfile(raw.get("modelfile"))
    return out


class ScenarioError(ValueError):
    """The scenario name cannot be used; the message says what to do instead."""


def _write(target: Path, name: str, data: Any) -> None:
    (target / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8", newline="\n")


def is_synthetic(folder: Path) -> bool:
    """Hand-made test scenario? Tests depend on its exact values, so a capture must not replace it."""
    meta = folder / "meta.json"
    if not meta.is_file():
        return False
    try:
        return json.loads(meta.read_text(encoding="utf-8")).get("source") == "synthetic"
    except (ValueError, AttributeError):
        return False                               # broken meta.json: nothing worth protecting


def resolve_target(name: str, samples_dir: Path, force: bool = False) -> Path:
    """Scenario name -> folder under samples_dir. Raises ScenarioError with a hint instead of guessing."""
    if "/" in name or "\\" in name:
        # the old call style took a path; a typo in it silently created a phantom folder tree
        raise ScenarioError(f"pass the scenario name only, e.g. 'real-normal' (files go to {samples_dir})")
    if not re.fullmatch(SCENARIO_NAME_PATTERN, name):
        raise ScenarioError(f"invalid scenario name '{name}': lowercase letters, digits and '-', "
                            "starting with a letter or digit")
    if not samples_dir.is_dir():
        raise ScenarioError(f"samples folder not found: {samples_dir}")
    target = samples_dir / name
    if is_synthetic(target) and not force:
        raise ScenarioError(f"'{name}' is synthetic test data (tests depend on its values). "
                            f"Record into a new name like 'real-{name}', or pass --force to replace it.")
    return target


async def capture(target: Path, settings: Settings, cpu_window_s: float = 1.0,
                  note: str = "") -> tuple[list[str], list[str]]:
    """Returns (written files, problems). Only our own file names in target are replaced."""
    cfg = settings.adapters
    dash = DashboardSettings.model_validate(settings.modules.section("dashboard"))
    cmdline = "off" if dash.process_cmdline == "off" else "short"     # never store full command lines
    target.mkdir(exist_ok=True)                   # one level only: a wrong parent is an error, not a new tree
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
        if "ollama-version.json" in written:             # Ollama answers: record the installed models too
            await _capture_models(client, base, target, written, problems)

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
                                 "note": note})
    written.append("meta.json")
    return written, problems


async def _capture_models(client: httpx.AsyncClient, base: str, target: Path,
                          written: list[str], problems: list[str]) -> None:
    """ollama-tags.json raw, ollama-show.json reduced. One failing /api/show only drops that model."""
    try:
        r = await client.get(base + "/api/tags")
        r.raise_for_status()
        tags = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        problems.append(f"ollama-tags.json: {describe(exc)}")
        return
    _write(target, "ollama-tags.json", tags)
    written.append("ollama-tags.json")
    names = [str(m.get("name") or m.get("model")) for m in (tags.get("models") or []) if isinstance(m, dict)]
    shows: dict[str, dict] = {}
    for name in names:
        try:
            r = await client.post(base + "/api/show", json={"model": name}, timeout=30)
            r.raise_for_status()
            shows[name] = sanitize_show(r.json())
        except (httpx.HTTPError, ValueError) as exc:
            problems.append(f"ollama-show.json {name}: {describe(exc)}")
    _write(target, "ollama-show.json", shows)
    written.append("ollama-show.json")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m control_center.capture_samples",
        description="Record the AI box's current state as a fake scenario under adapters/samples/<name>/.")
    p.add_argument("name", help="scenario name, e.g. real-normal (no path)")
    p.add_argument("--note", default="", help="free text stored in meta.json, e.g. 'only the coder model'")
    p.add_argument("--force", action="store_true", help="allow replacing a synthetic test scenario")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        target = resolve_target(args.name, SAMPLES_DIR, args.force)
    except ScenarioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    replacing = (target / "meta.json").is_file()
    written, problems = asyncio.run(capture(target, load_settings(), note=args.note))
    print(f"scenario {'replaced' if replacing else 'written'}: {target.resolve()}")
    for name in written:
        print(f"  ok       {name}")
    for p in problems:
        print(f"  missing  {p}")
    print("Check host.json (process names, command lines) and ollama-tags.json (model names) before committing.")
    return 0 if len(written) > 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
