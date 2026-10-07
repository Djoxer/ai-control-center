"""Help pages: the core's HELP.md plus the HELP.md of every module that is switched on, and the changelog.

Each HELP.md lives next to the code it explains (core/HELP.md, modules/<key>/HELP.md), so a module
brings its manual along and a disabled module takes it away. Files are read per request: a fixed
typo shows up after a reload, no restart needed.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from fastapi import APIRouter, Request

import control_center
from control_center.core.context import AppContext, ModuleState
from control_center.core.schemas import CamelModel

router = APIRouter(prefix="/api/v1/help", tags=["help"])

CORE_HELP = Path(__file__).with_name("HELP.md")
# repo root: backend/src/control_center/__init__.py -> parents[3]. Holds for the source checkout the
# AI box runs from (uv installs the project editable); a non-editable install simply has no changelog.
CHANGELOG_PATH = Path(control_center.__file__).resolve().parents[3] / "CHANGELOG.md"

_H1 = re.compile(r"\A\s*#\s+(.+?)\s*(?:\n|\Z)")


class HelpDoc(CamelModel):
    key: str                                # "core" or the module key
    title: str                              # first "# " heading of the file
    module_state: ModuleState | None        # None for the core page
    markdown: str                           # the file without its first heading (the UI shows the title)


class HelpIndex(CamelModel):
    docs: list[HelpDoc]                     # core first, then modules in menu order


class Changelog(CamelModel):
    markdown: str | None                    # None = no CHANGELOG.md yet
    hint: str | None = None                 # why it is missing


def split_title(text: str, fallback: str) -> tuple[str, str]:
    """'# Logs\\n\\nText' -> ('Logs', 'Text'). No heading -> (fallback, text)."""
    m = _H1.match(text)
    if not m:
        return fallback, text.strip()
    return m.group(1), text[m.end():].strip()


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return None


def collect(ctx: AppContext) -> list[HelpDoc]:
    docs: list[HelpDoc] = []
    core = _read(CORE_HELP)
    if core is not None:
        title, body = split_title(core, "Allgemein")
        docs.append(HelpDoc(key="core", title=title, module_state=None, markdown=body))
    for m in sorted(ctx.modules.values(), key=lambda m: (m.order, m.key)):
        path = ctx.help_files.get(m.key)
        if m.state == "disabled" or path is None:
            continue                                    # switched off = its manual is switched off too
        text = _read(path)
        if text is None:
            continue                                    # module without HELP.md yet
        title, body = split_title(text, m.title)
        docs.append(HelpDoc(key=m.key, title=title, module_state=m.state, markdown=body))
    return docs


@router.get("", response_model=HelpIndex)
async def index(request: Request) -> HelpIndex:
    ctx: AppContext = request.app.state.ctx
    return HelpIndex(docs=await asyncio.to_thread(collect, ctx))


@router.get("/changelog", response_model=Changelog)
async def changelog() -> Changelog:
    text = await asyncio.to_thread(_read, CHANGELOG_PATH)
    if text is None:
        return Changelog(markdown=None,
                         hint="Noch kein CHANGELOG.md - im Repo-Ordner: uvx git-cliff -o CHANGELOG.md")
    return Changelog(markdown=text.strip())
