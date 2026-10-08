"""Config validation and file selection. The scanner must pick and read exactly what the old scripts did."""
import os
import random
from pathlib import Path

import pytest
from pydantic import ValidationError

from control_center.modules.rag.scanner import (
    SourceError,
    read_text,
    resolve_root,
    scan,
)
from control_center.modules.rag.settings import RagSettings, SourceConfig


def source(**over):
    base = {"collection": "bent_php", "title": "PHP", "path": "repo", "includes": [{"dir": "src", "ext": [".php"]}]}
    return {**base, **over}


# ---- settings ------------------------------------------------------------------------------------

def test_defaults_match_the_scripts():
    s = SourceConfig.model_validate(source())
    assert s.exclude_names == [".env", ".htpasswd"]
    assert s.exclude_dirs == ["vendor", "node_modules", ".git", "var"]
    cfg = RagSettings()
    assert (cfg.max_chars, cfg.batch_size, cfg.embedding_model) == (6000, 50, "nomic-embed-text")
    assert cfg.allow_remote_writes is False and cfg.sources == []


@pytest.mark.parametrize("bad", [
    {"collection": "bent php"},
    {"collection": "../etc"},
    {"includes": []},
    {"includes": [{"dir": "../outside", "ext": [".php"]}]},
    {"includes": [{"dir": "C:/abs", "ext": [".php"]}]},
    {"includes": [{"dir": "src", "ext": ["php"]}]},          # dot missing
    {"includes": [{"dir": "src", "ext": []}]},
])
def test_invalid_sources(bad):
    with pytest.raises(ValidationError):
        SourceConfig.model_validate(source(**bad))


def test_include_dir_is_normalized():
    s = SourceConfig.model_validate(source(includes=[{"dir": "\\db\\migrations\\", "ext": [".sql"]}]))
    assert s.includes[0].dir == "db/migrations"


def test_one_source_per_collection():
    with pytest.raises(ValidationError, match="unique"):
        RagSettings.model_validate({"sources": [source(), source(title="again")]})


@pytest.mark.parametrize("url", ["localhost:6333", "ftp://x:6333", "http://127.0.0.1:99999"])
def test_invalid_qdrant_url(url):
    with pytest.raises(ValidationError):
        RagSettings.model_validate({"qdrant_url": url})


def test_qdrant_url_with_placeholder_is_fine():
    assert RagSettings.model_validate({"qdrant_url": "http://{ai_host}:6333/"}).qdrant_url == "http://{ai_host}:6333"


# ---- scanner: the old scripts as oracle -----------------------------------------------------------

