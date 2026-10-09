"""Model library adapter: names as people paste them, manifests, Range requests behind a redirect, the fake."""
import asyncio
import hashlib
import json

import httpx
import pytest
import respx

from control_center.adapters.library import (
    LIBRARY_SAMPLES_DIR, BadReference, FakeLibrary, HttpLibrary, LibraryNotFound, LibraryUnavailable, ModelRef,
    parse_manifest, parse_ref,
)

REG = "https://registry.ollama.ai/v2"
DIGEST = "sha256:" + "ab" * 32


@pytest.mark.parametrize(("text", "ref", "name"), [
    ("qwen3-coder:30b", ("registry.ollama.ai", "library", "qwen3-coder", "30b"), "qwen3-coder:30b"),
    ("gemma3", ("registry.ollama.ai", "library", "gemma3", "latest"), "gemma3:latest"),
    ("  Qwen3-Coder:30B  ", ("registry.ollama.ai", "library", "qwen3-coder", "30b"), "qwen3-coder:30b"),
    ("ollama pull devstral:24b", ("registry.ollama.ai", "library", "devstral", "24b"), "devstral:24b"),
    ("ollama run qwen3.5:9b", ("registry.ollama.ai", "library", "qwen3.5", "9b"), "qwen3.5:9b"),
    ("someone/coder:7b", ("registry.ollama.ai", "someone", "coder", "7b"), "someone/coder:7b"),
    ("registry.ollama.ai/library/qwen3:8b", ("registry.ollama.ai", "library", "qwen3", "8b"), "qwen3:8b"),
    ("hf.co/Org/Repo-GGUF:Q4_K_M", ("hf.co", "Org", "Repo-GGUF", "Q4_K_M"), "hf.co/Org/Repo-GGUF:Q4_K_M"),
    ("https://ollama.com/library/qwen3-coder:30b", ("registry.ollama.ai", "library", "qwen3-coder", "30b"),
     "qwen3-coder:30b"),
    ("https://ollama.com/library/qwen3-coder/tags", ("registry.ollama.ai", "library", "qwen3-coder", "latest"),
     "qwen3-coder:latest"),
    ("https://ollama.com/someone/coder", ("registry.ollama.ai", "someone", "coder", "latest"), "someone/coder:latest"),
    ("https://huggingface.co/Org/Repo-GGUF/blob/main/x.gguf", ("hf.co", "Org", "Repo-GGUF", "latest"),
     "hf.co/Org/Repo-GGUF:latest"),
])
def test_names_as_people_paste_them(text, ref, name):
    r = parse_ref(text)
    assert (r.host, r.namespace, r.model, r.tag) == ref and r.name == name


@pytest.mark.parametrize(("text", "message"), [
    ("", "Bitte einen Modellnamen"),
    ("   ", "Bitte einen Modellnamen"),
    ("qwen3 coder", "Leerzeichen"),
    ("a/b/c/d", "kein Modellname"),
    ("hf.co/model", "kein Modellname"),                      # host without namespace
    ("evil.example/x/y", "nicht freigegeben"),
    ("http://192.0.2.10/x/y", "nicht freigegeben"),           # the server must not fetch LAN addresses (RFC 5737 IP)
    ("qwen3-coder:", "Tag fehlt"),
    ("qwen$3:8b", "unzulässige Zeichen"),
    ("qwen3:8b?x=1", "unzulässige Zeichen"),
    ("_x/model", "unzulässige Zeichen"),
    ("Bad_Host!/a/b", "kein Registry-Host"),
])
def test_what_is_not_a_model_name(text, message):
    with pytest.raises(BadReference, match=message):
        parse_ref(text)


def test_only_the_configured_hosts():
    with pytest.raises(BadReference, match=r"erlaubt: registry.ollama.ai – \[adapters\] library_hosts"):
        parse_ref("hf.co/org/repo", hosts=["registry.ollama.ai"])
    assert parse_ref("my.registry:5000/team/coder:1", hosts=["my.registry:5000"]).name == "my.registry:5000/team/coder:1"


