"""Qdrant REST calls and the Ollama embedding call, against recorded answer shapes (respx)."""
import asyncio
import json

import httpx
import pytest
import respx

from control_center.modules.rag.embedder import (
    EmbedError,
    EmbedUnavailable,
    FakeEmbedder,
    OllamaEmbedder,
    fake_vector,
)
from control_center.modules.rag.service import is_local_host
from control_center.modules.rag.store import (
    CollectionMissing,
    Point,
    QdrantStore,
    StoreError,
    StoreUnavailable,
)

QDRANT = "http://qdrant.test:6333"
OLLAMA = "http://ollama.test:11434"


def qdrant(api_key=None) -> QdrantStore:
    return QdrantStore(httpx.AsyncClient(), QDRANT, 5, api_key)


def ok(result):
    return httpx.Response(200, json={"result": result, "status": "ok", "time": 0.001})


# ---- Qdrant ---------------------------------------------------------------------------------------

@respx.mock
def test_stats_reads_the_collection_info():
    respx.get(f"{QDRANT}/collections/bent_php").mock(return_value=ok({
        "status": "green", "points_count": 124, "indexed_vectors_count": 0, "segments_count": 2,
        "config": {"params": {"vectors": {"size": 768, "distance": "Cosine"}}}}))
    s = asyncio.run(qdrant().stats("bent_php"))
    assert (s.points, s.vector_size, s.distance, s.status) == (124, 768, "Cosine", "green")


@respx.mock
def test_named_vectors_have_no_single_size():
    respx.get(f"{QDRANT}/collections/multi").mock(return_value=ok({
        "status": "green", "points_count": 1, "config": {"params": {"vectors": {"text": {"size": 768, "distance": "Dot"}}}}}))
    assert asyncio.run(qdrant().stats("multi")).vector_size is None


@respx.mock
def test_missing_collection_and_german_errors():
    respx.get(f"{QDRANT}/collections/nope").mock(return_value=httpx.Response(
        404, json={"status": {"error": "Not found: Collection `nope` doesn't exist!"}, "time": 0}))
    with pytest.raises(CollectionMissing, match="doesn't exist"):
        asyncio.run(qdrant().stats("nope"))
    respx.put(f"{QDRANT}/collections/x/points?wait=true").mock(return_value=httpx.Response(
        400, json={"status": {"error": "Wrong input: Vector dimension error: expected dim: 4, got 3"}}))
    with pytest.raises(StoreError, match="Qdrant-Fehler 400: Wrong input"):
        asyncio.run(qdrant().upsert("x", [Point(1, [1.0, 0.0, 0.0], {})]))
    respx.get(f"{QDRANT}/collections").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(StoreUnavailable, match="Qdrant nicht erreichbar"):
        asyncio.run(qdrant().collections())


@respx.mock
def test_scroll_follows_the_pages():
    pages = iter([ok({"points": [{"id": 0}, {"id": 1}], "next_page_offset": "5c56c793-69f3-4fbf-87e6-c4bf54c28c26"}),
                  ok({"points": [{"id": "5c56c793-69f3-4fbf-87e6-c4bf54c28c26"}], "next_page_offset": None})])
    route = respx.post(f"{QDRANT}/collections/c/points/scroll").mock(side_effect=lambda req: next(pages))
    assert asyncio.run(qdrant().point_ids("c")) == [0, 1, "5c56c793-69f3-4fbf-87e6-c4bf54c28c26"]
    bodies = [json.loads(c.request.content) for c in route.calls]
    assert "offset" not in bodies[0] and bodies[1]["offset"] == "5c56c793-69f3-4fbf-87e6-c4bf54c28c26"
    assert bodies[0]["with_payload"] is False and bodies[0]["with_vector"] is False


@respx.mock
def test_delete_points_in_batches_and_query_like_query_points():
    route = respx.post(f"{QDRANT}/collections/c/points/delete?wait=true").mock(return_value=ok({"status": "completed"}))
    asyncio.run(qdrant().delete_points("c", list(range(1200))))
    assert [len(json.loads(c.request.content)["points"]) for c in route.calls] == [500, 500, 200]
    q = respx.post(f"{QDRANT}/collections/c/points/query").mock(return_value=ok({"points": [
        {"id": 3, "version": 1, "score": 0.81, "payload": {"filename": "src/A.php", "text": "x"}}]}))
    hits = asyncio.run(qdrant().query("c", [0.1, 0.2], 8))
    assert json.loads(q.calls[0].request.content) == {"query": [0.1, 0.2], "limit": 8, "with_payload": True}
    assert (hits[0].id, hits[0].score, hits[0].payload["filename"]) == (3, 0.81, "src/A.php")


