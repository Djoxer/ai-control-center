"""Serve the Angular build from the same process: one port, no second web server.

Caching, because a rebuild deletes the old bundles:
- Files with a content hash in the name (main-QJJNGQFR.js) never change -> cached for a year.
- Everything else (index.html, icons.svg, favicon) -> "no-cache": the browser keeps a copy but asks
  first (a cheap 304 via ETag). Without this, a cached index.html keeps pointing at last week's
  main-*.js after a rebuild, and the page stays blank.
- A missing file that looks like an asset (.js, .css, .png ...) is a real 404. Falling back to
  index.html there would hand HTML to a <script> tag - the browser refuses it, again a blank page.
Routes with dots stay routes: /catalog/qwen2.5-coder:14b has the "extension" .5-coder:14b, not an asset one.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

log = logging.getLogger("control_center.core.spa")

CACHE_IMMUTABLE = "public, max-age=31536000, immutable"
CACHE_REVALIDATE = "no-cache"
# Angular's esbuild output: name-HASH.ext with an 8-character upper-case hash (main-QJJNGQFR.js, chunk-..., media/...)
_HASHED = re.compile(r"-[A-Z0-9]{8}\.[a-z0-9]+$")
ASSET_SUFFIXES = frozenset({
    ".js", ".mjs", ".css", ".map", ".json", ".txt", ".webmanifest",
    ".ico", ".png", ".svg", ".jpg", ".jpeg", ".gif", ".webp", ".avif",
    ".woff", ".woff2", ".ttf", ".otf",
})


def cache_control(file_name: str) -> str:
    return CACHE_IMMUTABLE if _HASHED.search(file_name) else CACHE_REVALIDATE


def looks_like_asset(path: str) -> bool:
    return Path(path).suffix.lower() in ASSET_SUFFIXES


def etag_matches(if_none_match: str | None, etag: str | None) -> bool:
    """If-None-Match may list several tags ('"a", "b"') or '*'; weak tags (W/"a") count as equal."""
    if not if_none_match or not etag:
        return False
    def strip(tag: str) -> str:
        return tag.strip().removeprefix("W/")
    tags = {strip(t) for t in if_none_match.split(",")}
    return "*" in tags or strip(etag) in tags


def file_response(request: Request, path: Path, cache: str) -> Response:
    """FileResponse plus the 304 that Starlette's FileResponse does not do on its own."""
    resp = FileResponse(path, stat_result=path.stat(), headers={"Cache-Control": cache})   # stat -> ETag now
    if etag_matches(request.headers.get("if-none-match"), resp.headers.get("etag")):
        return Response(status_code=304, headers={"ETag": resp.headers["etag"], "Cache-Control": cache})
    return resp


def mount_spa(app: FastAPI, dist: Path) -> None:
    dist = dist.resolve()
    index = dist / "index.html"
    if index.is_file():
        log.info("serving frontend from %s", dist)
    else:
        # not fatal: the API works without it; the pages answer 404 "frontend build missing"
        log.warning("frontend_dist has no index.html: %s - run 'npm run build' in frontend/", dist)

    # HEAD too: uptime checks and curl -I should see the same status and headers as a browser
    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa(full_path: str, request: Request) -> Response:
        if full_path.startswith("api/"):
            raise HTTPException(404)                    # unknown API route stays a JSON 404, not index.html
        candidate = (dist / full_path).resolve()
        # path traversal guard: "/../../secrets.txt" must not escape the dist folder
        if candidate.is_file() and candidate.is_relative_to(dist):
            return file_response(request, candidate, cache_control(candidate.name))
        if looks_like_asset(full_path):
            raise HTTPException(404)                    # stale bundle name after a rebuild: 404, not HTML
        if not index.is_file():
            raise HTTPException(404, "frontend build missing")
        # Angular router handles /dashboard, /logs, ...
        return file_response(request, index, CACHE_REVALIDATE)
