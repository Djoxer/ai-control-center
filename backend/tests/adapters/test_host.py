import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psutil
import pytest

from control_center.adapters.common import SAMPLES_DIR
from control_center.adapters.host import (
    MAX_CMDLINE,
    FakeHost,
    ProcessReading,
    PsutilHost,
    drop_launchers,
    existing_ancestor,
    matches,
    mount_of,
    shorten_cmdline,
    stored_args_key,
)

RUNNER = [r"C:\Users\someone\AppData\Local\Programs\Ollama\ollama.exe", "runner",
          "--model", r"C:\Users\someone\.ollama\models\blobs\sha256-abc", "--port", "52114"]


def test_short_cmdline_hides_paths():
    assert shorten_cmdline(RUNNER, "short") == "ollama.exe runner --model sha256-abc --port 52114"


@pytest.mark.parametrize("argv, expected", [
    (["app", "--api-key", "s3cret", "--x", "1"], "app --api-key *** --x 1"),
    (["app", "--TOKEN=s3cret"], "app --TOKEN=***"),
    (["app", "--db=/home/me/data/x.db"], "app --db=x.db"),
    (["/usr/bin/python3", "-c", "import x\nprint(1)"], "python3 -c import x print(1)"),
])
def test_short_cmdline_masks_and_flattens(argv, expected):
    assert shorten_cmdline(argv, "short") == expected


def test_cmdline_modes_and_limits():
    assert shorten_cmdline(RUNNER, "off") is None
    assert shorten_cmdline([], "short") is None
    assert "someone" in shorten_cmdline(RUNNER, "full")
    long = shorten_cmdline(["app"] + ["x" * 50] * 10, "short")
    assert len(long) == MAX_CMDLINE and long.endswith("…")


@pytest.mark.parametrize("name, hit", [
    ("Ollama.EXE", True), ("ollama app.exe", True), ("ollama_llama_server.exe", True),
    ("com.docker.backend.exe", True), ("notepad.exe", False), ("my-ollama.exe", False),
])
def test_process_patterns(name, hit):
    assert matches(name, ["ollama*", "com.docker.backend"]) is hit


def test_existing_ancestor(tmp_path, monkeypatch):
    monkeypatch.delenv("ACC_UNSET_VAR", raising=False)
    assert existing_ancestor("%ACC_UNSET_VAR%/models") is None          # never measure a random drive
    assert existing_ancestor("$ACC_UNSET_VAR/models") is None
    assert existing_ancestor("relative/path") is None
    assert existing_ancestor(str(tmp_path / "not" / "there")) == tmp_path
    monkeypatch.setenv("ACC_MODELS", str(tmp_path))
    assert existing_ancestor("$ACC_MODELS/x") == tmp_path


def test_mount_of_respects_path_boundaries():
    mounts = ["/", "/mnt/data", "/mnt/data/deep"]
    assert mount_of(Path("/mnt/data2/x"), mounts) == "/"
    assert mount_of(Path("/mnt/data/x"), mounts) == "/mnt/data"
    assert mount_of(Path("/mnt/data/deep"), mounts) == "/mnt/data/deep"
    assert mount_of(Path(r"C:\Users\x"), ["C:\\", "D:\\"]) == "C:\\"   # Windows-style strings


def test_psutil_host_sees_this_process():
    host = PsutilHost()
    me = os.getpid()
    first = host.read(["python*", "pytest*"], "short")
    assert first.cpu_percent is None                      # first machine-wide value is meaningless
    second = host.read(["python*", "pytest*"], "short")
    own = [p for p in second.processes if p.pid == me]
    assert own and own[0].cpu_percent is not None and own[0].rss_bytes > 0
    assert second.cpu_percent is not None and 0 < second.ram_used_bytes <= second.ram_total_bytes


def test_psutil_disks_dedupes_same_drive(tmp_path):
    disks = PsutilHost().disks([str(tmp_path), str(tmp_path / "missing"), "%ACC_UNSET_VAR%"])
    assert len(disks) == 1 and disks[0].total_bytes > 0


def test_fake_host_filters_and_hides_cmdline():
    host = FakeHost(SAMPLES_DIR / "normal")
    r = host.read(["ollama*"], "off")
    assert {p.name for p in r.processes} == {"ollama.exe", "ollama app.exe"}
    assert all(p.cmdline is None for p in r.processes)
    assert host.disks([])[0].mount == "C:\\"


# ---- venv launcher twins ------------------------------------------------------------------------

def _proc(pid, ppid, name="python.exe", cmd="python.exe -m control_center", rss=100):
    return ProcessReading(name=name, pid=pid, cpu_percent=0.0, rss_bytes=rss, cmdline=cmd, ppid=ppid)


