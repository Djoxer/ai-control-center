import pytest

from control_center.core import manual as help_module
from control_center.core.manual import split_title


def test_core_first_then_switched_on_modules(make_client):
    with make_client() as c:
        docs = c.get("/api/v1/help").json()["docs"]
    assert [d["key"] for d in docs] == ["core", "good"]            # failing/not_ready have no HELP.md
    core, good = docs
    assert core["title"] == "Allgemein" and core["moduleState"] is None
    assert not core["markdown"].startswith("#")                     # title is not repeated in the body
    assert good == {"key": "good", "title": "Gutes Modul", "moduleState": "running",
                    "markdown": "Text für **Bedienung** und Betrieb."}


def test_disabled_module_takes_its_help_along(make_client, settings):
    mods = settings.modules.model_copy(update={"disabled": ["good"]})
    with make_client(modules=mods) as c:
        assert [d["key"] for d in c.get("/api/v1/help").json()["docs"]] == ["core"]


def test_help_is_read_per_request(make_client, tmp_path, monkeypatch):
    page = tmp_path / "HELP.md"
    page.write_text("# Eins\n\nalt", encoding="utf-8")
    monkeypatch.setattr(help_module, "CORE_HELP", page)
    with make_client() as c:
        assert c.get("/api/v1/help").json()["docs"][0]["markdown"] == "alt"
        page.write_text("# Eins\n\nneu", encoding="utf-8")
        assert c.get("/api/v1/help").json()["docs"][0]["markdown"] == "neu"


def test_changelog_present_and_missing(make_client, tmp_path, monkeypatch):
    log = tmp_path / "CHANGELOG.md"
    monkeypatch.setattr(help_module, "CHANGELOG_PATH", log)
    with make_client() as c:
        missing = c.get("/api/v1/help/changelog").json()
        assert missing["markdown"] is None and "git-cliff" in missing["hint"]
        log.write_text("# Was ist neu\n\n## 0.5.0\n", encoding="utf-8")
        assert c.get("/api/v1/help/changelog").json() == {"markdown": "# Was ist neu\n\n## 0.5.0", "hint": None}


@pytest.mark.parametrize("text, title, body", [
    ("# Logs\n\nText", "Logs", "Text"),
    ("\n\n#  Mit Abstand  \nText", "Mit Abstand", "Text"),
    ("Kein Titel\n## Unter", "Fallback", "Kein Titel\n## Unter"),
    ("## Nur H2\nText", "Fallback", "## Nur H2\nText"),            # only a level-1 heading is the title
    ("# Nur Titel", "Nur Titel", ""),
])
def test_split_title(text, title, body):
    assert split_title(text, "Fallback") == (title, body)


def test_every_real_module_with_a_router_has_help():
    # the shipped modules explain themselves; a new module without HELP.md fails here
    from control_center.core.loader import discover
    for d in discover():
        if d.spec is not None:
            assert d.folder and (d.folder / "HELP.md").is_file(), f"{d.key} has no HELP.md"
