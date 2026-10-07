"""Process level: command resolution, output log, tree stop, leftovers, port owners. Real child processes."""
import json
import os
import socket
import sys
import time
from pathlib import Path

import psutil
import pytest

from control_center.modules.logs.reader import parse_line
from control_center.modules.mcp.process import (
    ManagedProcess,
    ProcessLog,
    ProcessRecord,
    SpawnError,
    child_env,
    detect_level,
    find_listeners,
    kill_leftover,
    resolve_argv,
    resolve_cwd,
    save_record,
)

FAKE = Path(__file__).parents[2] / "fixtures" / "mcp_fake.py"


def wait_until(pred, timeout=5.0, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return False


@pytest.fixture
def plog(tmp_path):
    out = ProcessLog(tmp_path / "logs" / "mcp-x.log", max_bytes=1024 * 1024, backup_count=1)
    yield out
    out.close()


# ---- command line --------------------------------------------------------------------------

def test_python_placeholder_and_path_lookup(tmp_path, monkeypatch):
    argv = resolve_argv(["{python}", "-c", "print(1)"], tmp_path)
    assert argv[0] == sys.executable
    exe = Path(sys.executable)
    monkeypatch.setenv("PATH", str(exe.parent))
    found = resolve_argv([exe.stem, "x.py"], tmp_path)[0]     # bare name via PATH (+ ".exe" on Windows)
    assert os.path.samefile(found, exe)


def test_relative_program_is_resolved_against_cwd(tmp_path):
    (tmp_path / "bin").mkdir()
    prog = tmp_path / "bin" / "server"
    prog.write_text("", encoding="utf-8")
    assert resolve_argv(["bin/server"], tmp_path)[0] == str(tmp_path / "bin" / "server")


def test_missing_program_and_folder_give_readable_errors(tmp_path):
    with pytest.raises(SpawnError, match="nicht im PATH"):
        resolve_argv(["surely-not-installed-xyz"], tmp_path)
    with pytest.raises(SpawnError, match="Programm nicht gefunden"):
        resolve_argv(["./venv/bin/python"], tmp_path)
    with pytest.raises(SpawnError, match="Arbeitsordner fehlt"):
        resolve_cwd("does/not/exist", tmp_path)
    assert resolve_cwd(None, tmp_path) == tmp_path
    (tmp_path / "rag").mkdir()
    assert resolve_cwd("rag", tmp_path) == tmp_path / "rag"                  # relative = next to the toml


def test_child_env_defaults_can_be_overridden():
    env = child_env({"PYTHONUNBUFFERED": "0", "EXTRA": "1"})
    assert env["PYTHONUNBUFFERED"] == "0" and env["EXTRA"] == "1" and env["PYTHONIOENCODING"] == "utf-8"


@pytest.mark.parametrize("line,level", [
    ("INFO:     Uvicorn running on http://127.0.0.1:8000", "INFO"),
    ("ERROR:root:something broke", "ERROR"),
    ("[WARN] slow", "WARNING"),
    ('time=2026-10-07 level=warn msg="x"', "WARNING"),
    ("Traceback (most recent call last):", "ERROR"),
    ("StreamableHTTP session manager started", None),
    ("information about INFO", None),                       # only a prefix counts
])
def test_detect_level(line, level):
    assert detect_level(line) == level


# ---- output log ----------------------------------------------------------------------------

def test_process_log_is_readable_by_the_logs_module(plog):
    plog.output("INFO:     ready – grüne Ampel")
    plog.supervisor("ERROR", "exited unexpectedly")
    lines = plog.path.read_text(encoding="utf-8").splitlines()
    first, second = (parse_line(x, "json", "mcp-x", plog.path.name) for x in lines)
    assert (first.logger, first.level, first.msg) == ("output", "INFO", "INFO:     ready – grüne Ampel")
    assert (second.logger, second.level) == ("supervisor", "ERROR")
    assert first.ts is not None


def test_process_log_line_without_level_has_none(plog):
    plog.output("plain text")
    entry = parse_line(plog.path.read_text(encoding="utf-8").strip(), "json", "mcp-x", "f")
    assert entry.level is None


# ---- running processes ---------------------------------------------------------------------

def test_output_is_captured_and_exit_code_reported(tmp_path, plog):
    proc = ManagedProcess.spawn([sys.executable, str(FAKE), "print-exit", "3"], tmp_path, child_env({}), plog)
    assert wait_until(lambda: proc.poll() is not None)
    proc.wait_output()
    assert proc.poll() == 3
    assert list(proc.recent) == ["hello from fake", "ERROR: boom - fake crash"]
    text = plog.path.read_text(encoding="utf-8")
    assert "hello from fake" in text and '"level": "ERROR"' in text


def test_stop_ends_the_whole_tree(tmp_path, plog):
    """The venv launcher case: killing only the PID we started would leave the real server alive."""
    pid_file = tmp_path / "grandchild.pid"
    proc = ManagedProcess.spawn([sys.executable, str(FAKE), "tree", str(pid_file)], tmp_path, child_env({}), plog)
    assert wait_until(lambda: pid_file.exists() and pid_file.read_text().strip())
    grandchild = psutil.Process(int(pid_file.read_text()))
    try:
        assert proc.owns(grandchild.pid)
        proc.stop(timeout=3)
        assert proc.poll() is not None
        assert wait_until(lambda: not grandchild.is_running() or grandchild.status() == psutil.STATUS_ZOMBIE, 3)
    finally:
        for p in (grandchild, psutil.Process(proc.pid) if proc.poll() is None else None):
            if p is not None and p.is_running():
                p.kill()                                  # a failing run must not leave sleepers behind


def test_tree_remembers_children_of_a_dead_parent(tmp_path, plog):
    pid_file = tmp_path / "grandchild.pid"
    proc = ManagedProcess.spawn([sys.executable, str(FAKE), "tree", str(pid_file)], tmp_path, child_env({}), plog)
    assert wait_until(lambda: pid_file.exists() and pid_file.read_text().strip())
    grandchild_pid = int(pid_file.read_text())
    assert proc.owns(grandchild_pid)                      # seen once ...
    psutil.Process(proc.pid).kill()                       # ... then the parent dies alone
    assert wait_until(lambda: proc.poll() is not None)
    left = proc.tree()
    try:
        # still found and can be cleaned up. On Windows the venv launcher adds a level per Python
        # process, so more than the grandchild may be left - but never the dead root.
        assert grandchild_pid in {p.pid for p in left}
        assert proc.pid not in {p.pid for p in left}
    finally:
        for p in left:
            p.kill()


# ---- leftovers after a hard crash of the control center ------------------------------------

def _sleeper(tmp_path):
    return psutil.Popen([sys.executable, str(FAKE), "sleep"], cwd=tmp_path)


def test_leftover_with_matching_record_is_stopped(tmp_path):
    p = _sleeper(tmp_path)
    rec = tmp_path / "x.json"
    save_record(rec, ProcessRecord(p.pid, p.create_time(), ["fake"]))
    assert kill_leftover(rec, timeout=3) == p.pid
    assert wait_until(lambda: not p.is_running(), 3)      # psutil already reaped it while waiting
    assert not rec.exists()


def test_reused_pid_is_never_killed(tmp_path):
    """Same PID, other creation time = some other program got the number. Hands off."""
    p = _sleeper(tmp_path)
    rec = tmp_path / "x.json"
    save_record(rec, ProcessRecord(p.pid, p.create_time() - 3600, ["fake"]))
    try:
        assert kill_leftover(rec, timeout=1) is None
        assert p.is_running() and p.status() != psutil.STATUS_ZOMBIE
        assert not rec.exists()                           # stale record is cleaned up anyway
    finally:
        for q in [*p.children(recursive=True), p]:      # Windows: launcher + real interpreter
            q.kill()
        p.wait(3)


def test_broken_record_is_dropped(tmp_path):
    rec = tmp_path / "x.json"
    rec.write_text("{not json", encoding="utf-8")
    assert kill_leftover(rec, timeout=1) is None and not rec.exists()
    rec.write_text(json.dumps({"pid": 2 ** 22 + 12345, "createTime": 1.0}), encoding="utf-8")
    assert kill_leftover(rec, timeout=1) is None and not rec.exists()


# ---- port owners ---------------------------------------------------------------------------

def test_find_listeners_names_the_process():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        found = find_listeners({port, 1})
        assert found is not None, "socket table not readable in this environment"
        assert found[port].pid == psutil.Process().pid
        assert 1 not in found