def test_drop_launchers_keeps_the_child():
    launcher, child = _proc(10, 1, rss=4), _proc(11, 10, rss=130)
    assert drop_launchers([(launcher, "-m control_center"), (child, "-m control_center")]) == [child]


@pytest.mark.parametrize("child_name, child_key, launcher_key", [
    ("python.exe", "-m other", "-m control_center"),   # different arguments: a real subprocess
    ("node.exe", "-m control_center", "-m control_center"),  # different program
    ("python.exe", None, None),                      # arguments unknown (access denied)
    ("python.exe", "", ""),                          # no arguments: no evidence of a twin
])
def test_drop_launchers_is_conservative(child_name, child_key, launcher_key):
    launcher, child = _proc(10, 1), _proc(11, 10, name=child_name)
    assert drop_launchers([(launcher, launcher_key), (child, child_key)]) == [launcher, child]


def test_drop_launchers_ignores_unrelated_twins():
    a, b = _proc(10, 1), _proc(11, 1)                # same command line, but siblings, not parent/child
    assert drop_launchers([(a, "-m x"), (b, "-m x")]) == [a, b]


def test_stored_args_key_handles_names_with_spaces():
    assert stored_args_key("python.exe", "python.exe -m control_center") == "-m control_center"
    assert stored_args_key("ollama app.exe", "ollama app.exe") == ""
    assert stored_args_key("Python.EXE", "python.exe -m x") == "-m x"
    assert stored_args_key("python.exe", "py.exe -m x") is None
    assert stored_args_key("python.exe", None) is None


def test_fake_host_hides_launchers_when_ppid_was_recorded(tmp_path):
    base = json.loads((SAMPLES_DIR / "normal" / "host.json").read_text(encoding="utf-8"))
    base["processes"] = [
        {"name": "python.exe", "pid": 10, "cpu_percent": 0.0, "rss_bytes": 4, "cmdline": "python.exe -m x", "ppid": 1},
        {"name": "python.exe", "pid": 11, "cpu_percent": 0.0, "rss_bytes": 9, "cmdline": "python.exe -m x", "ppid": 10},
    ]
    (tmp_path / "host.json").write_text(json.dumps(base), encoding="utf-8")
    assert [p.pid for p in FakeHost(tmp_path).read(["python*"], "short").processes] == [11]
    assert [p.pid for p in FakeHost(tmp_path).read(["python*"], "off").processes] == [11]   # key from file, not shown


TWIN = ("import os, subprocess, sys, time\n"
        "if os.environ.get('ACC_TWIN'): time.sleep(30)\n"
        "else: subprocess.run(sys.orig_argv, env={**os.environ, 'ACC_TWIN': '1'})")


def _settled_descendants(pid: int, timeout_s: float = 10) -> list[psutil.Process]:
    """Wait until the process tree below pid stops growing (same size for 5 polls in a row)."""
    deadline, last, stable = time.monotonic() + timeout_s, -1, 0
    tree: list[psutil.Process] = []
    while time.monotonic() < deadline and stable < 5:
        tree = psutil.Process(pid).children(recursive=True)
        stable = stable + 1 if tree and len(tree) == last else 0
        last = len(tree)
        time.sleep(0.05)
    return tree


def test_psutil_host_hides_a_real_launcher():
    """A python process starting itself with the same argv - the venv launcher pattern.

    The tree depth depends on the platform: on Linux A -> B(sleeps). On Windows sys.executable is
    itself the venv launcher, so the tree is A(launcher) -> B -> C(launcher) -> D(sleeps).
    Either way only the leaf does real work; every process above it has a same-args twin child.
    """
    parent = subprocess.Popen([sys.executable, "-c", TWIN, f"acc-twin-{uuid.uuid4().hex}"])
    try:
        tree = _settled_descendants(parent.pid)
        assert tree, "twin process did not start"
        leaves = [p for p in tree if not p.children()]
        assert len(leaves) == 1
        chain = {parent.pid} | {p.pid for p in tree} - {leaves[0].pid}
        pids = {p.pid for p in PsutilHost().read(["python*"], "off").processes}
        assert leaves[0].pid in pids
        assert not chain & pids, "launchers above the leaf must be hidden"
    finally:
        for k in psutil.Process(parent.pid).children(recursive=True):
            k.kill()
        parent.kill()
        parent.wait()


def test_drop_launchers_collapses_a_chain():
    """Windows shape of the test above: launcher -> python -> launcher -> python, all same args."""
    a, b, c, d = _proc(1, 0), _proc(2, 1), _proc(3, 2), _proc(4, 3)
    assert drop_launchers([(r, "-c x") for r in (a, b, c, d)]) == [d]
