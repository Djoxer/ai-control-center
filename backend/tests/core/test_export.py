import json
import sys

from control_center import export_openapi


def test_export_is_stable_and_has_no_side_effects(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "out" / "openapi.json"
    monkeypatch.setattr(sys, "argv", ["x", str(out)])
    export_openapi.main()
    first = out.read_bytes()
    export_openapi.main()
    assert out.read_bytes() == first                       # byte-identical -> no noise in git
    assert "/api/v1/health" in json.loads(first)["paths"]
    assert not (tmp_path / "data").exists()                # no db/logs created by the export


def test_export_opens_no_log_file(tmp_path, monkeypatch):
    import logging

    monkeypatch.setenv("ACC_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.setattr(sys, "argv", ["x", str(tmp_path / "openapi.json")])
    before = set(logging.getLogger("control_center").handlers)
    export_openapi.main()
    after = set(logging.getLogger("control_center").handlers)
    # a new FileHandler would keep the temp dir locked on Windows
    assert not [h for h in after - before if isinstance(h, logging.FileHandler)]
