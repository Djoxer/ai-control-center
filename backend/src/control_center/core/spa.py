"""Serve the Angular build from the same process: one port, no second web server."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


def mount_spa(app: FastAPI, dist: Path) -> None:
    dist = dist.resolve()
    index = dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(404)                    # unknown API route stays a JSON 404, not index.html
        candidate = (dist / full_path).resolve()
        # path traversal guard: "/../../secrets.txt" must not escape the dist folder
        if candidate.is_file() and candidate.is_relative_to(dist):
            return FileResponse(candidate)
        if not index.is_file():
            raise HTTPException(404, "frontend build missing")
        return FileResponse(index)                      # Angular router handles /dashboard, /logs, ...
