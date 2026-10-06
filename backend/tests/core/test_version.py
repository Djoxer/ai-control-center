import tomllib
from pathlib import Path

from control_center import __version__

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_version_comes_from_pyproject():
    # fails if the package metadata is stale -> run "uv sync" after changing the version
    expected = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    assert __version__ == expected


def test_health_reports_same_version(make_client):
    with make_client() as c:
        assert c.get("/api/v1/health").json()["version"] == __version__