def test_web_pages_of_a_model():
    assert parse_ref("qwen3-coder:30b").page == "https://ollama.com/library/qwen3-coder:30b"
    assert parse_ref("someone/coder:7b").page == "https://ollama.com/someone/coder:7b"
    assert parse_ref("hf.co/Org/Repo:Q4_K_M").page == "https://huggingface.co/Org/Repo"
    assert ModelRef("my.registry", "a", "b", "c").page is None


def _manifest(**extra) -> bytes:
    return json.dumps({
        "schemaVersion": 2, "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
        "config": {"mediaType": "application/vnd.docker.container.image.v1+json", "digest": DIGEST, "size": 539},
        "layers": [
            {"mediaType": "application/vnd.ollama.image.model", "digest": DIGEST, "size": 18556688736,
             "from": "model.gguf"},
            {"mediaType": "application/vnd.ollama.image.projector", "digest": DIGEST, "size": 900},
            {"mediaType": "application/vnd.ollama.image.params", "digest": DIGEST, "size": 148},
            {"mediaType": "application/vnd.ollama.image.license", "digest": "md5:x", "size": 1},   # skipped
            {"mediaType": "text/plain", "digest": DIGEST, "size": 2},
            "garbage",
        ], **extra}).encode()


def test_manifest_layers_kinds_and_size():
    body = _manifest(runner="llamacpp", format="gguf", nested={"x": 1})
    m = parse_manifest(body)
    assert m.digest == "sha256:" + hashlib.sha256(body).hexdigest()
    assert [la.kind for la in m.layers] == ["model", "projector", "params", "text/plain"]
    assert m.first("model").size == 18556688736 and m.first("model").source == "model.gguf"
    assert m.first("template") is None and m.all("projector")[0].size == 900
    assert m.size == 18556688736 + 900 + 148 + 2 + 539                   # what /api/tags reports after the pull
    assert m.extra == {"runner": "llamacpp", "format": "gguf"}


@pytest.mark.parametrize(("body", "message"), [(b"<html>", "kein JSON"), (b'{"layers": 3}', "ohne Layer-Liste"),
                                               (b"[]", "ohne Layer-Liste")])
def test_unreadable_manifests(body, message):
    with pytest.raises(LibraryUnavailable, match=message):
        parse_manifest(body)


def _http():
    return HttpLibrary(httpx.AsyncClient())


@respx.mock
def test_manifest_request_asks_for_the_docker_format():
    route = respx.get(f"{REG}/library/qwen3-coder/manifests/30b").respond(content=_manifest())
    m = asyncio.run(_http().manifest(parse_ref("qwen3-coder:30b"), timeout=5))
    assert m.first("model").size == 18556688736
    assert route.calls[0].request.headers["Accept"] == "application/vnd.docker.distribution.manifest.v2+json"


@pytest.mark.parametrize("status", [401, 403, 404])
@respx.mock
def test_unknown_or_private_models(status):
    respx.get(f"{REG}/library/nope/manifests/1b").respond(status)
    with pytest.raises(LibraryNotFound, match="nope:1b gibt es in registry.ollama.ai nicht"):
        asyncio.run(_http().manifest(parse_ref("nope:1b"), timeout=5))


@respx.mock
def test_registry_trouble_is_unavailable():
    respx.get(f"{REG}/library/a/manifests/1").respond(503)
    respx.get(f"{REG}/library/b/manifests/1").mock(side_effect=httpx.ConnectError("no route"))
    respx.get(f"{REG}/library/c/manifests/1").respond(content=b"{" * (1024 * 1024 + 1))
    lib = _http()
    with pytest.raises(LibraryUnavailable, match="HTTP 503"):
        asyncio.run(lib.manifest(parse_ref("a:1"), timeout=5))
    with pytest.raises(LibraryUnavailable, match="nicht erreichbar: ConnectError: no route"):
        asyncio.run(lib.manifest(parse_ref("b:1"), timeout=5))
    with pytest.raises(LibraryUnavailable, match="unerwartet groß"):
        asyncio.run(lib.manifest(parse_ref("c:1"), timeout=5))


