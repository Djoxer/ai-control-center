import os

import pytest

from control_center.core.config import AdaptersConfig
from control_center.core.tail import FileTail


def test_attach_skips_old_content_and_waits_for_complete_lines(tmp_path):
    log = tmp_path / "server.log"
    log.write_bytes(b"old\n")
    tail = FileTail([str(tmp_path / "server*.log")])
    tail.attach(from_end=True)
    with log.open("ab") as f:
        f.write(b"new\r\nhalf")
    assert tail.poll() == ["new"]                       # CRLF handled, half line kept back
    with log.open("ab") as f:
        f.write(b" done\n")
    assert tail.poll() == ["half done"]
    assert tail.poll() == []


def test_follows_rotation_and_truncation(tmp_path):
    log = tmp_path / "server.log"
    log.write_bytes(b"a\n")
    tail = FileTail([str(tmp_path / "server*.log")])
    tail.attach()
    os.replace(log, tmp_path / "server-1.log")
    os.utime(tmp_path / "server-1.log", (1_000, 1_000))
    log.write_bytes(b"after rotation\n")
    assert tail.poll() == ["after rotation"]
    log.write_bytes(b"x\n")                             # truncated and rewritten: shorter than offset
    assert tail.poll() == ["x"]


def test_giant_line_does_not_block_the_tail(tmp_path):
    log = tmp_path / "server.log"
    log.write_bytes(b"")
    tail = FileTail([str(log)], max_bytes=16)
    tail.attach()
    log.write_bytes(b"0123456789abcdefXYZ\nok\n")
    assert tail.poll() == ["0123456789abcdef"]           # cut at max_bytes instead of waiting forever
    assert tail.poll() == ["XYZ", "ok"]


def test_missing_file_is_not_an_error(tmp_path):
    tail = FileTail([str(tmp_path / "nope*.log")])
    tail.attach()
    assert tail.poll() == [] and tail.file is None
    (tmp_path / "nope.log").write_text("appeared\n")
    assert tail.poll() == ["appeared"]                  # a file that shows up later is read from its start


@pytest.mark.parametrize("value", ["http://192.168.1.20/", "192.168.1.20/", "http://box", "", "a b"])
def test_ai_host_rejects_urls(value):
    with pytest.raises(ValueError, match="host name or IP only"):
        AdaptersConfig(ai_host=value)


def test_ai_host_accepts_names_and_ips():
    assert AdaptersConfig(ai_host=" 192.168.1.20 ").ai_host == "192.168.1.20"
    assert AdaptersConfig(ai_host="ai-box").expand("http://{ai_host}:11434") == "http://ai-box:11434"
