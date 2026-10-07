"""Request guards for endpoints that change something (start/stop a process; later reindex, delete).

There is no login yet, so anyone in the LAN can call these endpoints with curl - that is a known,
accepted gap until auth via OpenWebUI arrives. What this guard closes is the other door: a web page
on some other site that makes the BROWSER of someone in the LAN send the request (cross-site request
forgery). A plain form post needs no CORS approval, so CORS alone does not stop it.

Browsers label such requests themselves: `Sec-Fetch-Site` (all current browsers) and `Origin`
(every cross-origin POST). Scripts send neither and stay allowed. This protects browsers; it is not
authentication.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import HTTPException, Request

ALLOWED_FETCH_SITES = {"same-origin", "none"}   # "none" = typed into the address bar / bookmark


def same_origin(request: Request) -> None:
    """FastAPI dependency: 403 for write requests a browser sends on behalf of another site."""
    origin = request.headers.get("origin")
    allowed_origins = request.app.state.ctx.settings.cors_origins     # "ng serve" without proxy, if configured
    if origin is not None and origin in allowed_origins:
        return
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        # the browser compared page and target itself - more reliable than our Host comparison
        if site in ALLOWED_FETCH_SITES:
            return
        raise HTTPException(403, "Aktion abgelehnt: Die Anfrage kam von einer fremden Seite.")
    if origin is None:
        return                                      # curl, scripts, very old browsers
    # older browser without Sec-Fetch-*: the page's origin must be this server (dev proxy keeps Host)
    if urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower():
        return
    raise HTTPException(403, "Aktion abgelehnt: Die Anfrage kam von einer fremden Seite.")
