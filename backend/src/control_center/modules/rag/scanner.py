"""Which files of a source get indexed, and what text goes into the vector store.

Selection and reading mirror the old index scripts line by line, so the search quality stays the
same and can be measured later against exactly this state:

    os.walk(<repo>/<include dir>)          sub-folders in exclude_dirs pruned (the include dir itself is not)
    fname in exclude_names -> skip         exact name
    fname.endswith(ext) else skip          case-sensitive, '.ts' also takes 'x.spec.ts' and 'x.d.ts'
    open(encoding="utf-8", errors="ignore").read()[:max_chars]     universal newlines: \\r\\n -> \\n
    text.strip() == "" -> skip ("empty")

Differences, all without effect on what is embedded:
- the relative path uses "/" and has no leading separator ("src/Domain/Note.php" instead of
  "\\src\\Domain\\Note.php") - the same on Windows and Linux, so point IDs match on every machine
- a file listed by two overlapping include dirs is taken once (the scripts stored it twice)
- order is sorted (stable progress display); the scripts used the file system's order
- only max_chars + 1 characters are read: enough to know whether the file was cut
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from control_center.modules.rag.settings import SourceConfig


class SourceError(Exception):
    """The source as a whole cannot be indexed. German message, shown as-is."""


@dataclass(frozen=True)
class Candidate:
    rel: str                # "src/Domain/Note/Note.php" - payload "filename" and part of the point ID
    path: Path
    size: int               # bytes on disk


@dataclass
class ScanResult:
    root: Path
    files: list[Candidate] = field(default_factory=list)
    missing_dirs: list[str] = field(default_factory=list)   # include dirs that do not exist (scripts: "UEBERSPRUNGEN")


@dataclass(frozen=True)
class FileText:
    text: str               # what goes into the payload and the embedding
    truncated: bool         # the file had more than max_chars characters


def resolve_root(source: SourceConfig, base: Path) -> Path:
    """%USERPROFILE%/rag-setup/repos/bent/bent-php-api -> absolute path. Relative = next to the toml."""
    expanded = Path(os.path.expandvars(os.path.expanduser(source.path)))
    return expanded if expanded.is_absolute() else (base / expanded)


def scan(source: SourceConfig, base: Path) -> ScanResult:
    """Walk the include dirs. Blocking file system work: call it in a worker thread."""
    root = resolve_root(source, base)
    if not root.is_dir():
        raise SourceError(f"Ordner der Quelle fehlt: {root}")
    result = ScanResult(root=root)
    seen: set[str] = set()
    exclude_dirs = set(source.exclude_dirs)
    exclude_names = set(source.exclude_names)
    for rule in source.includes:
        full = root / rule.dir
        if not full.is_dir():
            result.missing_dirs.append(rule.dir)
            continue
        endings = tuple(rule.ext)
        for current, dirs, files in os.walk(full):
            dirs[:] = sorted(d for d in dirs if d not in exclude_dirs)     # prune + stable order
            for name in sorted(files):
                if name in exclude_names or not name.endswith(endings):
                    continue
                path = Path(current) / name
                rel = path.relative_to(root).as_posix()
                if rel in seen:
                    continue
                seen.add(rel)
                try:
                    size = path.stat().st_size
                except OSError:
                    size = -1                   # vanished meanwhile: reading reports it
                result.files.append(Candidate(rel, path, size))
    if len(result.missing_dirs) == len(source.includes):
        raise SourceError("Keiner der Include-Ordner existiert: " + ", ".join(result.missing_dirs))
    return result


def read_text(path: Path, max_chars: int) -> FileText:
    """Exactly the scripts' read + cut. Raises OSError (reported as 'skipped', the old point stays)."""
    with open(path, encoding="utf-8", errors="ignore") as f:
        text = f.read(max_chars + 1)            # TextIOWrapper decodes incrementally: same as read()[:n+1]
    return FileText(text[:max_chars], len(text) > max_chars)
