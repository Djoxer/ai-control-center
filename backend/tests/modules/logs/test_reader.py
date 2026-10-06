import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from control_center.modules.logs import reader
from control_center.modules.logs.reader import EntryFilter, iter_lines_reverse, parse_line, read_page


def jline(i: int, level: str = "INFO", logger: str = "control_center.core", msg: str | None = None) -> str:
    ts = datetime(2026, 10, 6, 12, 0, i % 60, tzinfo=timezone.utc).isoformat()
    return json.dumps({"ts": ts, "level": level, "logger": logger, "msg": msg or f"line {i}"})


def write(path: Path, lines: list[str], newline: str = "\n", trailing: bool = True) -> Path:
    path.write_bytes((newline.join(lines) + (newline if trailing else "")).encode("utf-8"))
    return path


# ---- parsing ---------------------------------------------------------------------------------

def test_parse_json_line():
    e = parse_line(jline(1, "WARNING", msg="hot"), "json", "cc", "f.log")
    assert (e.level, e.logger, e.msg) == ("WARNING", "control_center.core", "hot")
    assert e.ts is not None and e.ts.tzinfo is not None


def test_parse_ollama_slog_line():
    line = 'time=2026-10-06T12:00:01.123+02:00 level=WARN source=server.go:42 msg="model \\"x\\" offloaded" n=3'
    e = parse_line(line, "text", "ollama", "server.log")
    assert e.level == "WARNING"                      # WARN normalized
    assert e.logger == "server.go:42"
    assert e.msg == 'model "x" offloaded'            # quotes unescaped
    assert e.ts.utcoffset().total_seconds() == 7200


def test_unparsable_lines_become_raw_entries():
    for fmt, line in [("json", "not json {"), ("text", "ggml_cuda_init: found 1 CUDA devices")]:
        e = parse_line(line, fmt, "s", "f")
        assert e.msg == line and e.level is None and e.ts is None


# ---- reverse reading -------------------------------------------------------------------------

def test_reverse_reads_crlf_and_skips_unfinished_last_line(tmp_path):
    p = tmp_path / "a.log"
    p.write_bytes(b"one\r\ntwo\r\nthree-half")       # writer is mid-line on 'three'
    assert [t for _, t in iter_lines_reverse(p)] == ["two", "one"]


def test_reverse_across_small_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(reader, "CHUNK", 3)           # forces lines to span chunk borders
    p = write(tmp_path / "a.log", [f"line-{i}-äö" for i in range(20)])
    assert [t for _, t in iter_lines_reverse(p)] == [f"line-{i}-äö" for i in reversed(range(20))]


# ---- paging over rotated files ---------------------------------------------------------------

@pytest.fixture
def rotated(tmp_path):
    """control-center.log (newest, lines 20-29) and .log.1 (older, lines 0-19)."""
    old = write(tmp_path / "control-center.log.1", [jline(i) for i in range(20)])
    new = write(tmp_path / "control-center.log", [jline(i) for i in range(20, 30)])
    os.utime(old, (1_000, 1_000))                      # rotated file is older
    return reader.resolve_files([str(tmp_path / "control-center.log*")])


def collect_all(files, flt, limit):
    msgs, cursor, pages = [], None, 0
    while True:
        entries, cursor = read_page(files, "json", "cc", flt, limit, cursor)
        msgs += [e.msg for e in entries]
        pages += 1
        if cursor is None:
            return msgs, pages


def test_resolve_files_newest_first(rotated):
    assert [p.name for p in rotated] == ["control-center.log", "control-center.log.1"]


def test_paging_is_complete_without_duplicates(rotated):
    msgs, pages = collect_all(rotated, EntryFilter(), limit=7)
    assert msgs == [f"line {i}" for i in reversed(range(30))]
    assert pages == 5                                  # 7+7+7+7+2


def test_exact_page_boundary_has_no_empty_extra_page(rotated):
    entries, cursor = read_page(rotated, "json", "cc", EntryFilter(), 30, None)
    assert len(entries) == 30 and cursor is None


def test_filters(tmp_path):
    p = write(tmp_path / "x.log", [
        jline(1, "DEBUG"), jline(2, "INFO", "control_center.modules.logs"), jline(3, "ERROR", msg="Disk FULL"),
        "raw line without level",
    ])
    files = [p]
    by = lambda **kw: [e.msg for e in read_page(files, "json", "s", EntryFilter(**kw), 50)[0]]  # noqa: E731
    assert by(min_level="INFO") == ["Disk FULL", "line 2"]          # raw line hidden by level filter
    assert by(logger_prefix="control_center.modules") == ["line 2"]
    assert by(exclude_prefixes=("control_center",)) == ["raw line without level"]   # raw lines have no logger
    assert by(query="disk full") == ["Disk FULL"]                   # case-insensitive
    assert by(query="raw") == ["raw line without level"]            # text search still finds raw lines
    since = datetime(2026, 10, 6, 12, 0, 2, tzinfo=timezone.utc)
    assert by(since=since) == ["Disk FULL", "line 2"]               # raw line has no ts -> excluded


def test_stale_cursor_is_reported(rotated):
    with pytest.raises(ValueError):
        read_page(rotated, "json", "cc", EntryFilter(), 5, "gone.log|10")
    with pytest.raises(ValueError):
        read_page(rotated, "json", "cc", EntryFilter(), 5, "garbage")
