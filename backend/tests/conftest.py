import pytest
from fastapi.testclient import TestClient

from control_center.core.config import AdaptersConfig, Settings
from control_center.main import create_app


@pytest.fixture
def settings(tmp_path, monkeypatch):
    # point ACC_CONFIG at a non-existing file -> pure defaults, nothing from the dev machine leaks in
    monkeypatch.setenv("ACC_CONFIG", str(tmp_path / "missing.toml"))
    # fake adapters: no test may depend on the Ollama, GPU or process list of the machine it runs on
    fakes = AdaptersConfig(ollama="fake", gpu="fake", host="fake", fake_scenario="normal")
    return Settings(data_dir=tmp_path / "data", adapters=fakes)


@pytest.fixture
def make_client(settings):
    def _make(**overrides) -> TestClient:
        s = settings.model_copy(update=overrides)
        return TestClient(create_app(s, modules_package="fake_modules"))
    return _make
