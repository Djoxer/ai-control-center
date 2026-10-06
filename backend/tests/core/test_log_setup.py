import json
import logging

from control_center.core.config import Settings
from control_center.core.log_setup import setup_logging


def test_reconfigure_closes_previous_file_handler(tmp_path):
    setup_logging(Settings(data_dir=tmp_path / "a"))
    first = [h for h in logging.getLogger("control_center").handlers if isinstance(h, logging.FileHandler)][0]
    setup_logging(Settings(data_dir=tmp_path / "b"))
    assert first.stream is None          # closed -> Windows can delete/rotate the old file
    assert first not in logging.getLogger("control_center").handlers


def test_file_lines_are_json(tmp_path):
    s = Settings(data_dir=tmp_path)
    setup_logging(s)
    logging.getLogger("control_center.modules.demo").warning("hello %s", "world")
    for h in logging.getLogger("control_center").handlers:
        h.flush()
    line = (s.log_dir / s.log.file_name).read_text(encoding="utf-8").strip().splitlines()[-1]
    entry = json.loads(line)
    assert entry["msg"] == "hello world"
    assert entry["logger"] == "control_center.modules.demo"   # module name survives -> logs filter
    assert entry["level"] == "WARNING"