def script_oracle(repo: Path, includes, exclude_names, exclude_dirs, max_chars=6000):
    """index_bent.py's loop, verbatim apart from embedding/upsert: {normalized rel: text} of indexed files."""
    out = {}
    repo_path = str(repo)
    for include_dir, extensions in includes:
        full_dir = os.path.join(repo_path, include_dir)
        if not os.path.isdir(full_dir):
            continue
        for root, dirs, files in os.walk(full_dir):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for fname in files:
                if fname in exclude_names or not fname.endswith(extensions):
                    continue
                fpath = os.path.join(root, fname)
                rel = fpath.replace(repo_path, "")
                with open(fpath, encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                text = text[:max_chars]
                if not text.strip():
                    continue
                out[rel.replace("\\", "/").lstrip("/")] = text
    return out


def write(root: Path, rel: str, data: bytes) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    rnd = random.Random(7)
    write(root, "src/Domain/Note.php", b"<?php\r\nclass Note {}\r\n")
    write(root, "src/Domain/Big.php", ("<?php\r\n" + "// umlaut \xc3\xa4\r\n" * 700).encode("latin1"))
    write(root, "src/Domain/Bom.php", b"\xef\xbb\xbf<?php echo 1;\n")
    write(root, "src/Domain/Invalid.php", b"<?php \xff\xfe broken \x80 bytes\n" * 400)
    write(root, "src/Domain/Empty.php", b"")
    write(root, "src/Domain/Blank.php", b"  \r\n\t\n")
    write(root, "src/Domain/Upper.PHP", b"<?php upper")             # case-sensitive ending: not taken
    write(root, "src/.env", b"DB_PASS=x")
    write(root, "src/vendor/lib.php", b"<?php vendor")
    write(root, "src/Deep/var/cache.php", b"<?php cache")
    write(root, "src/a.spec.php", b"<?php spec")                     # endswith('.php'): taken
    write(root, "app/routes.php", b"<?php routes\r" * 3000)         # old Mac line endings
    write(root, "app/readme.md", b"no")
    write(root, "db/migrations/001.sql", b"CREATE TABLE x();\n")
    write(root, "db/migrations/002.php", b"<?php not sql")
    write(root, "src/exact.php", b"a" * 6000)
    write(root, "src/plus1.php", b"a" * 6001)
    write(root, "src/crlf_edge.php", b"a" * 5999 + b"\r\nzzz")      # \r\n straddles the cut
    write(root, "src/random.php", bytes(rnd.randrange(256) for _ in range(30000)))
    return root


INCLUDES = [{"dir": "src", "ext": [".php"]}, {"dir": "app", "ext": [".php"]}, {"dir": "db/migrations", "ext": [".sql"]}]


def test_same_files_and_texts_as_the_script(repo, tmp_path):
    src = SourceConfig.model_validate(source(path=str(repo), includes=INCLUDES))
    result = scan(src, tmp_path)
    mine = {}
    for c in result.files:
        ft = read_text(c.path, 6000)
        if ft.text.strip():
            mine[c.rel] = ft.text
    oracle = script_oracle(repo, [("src", (".php",)), ("app", (".php",)), ("db/migrations", (".sql",))],
                           {".env", ".htpasswd"}, {"vendor", "node_modules", ".git", "var"})
    assert mine == oracle
    assert "src/Domain/Upper.PHP" not in mine and "src/vendor/lib.php" not in mine


@pytest.mark.parametrize("rel, truncated", [
    ("src/exact.php", False), ("src/plus1.php", True), ("src/crlf_edge.php", True),
    ("src/Domain/Note.php", False), ("app/routes.php", True),
])
def test_truncated_flag(repo, rel, truncated):
    ft = read_text(repo / rel, 6000)
    assert ft.truncated is truncated and len(ft.text) <= 6000


def test_read_text_equals_full_read_and_cut(repo):
    for path in repo.rglob("*"):
        if path.is_file():
            with open(path, encoding="utf-8", errors="ignore") as f:
                assert read_text(path, 6000).text == f.read()[:6000], path


def test_relative_paths_use_slashes_and_overlap_is_taken_once(repo, tmp_path):
    src = SourceConfig.model_validate(source(path=str(repo), includes=[
        {"dir": "src", "ext": [".php"]}, {"dir": "src/Domain", "ext": [".php"]}]))
    rels = [c.rel for c in scan(src, tmp_path).files]
    assert len(rels) == len(set(rels))
    assert "src/Domain/Note.php" in rels and all("\\" not in r and not r.startswith("/") for r in rels)


def test_missing_include_dirs_are_reported(repo, tmp_path):
    src = SourceConfig.model_validate(source(path=str(repo), includes=[
        {"dir": "src", "ext": [".php"]}, {"dir": "nope", "ext": [".php"]}]))
    assert scan(src, tmp_path).missing_dirs == ["nope"]


def test_all_include_dirs_missing_or_no_folder_is_an_error(repo, tmp_path):
    with pytest.raises(SourceError, match="Keiner der Include-Ordner"):
        scan(SourceConfig.model_validate(source(path=str(repo), includes=[{"dir": "nope", "ext": [".php"]}])), tmp_path)
    with pytest.raises(SourceError, match="Ordner der Quelle fehlt"):
        scan(SourceConfig.model_validate(source(path=str(tmp_path / "gone"))), tmp_path)


def test_relative_path_is_next_to_the_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_TEST_RAG", str(tmp_path / "x"))
    assert resolve_root(SourceConfig.model_validate(source(path="repos/a")), tmp_path) == tmp_path / "repos" / "a"
    assert resolve_root(SourceConfig.model_validate(source(path="$ACC_TEST_RAG/a")), Path("/else")) == tmp_path / "x" / "a"
