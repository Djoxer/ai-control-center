import os
from pathlib import Path

import pytest

from control_center.adapters.common import SAMPLES_DIR
from control_center.adapters.host import (
    MAX_CMDLINE, FakeHost, PsutilHost, existing_ancestor, matches, mount_of, shorten_cmdline,
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