@respx.mock
def test_blob_follows_the_redirect_and_sends_the_range():
    """The registry answers 302 to a presigned storage URL; the Range header must arrive there."""
    storage = "https://storage.example/blob?sig=1"
    respx.get(f"{REG}/library/qwen3-coder/blobs/{DIGEST}").respond(302, headers={"Location": storage})
    target = respx.get(storage).respond(206, content=b"0123456789")
    data = asyncio.run(_http().blob(parse_ref("qwen3-coder:30b"), DIGEST, 100, 10, timeout=5))
    assert data == b"0123456789"
    assert target.calls[0].request.headers["Range"] == "bytes=100-109"


@respx.mock
def test_blob_without_range_support_skips_what_was_read_already():
    respx.get(f"{REG}/library/m/blobs/{DIGEST}").respond(200, content=bytes(range(256)) * 4)
    data = asyncio.run(_http().blob(parse_ref("m:1"), DIGEST, 300, 5, timeout=5))
    assert data == bytes([44, 45, 46, 47, 48])                           # bytes 300..304 of the whole file


@respx.mock
def test_blob_edges():
    respx.get(f"{REG}/library/end/blobs/{DIGEST}").respond(416)
    respx.get(f"{REG}/library/gone/blobs/{DIGEST}").respond(404)
    respx.get(f"{REG}/library/long/blobs/{DIGEST}").respond(206, content=b"x" * 50)
    respx.get(f"{REG}/library/cut/blobs/{DIGEST}").mock(side_effect=httpx.ReadTimeout("slow"))
    lib = _http()
    assert asyncio.run(lib.blob(parse_ref("end:1"), DIGEST, 10 ** 12, 10, timeout=5)) == b""
    assert asyncio.run(lib.blob(parse_ref("long:1"), DIGEST, 0, 8, timeout=5)) == b"x" * 8   # never more
    assert asyncio.run(lib.blob(parse_ref("long:1"), DIGEST, 0, 0, timeout=5)) == b""
    with pytest.raises(LibraryUnavailable, match="HTTP 404"):
        asyncio.run(lib.blob(parse_ref("gone:1"), DIGEST, 0, 8, timeout=5))
    with pytest.raises(LibraryUnavailable, match="ReadTimeout"):
        asyncio.run(lib.blob(parse_ref("cut:1"), DIGEST, 0, 8, timeout=5))


def test_fake_library_replays_the_samples():
    lib = FakeLibrary()
    m = asyncio.run(lib.manifest(parse_ref("qwen3-coder:30b"), timeout=5))
    weights = m.first("model")
    head = asyncio.run(lib.blob(parse_ref("qwen3-coder:30b"), weights.digest, 0, 8, timeout=5))
    assert head[:4] == b"GGUF" and len(head) == 8
    assert asyncio.run(lib.blob(parse_ref("qwen3-coder:30b"), weights.digest, 10 ** 9, 8, timeout=5)) == b""
    with pytest.raises(LibraryNotFound, match="nicht in index.json"):
        asyncio.run(lib.manifest(parse_ref("nope:1b"), timeout=5))
    with pytest.raises(LibraryUnavailable, match="fehlt"):
        asyncio.run(lib.blob(parse_ref("x:1"), "sha256:" + "00" * 32, 0, 8, timeout=5))


def test_fake_library_without_samples(tmp_path):
    with pytest.raises(LibraryUnavailable, match="index.json nicht lesbar"):
        asyncio.run(FakeLibrary(tmp_path).manifest(parse_ref("a:1"), timeout=5))
    (tmp_path / "index.json").write_text('{"a:1": "a.json"}')
    with pytest.raises(LibraryUnavailable, match="Manifest a.json fehlt"):
        asyncio.run(FakeLibrary(tmp_path).manifest(parse_ref("a:1"), timeout=5))


def test_library_samples_are_up_to_date():
    """library-samples is generated by tests/fixtures/library_samples.py - edit that, then run it."""
    import library_samples
    expected = library_samples.build()
    on_disk = {p.relative_to(LIBRARY_SAMPLES_DIR).as_posix(): p.read_bytes()
               for p in LIBRARY_SAMPLES_DIR.rglob("*") if p.is_file() and p.name not in ("README.md", ".gitattributes")}
    assert on_disk.keys() == expected.keys()
    assert [k for k in expected if on_disk[k] != expected[k]] == []