@respx.mock
def test_api_key_header_and_version():
    route = respx.get(f"{QDRANT}/").mock(return_value=httpx.Response(200, json={"title": "qdrant", "version": "1.19.2"}))
    assert asyncio.run(qdrant("k3y").version()) == "1.19.2"
    assert route.calls[0].request.headers["api-key"] == "k3y"
    respx.get(f"{QDRANT}/collections").mock(return_value=httpx.Response(401, text="unauthorized"))
    with pytest.raises(StoreUnavailable, match="API-Key"):
        asyncio.run(qdrant().collections())


@respx.mock
def test_collection_names_are_url_quoted():
    route = respx.get(f"{QDRANT}/collections/a%2Fb").mock(return_value=ok({"status": "green", "config": {}}))
    asyncio.run(qdrant().stats("a/b"))
    assert route.called


# ---- Ollama ---------------------------------------------------------------------------------------

def ollama() -> OllamaEmbedder:
    return OllamaEmbedder(httpx.AsyncClient(), OLLAMA, "nomic-embed-text", 5)


@respx.mock
def test_embedding_request_is_exactly_the_one_of_mcp_server():
    route = respx.post(f"{OLLAMA}/api/embeddings").mock(return_value=httpx.Response(200, json={"embedding": [0.1, 0.2]}))
    assert asyncio.run(ollama().embed("search_query? no prefix")) == [0.1, 0.2]
    # same endpoint, same two fields, no "search_document:" prefix - otherwise query and index would not match
    assert json.loads(route.calls[0].request.content) == {"model": "nomic-embed-text", "prompt": "search_query? no prefix"}


@respx.mock
@pytest.mark.parametrize("response, exc, text", [
    (httpx.Response(404, json={"error": "model \"nomic-embed-text\" not found, try pulling it first"}),
     EmbedUnavailable, "ollama pull nomic-embed-text"),
    (httpx.Response(404, text="404 page not found"), EmbedUnavailable, "ollama_url"),
    (httpx.Response(500, json={"error": "input too long"}), EmbedError, "Ollama-Fehler 500: input too long"),
    (httpx.Response(200, json={"oops": 1}), EmbedError, "ohne „embedding“"),
    (httpx.Response(200, json={"embedding": []}), EmbedError, "leeren Vektor"),
])
def test_embedding_errors(response, exc, text):
    respx.post(f"{OLLAMA}/api/embeddings").mock(return_value=response)
    with pytest.raises(exc, match=text):
        asyncio.run(ollama().embed("x"))


@respx.mock
def test_ollama_down_or_slow_is_unavailable():
    respx.post(f"{OLLAMA}/api/embeddings").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(EmbedUnavailable, match="nicht erreichbar"):
        asyncio.run(ollama().embed("x"))
    respx.post(f"{OLLAMA}/api/embeddings").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(EmbedUnavailable, match="nicht innerhalb"):
        asyncio.run(ollama().embed("x"))


def test_fake_vectors_are_deterministic_unit_vectors():
    a, b = fake_vector("class NoteRepository"), fake_vector("class NoteRepository")
    assert a == b and abs(sum(x * x for x in a) - 1) < 1e-9
    assert fake_vector("") != [0.0] * len(a)
    assert asyncio.run(FakeEmbedder().embed("x")) == fake_vector("x")


# ---- which Qdrant may be written to ---------------------------------------------------------------

@pytest.mark.parametrize("host, local", [
    ("localhost", True), ("127.0.0.1", True), ("[::1]", True), ("::1", True),
    ("192.0.2.10", False),                         # TEST-NET, never a local interface
    ("no-such-host.invalid", False),               # unknown name: the safe side
])
def test_is_local_host(host, local):
    assert is_local_host(host) is local


def test_own_interface_address_counts_as_local():
    import socket

    import psutil
    own = next((a.address for addrs in psutil.net_if_addrs().values() for a in addrs
                if a.family == socket.AF_INET and not a.address.startswith("127.")), None)
    if own is None:
        pytest.skip("no non-loopback IPv4 address on this machine")
    assert is_local_host(own) is True
