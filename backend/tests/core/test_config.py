from control_center.core.config import load_settings


def test_toml_then_env_precedence(tmp_path, monkeypatch):
    cfg = tmp_path / "control-center.toml"
    cfg.write_text('port = 9000\ndata_dir = "rt"\n[log]\nlevel = "DEBUG"\n', encoding="utf-8")
    monkeypatch.setenv("ACC_CONFIG", str(cfg))
    monkeypatch.setenv("ACC_LOG__LEVEL", "WARNING")
    monkeypatch.chdir(tmp_path.parent)                         # simulate autostart with a foreign CWD
    s = load_settings()
    assert s.port == 9000                                      # from toml
    assert s.log.level == "WARNING"                            # env beats toml
    assert s.data_dir == (tmp_path / "rt").resolve()           # anchored at the toml, not the CWD


def test_missing_toml_gives_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_CONFIG", str(tmp_path / "nope.toml"))
    s = load_settings()
    assert s.host == "127.0.0.1" and s.port == 8090


def test_module_sections_are_passed_through(tmp_path, monkeypatch):
    cfg = tmp_path / "control-center.toml"
    cfg.write_text('[modules]\ndisabled = ["catalog"]\n[modules.logs]\ntail_interval_s = 2.5\n', encoding="utf-8")
    monkeypatch.setenv("ACC_CONFIG", str(cfg))
    s = load_settings()
    assert s.modules.disabled == ["catalog"]
    assert s.modules.section("logs") == {"tail_interval_s": 2.5}
    assert s.modules.section("dashboard") == {}               # no table -> empty, module uses defaults
