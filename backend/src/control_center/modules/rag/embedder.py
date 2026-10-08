"""Text -> vector. The SAME call as mcp_server.py: POST /api/embeddings {model, prompt}, no prefix.

If indexing and searching embedded differently, the vectors of documents and questions would live in
different spaces and the search would quietly return nonsense. nomic-embed-text's recommended
prefixes ("search_document: ", "search_query: ") and the batch endpoint /api/embed are deliberately
NOT used yet: both change the vectors, so they belong to the measured quality step, not here.

Note for the AI box: with OLLAMA_MAX_LOADED_MODELS=1 every embedding request makes Ollama unload the
chat model that is loaded at the moment (and reload it on the next chat).
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import re
from typing import Protocol

import httpx

from control_center.adapters.common import describe

FAKE_DIM = 256                                  # small, enough for a lexical demo search


class EmbedUnavailable(Exception):
    """Ollama cannot embed at all right now (down, timeout, model missing) -> a running job stops."""


class EmbedError(Exception):
    """This one text failed (Ollama answered with an error) -> the file is skipped, the job goes on."""


class Embedder(Protocol):
    mode: str                                   # "ollama" | "fake"
    url: str | None
    model: str

    async def embed(self, text: str) -> list[float]: ...


class OllamaEmbedder:
    mode = "ollama"

    def __init__(self, http: httpx.AsyncClient, url: str, model: str, timeout_s: float) -> None:
        self.http = http
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout_s

    async def embed(self, text: str) -> list[float]:
        try:
            r = await self.http.post(f"{self.url}/api/embeddings", json={"model": self.model, "prompt": text},
                                     timeout=self.timeout)
        except httpx.TimeoutException as exc:
            raise EmbedUnavailable(f"Ollama antwortet nicht innerhalb von {self.timeout:g} s ({self.url})") from exc
        except httpx.HTTPError as exc:
            raise EmbedUnavailable(f"Ollama nicht erreichbar ({self.url}): {describe(exc)}") from exc
        error = _error_text(r)
        if error and "not found" in error.lower() and "model" in error.lower():
            raise EmbedUnavailable(f"Modell „{self.model}“ fehlt in Ollama – einmal „ollama pull {self.model}“ "
                                   f"auf dem AI-Rechner ausführen")
        if r.status_code == 404:                # no Ollama behind this address (or a very old one)
            raise EmbedUnavailable(f"{self.url}/api/embeddings gibt 404 – zeigt ollama_url auf Ollama?")
        if r.status_code >= 400:
            raise EmbedError(f"Ollama-Fehler {r.status_code}: {error or r.text[:200]}")
        try:
            vector = r.json()["embedding"]
        except (ValueError, KeyError, TypeError):
            raise EmbedError(f"Antwort ohne „embedding“: {r.text[:200]}") from None
        if not isinstance(vector, list) or not vector:
            raise EmbedError("Ollama lieferte einen leeren Vektor")
        return vector


class FakeEmbedder:
    """Deterministic word hashing (no model): texts sharing words get similar vectors.

    Good enough to click through indexing and test search on the second PC. Not comparable with
    nomic-embed-text in any way - never point it at the real collections (different vector size).
    """
    mode = "fake"
    url = None

    def __init__(self, model: str = "fake-hash-256", delay_s: float = 0.0) -> None:
        self.model = model
        self.delay_s = delay_s

    async def embed(self, text: str) -> list[float]:
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return fake_vector(text)


_WORD = re.compile(r"[a-z_][a-z0-9_]{1,}|\d{2,}")


def fake_vector(text: str, dim: int = FAKE_DIM) -> list[float]:
    vec = [0.0] * dim
    for word in _WORD.findall(text.lower()):
        h = int.from_bytes(hashlib.blake2b(word.encode(), digest_size=8).digest(), "big")
        vec[h % dim] += 1.0 if (h >> 32) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        vec[0] = 1.0                            # text without words: still a valid unit vector
        return vec
    return [v / norm for v in vec]


def _error_text(r: httpx.Response) -> str | None:
    try:
        data = r.json()
    except ValueError:
        return None
    return data.get("error") if isinstance(data, dict) and isinstance(data.get("error"), str) else None
