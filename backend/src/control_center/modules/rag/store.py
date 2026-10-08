"""Vector store access: Qdrant over its REST API, or an in-memory store for the second PC.

REST via httpx instead of the qdrant-client package: httpx is already there (Ollama, probes), the
calls needed are few and documented, and tests fake them with respx or the memory store. The client
package would pull grpcio, numpy and protobuf into the control center for the same eight calls.

Calls (Qdrant >= 1.10 for /points/query, the call mcp_server.py's query_points() makes as well):
    GET    /                                   version
    GET    /collections                        names
    GET    /collections/{c}                    points, vector size, status (404 = missing)
    PUT    /collections/{c}                    create
    DELETE /collections/{c}                    delete
    PUT    /collections/{c}/points?wait=true   upsert
    POST   /collections/{c}/points/scroll      all point IDs (paged)
    POST   /collections/{c}/points/delete      delete point IDs
    POST   /collections/{c}/points/query       nearest neighbours
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from control_center.adapters.common import describe

PointId = int | str                             # Qdrant: unsigned int or UUID string
SCROLL_PAGE = 1000
DELETE_BATCH = 500


class StoreUnavailable(Exception):
    """Qdrant does not answer (down, wrong address, timeout). German message."""


class StoreError(Exception):
    """Qdrant answered with an error (bad request, dimension mismatch ...). German message."""


class CollectionMissing(StoreError):
    pass


@dataclass(frozen=True)
class CollectionStats:
    name: str
    status: str | None              # green | yellow | grey | red (Qdrant's optimizer state)
    points: int | None
    indexed_vectors: int | None
    segments: int | None
    vector_size: int | None         # None = named vectors or unknown layout (not written by us)
    distance: str | None


@dataclass(frozen=True)
class Point:
    id: PointId
    vector: list[float]
    payload: dict[str, Any]


@dataclass(frozen=True)
class Hit:
    id: PointId
    score: float
    payload: dict[str, Any]


class VectorStore(Protocol):
    mode: str                                   # "qdrant" | "memory"
    url: str | None

    async def version(self) -> str: ...
    async def collections(self) -> list[str]: ...
    async def stats(self, name: str) -> CollectionStats: ...          # CollectionMissing if absent
    async def create(self, name: str, size: int, distance: str) -> None: ...
    async def drop(self, name: str) -> bool: ...                     # False = did not exist
    async def upsert(self, name: str, points: list[Point]) -> None: ...
    async def point_ids(self, name: str) -> list[PointId]: ...
    async def delete_points(self, name: str, ids: list[PointId]) -> None: ...
    async def query(self, name: str, vector: list[float], limit: int) -> list[Hit]: ...


# ---- Qdrant (REST) --------------------------------------------------------------------------------

def _stats_from(name: str, result: dict[str, Any]) -> CollectionStats:
    vectors = (((result.get("config") or {}).get("params") or {}).get("vectors")) or {}
    single = isinstance(vectors, dict) and "size" in vectors     # named vectors: {"text": {"size": …}}
    return CollectionStats(
        name=name, status=result.get("status"), points=result.get("points_count"),
        indexed_vectors=result.get("indexed_vectors_count"), segments=result.get("segments_count"),
        vector_size=vectors.get("size") if single else None, distance=vectors.get("distance") if single else None,
    )


class QdrantStore:
    mode = "qdrant"

    def __init__(self, http: httpx.AsyncClient, url: str, timeout_s: float, api_key: str | None = None) -> None:
        self.http = http
        self.url = url.rstrip("/")
        self.timeout = timeout_s
        self.headers = {"api-key": api_key} if api_key else {}

    async def _call(self, method: str, path: str, json: Any = None, missing_ok: bool = False) -> Any:
        try:
            r = await self.http.request(method, f"{self.url}{path}", json=json, headers=self.headers,
                                        timeout=self.timeout)
        except httpx.TimeoutException as exc:
            raise StoreUnavailable(f"Qdrant antwortet nicht innerhalb von {self.timeout:g} s ({self.url})") from exc
        except httpx.HTTPError as exc:
            raise StoreUnavailable(f"Qdrant nicht erreichbar ({self.url}): {describe(exc)}") from exc
        try:
            body = r.json()
        except ValueError:
            body = None
        if r.status_code == 404:
            if missing_ok:
                return None
            raise CollectionMissing(_qdrant_error(body) or f"Nicht gefunden: {path}")
        if r.status_code in (401, 403):
            raise StoreUnavailable("Qdrant verweigert den Zugriff – API-Key (qdrant_api_key) prüfen")
        if r.status_code >= 400 or not isinstance(body, dict):
            raise StoreError(f"Qdrant-Fehler {r.status_code}: {_qdrant_error(body) or r.text[:200]}")
        return body.get("result")

    @staticmethod
    def _c(name: str) -> str:
        return "/collections/" + quote(name, safe="")

    async def version(self) -> str:
        try:
            r = await self.http.get(self.url + "/", headers=self.headers, timeout=self.timeout)
            data = r.json()
        except httpx.HTTPError as exc:
            raise StoreUnavailable(f"Qdrant nicht erreichbar ({self.url}): {describe(exc)}") from exc
        except ValueError:
            raise StoreUnavailable(f"Unter {self.url} antwortet kein Qdrant") from None
        if not isinstance(data, dict) or "version" not in data:
            raise StoreUnavailable(f"Unter {self.url} antwortet kein Qdrant")
        return str(data["version"])

    async def collections(self) -> list[str]:
        result = await self._call("GET", "/collections")
        return sorted(c["name"] for c in (result or {}).get("collections", []))

    async def stats(self, name: str) -> CollectionStats:
        return _stats_from(name, await self._call("GET", self._c(name)) or {})

    async def create(self, name: str, size: int, distance: str) -> None:
        await self._call("PUT", self._c(name), {"vectors": {"size": size, "distance": distance}})

    async def drop(self, name: str) -> bool:
        return bool(await self._call("DELETE", self._c(name), missing_ok=True))

    async def upsert(self, name: str, points: list[Point]) -> None:
        body = {"points": [{"id": p.id, "vector": p.vector, "payload": p.payload} for p in points]}
        await self._call("PUT", self._c(name) + "/points?wait=true", body)

    async def point_ids(self, name: str) -> list[PointId]:
        ids: list[PointId] = []
        offset: PointId | None = None
        while True:
            body: dict[str, Any] = {"limit": SCROLL_PAGE, "with_payload": False, "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            result = await self._call("POST", self._c(name) + "/points/scroll", body) or {}
            ids.extend(p["id"] for p in result.get("points", []))
            offset = result.get("next_page_offset")
            if offset is None:
                return ids

    async def delete_points(self, name: str, ids: list[PointId]) -> None:
        for i in range(0, len(ids), DELETE_BATCH):
            await self._call("POST", self._c(name) + "/points/delete?wait=true", {"points": ids[i:i + DELETE_BATCH]})

    async def query(self, name: str, vector: list[float], limit: int) -> list[Hit]:
        result = await self._call("POST", self._c(name) + "/points/query",
                                  {"query": vector, "limit": limit, "with_payload": True}) or {}
        return [Hit(p["id"], float(p.get("score", 0.0)), p.get("payload") or {}) for p in result.get("points", [])]


def _qdrant_error(body: Any) -> str | None:
    if isinstance(body, dict):
        status = body.get("status")
        if isinstance(status, dict) and isinstance(status.get("error"), str):
            return status["error"]
    return None


# ---- in memory (second PC, tests) -----------------------------------------------------------------

@dataclass
class _MemCollection:
    size: int
    distance: str
    points: dict[PointId, tuple[list[float], dict[str, Any]]] = field(default_factory=dict)


class MemoryStore:
    """Behaves like the Qdrant calls above (same errors), keeps everything in a dict. Lost on restart."""
    mode = "memory"
    url = None

    def __init__(self) -> None:
        self.data: dict[str, _MemCollection] = {}

    def _get(self, name: str) -> _MemCollection:
        try:
            return self.data[name]
        except KeyError:
            raise CollectionMissing(f"Not found: Collection `{name}` doesn't exist!") from None

    async def version(self) -> str:
        return "memory"

    async def collections(self) -> list[str]:
        return sorted(self.data)

    async def stats(self, name: str) -> CollectionStats:
        c = self._get(name)
        n = len(c.points)
        return CollectionStats(name, "green", n, n, 1, c.size, c.distance)

    async def create(self, name: str, size: int, distance: str) -> None:
        if name in self.data:
            raise StoreError(f"Qdrant-Fehler 409: Collection `{name}` already exists!")
        self.data[name] = _MemCollection(size, distance)

    async def drop(self, name: str) -> bool:
        return self.data.pop(name, None) is not None

    async def upsert(self, name: str, points: list[Point]) -> None:
        c = self._get(name)
        for p in points:
            if len(p.vector) != c.size:
                raise StoreError(f"Qdrant-Fehler 400: Wrong input: Vector dimension error: "
                                 f"expected dim: {c.size}, got {len(p.vector)}")
        for p in points:
            c.points[p.id] = (list(p.vector), dict(p.payload))

    async def point_ids(self, name: str) -> list[PointId]:
        return list(self._get(name).points)

    async def delete_points(self, name: str, ids: list[PointId]) -> None:
        c = self._get(name)
        for i in ids:
            c.points.pop(i, None)

    async def query(self, name: str, vector: list[float], limit: int) -> list[Hit]:
        c = self._get(name)
        if len(vector) != c.size:
            raise StoreError(f"Qdrant-Fehler 400: Wrong input: Vector dimension error: "
                             f"expected dim: {c.size}, got {len(vector)}")
        scored = [Hit(pid, _cosine(vector, vec), payload) for pid, (vec, payload) in c.points.items()]
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:limit]


def _cosine(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return 0.0 if na == 0 or nb == 0 else sum(x * y for x, y in zip(a, b)) / (na * nb)
