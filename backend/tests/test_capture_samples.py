"""capture_samples CLI: scenario name instead of path, protection of synthetic test data."""
import asyncio
import json

import pytest

from control_center import capture_samples
from control_center.capture_samples import (
    EXIT_USAGE,
    ScenarioError,
    is_synthetic,
    resolve_target,
)


@pytest.fixture
def samples(tmp_path):
    root = tmp_path / "samples"
    (root / "normal").mkdir(parents=True)
    (root / "normal" / "meta.json").write_text(json.dumps({"source": "synthetic"}), encoding="utf-8")
    (root / "real-normal").mkdir()
    (root / "real-normal" / "meta.json").write_text(json.dumps({"source": "capture"}), encoding="utf-8")
    return root


@pytest.mark.parametrize("bad", [
    "src/control_center/adapters/samples/real-normal",     # old call style
    r"src\control_senter\adapters\samples\real-idle",      # the typo that created a phantom tree
])
def test_paths_are_rejected_with_a_hint(samples, bad):
    with pytest.raises(ScenarioError, match="scenario name only"):
        resolve_target(bad, samples)


@pytest.mark.parametrize("bad", ["Real-Normal", "-x", "", "a b", "x.y"])
def test_invalid_names(samples, bad):
    with pytest.raises(ScenarioError, match="invalid scenario name"):
        resolve_target(bad, samples)


def test_synthetic_is_protected_unless_forced(samples):
    with pytest.raises(ScenarioError, match="real-normal"):
        resolve_target("normal", samples)
    assert resolve_target("normal", samples, force=True) == samples / "normal"
    assert resolve_target("real-normal", samples) == samples / "real-normal"      # re-capture is fine
    assert resolve_target("real-new", samples) == samples / "real-new"            # not created yet


def test_missing_samples_folder(tmp_path):
    with pytest.raises(ScenarioError, match="samples folder not found"):
        resolve_target("real-x", tmp_path / "nope")


def test_is_synthetic_tolerates_broken_meta(tmp_path):
    (tmp_path / "meta.json").write_text("not json", encoding="utf-8")
    assert is_synthetic(tmp_path) is False
    assert is_synthetic(tmp_path / "missing") is False


def test_main_creates_no_folder_on_error(samples, monkeypatch, capsys):
    monkeypatch.setattr(capture_samples, "SAMPLES_DIR", samples)
    before = sorted(p.name for p in samples.iterdir())
    assert capture_samples.main(["src/control_senter/adapters/samples/real-idle"]) == EXIT_USAGE
    assert capture_samples.main(["normal"]) == EXIT_USAGE
    assert sorted(p.name for p in samples.iterdir()) == before
    assert "synthetic test data" in capsys.readouterr().err


def test_main_passes_target_and_note(samples, monkeypatch, capsys):
    monkeypatch.setattr(capture_samples, "SAMPLES_DIR", samples)
    monkeypatch.setattr(capture_samples, "load_settings", lambda: "settings")
    seen = {}

    async def fake_capture(target, settings, note=""):
        seen.update(target=target, settings=settings, note=note)
        target.mkdir(exist_ok=True)
        return ["host.json", "meta.json"], ["ollama-ps.json: ConnectError"]

    monkeypatch.setattr(capture_samples, "capture", fake_capture)
    assert capture_samples.main(["real-idle", "--note", "nothing loaded"]) == 0
    assert seen == {"target": samples / "real-idle", "settings": "settings", "note": "nothing loaded"}
    out = capsys.readouterr().out
    assert "scenario written" in out and "missing  ollama-ps.json" in out
    assert capture_samples.main(["real-normal"]) == 0
    assert "scenario replaced" in capsys.readouterr().out


def test_capture_creates_one_level_only(settings, tmp_path):
    """A wrong parent must fail loudly instead of growing a new folder tree."""
    with pytest.raises(FileNotFoundError):
        asyncio.run(capture_samples.capture(tmp_path / "typo" / "real-x", settings, cpu_window_s=0))
    assert not (tmp_path / "typo").exists()
