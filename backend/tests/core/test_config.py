from pathlib import Path

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


def test_example_toml_matches_the_settings_models(monkeypatch):
    """The documented example must load: a typo there costs the next person an evening."""
    from control_center.modules.dashboard.settings import DashboardSettings
    from control_center.modules.logs.settings import LogsSettings

    example = Path(__file__).parents[2] / "control-center.example.toml"
    monkeypatch.setenv("ACC_CONFIG", str(example))
    s = load_settings()
    assert s.adapters.expand(s.adapters.ollama_url) == "http://127.0.0.1:11434"
    dash = DashboardSettings.model_validate(s.modules.section("dashboard"))
    assert [p.key for p in dash.probes] == ["mcp", "openwebui", "qdrant"]
    assert dash == DashboardSettings()                        # example documents the defaults
    LogsSettings.model_validate(s.modules.section("logs"))


def test_adapters_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_CONFIG", str(tmp_path / "nope.toml"))
    monkeypatch.setenv("ACC_ADAPTERS__AI_HOST", "10.1.2.3")
    monkeypatch.setenv("ACC_ADAPTERS__GPU", "fake")
    s = load_settings()
    assert s.adapters.ai_host == "10.1.2.3" and s.adapters.gpu == "fake"
