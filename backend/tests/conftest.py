import pytest
from fastapi.testclient import TestClient

from control_center.core.config import Settings
from control_center.main import create_app


@pytest.fixture
def settings(tmp_path, monkeypatch):
    # point ACC_CONFIG at a non-existing file -> pure defaults, nothing from the dev machine leaks in
    monkeypatch.setenv("ACC_CONFIG", str(tmp_path / "missing.toml"))
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def make_client(settings):
    def _make(**overrides) -> TestClient:
        s = settings.model_copy(update=overrides)
        return TestClient(create_app(s, modules_package="fake_modules"))
    return _make
