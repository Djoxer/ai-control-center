import logging

from control_center.core.spa import (
    CACHE_IMMUTABLE,
    CACHE_REVALIDATE,
    cache_control,
    etag_matches,
    looks_like_asset,
)


def _dist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<app-root></app-root>", encoding="utf-8")
    (dist / "main.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "main-QJJNGQFR.js").write_text("console.log(2)", encoding="utf-8")
    (dist / "icons.svg").write_text("<svg/>", encoding="utf-8")
    return dist


def test_spa_fallback_and_guards(make_client, tmp_path):
    dist = _dist(tmp_path)
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    with make_client(frontend_dist=dist) as c:
        assert c.get("/main.js").text == "console.log(1)"
        assert "app-root" in c.get("/dashboard").text            # deep link -> index.html
        assert c.get("/api/v1/nope").status_code == 404          # API stays JSON 404
        assert "nope" not in c.get("/..%2Fsecret.txt").text     # traversal blocked
        assert c.get("/api/v1/health").status_code == 200        # core routes win over catch-all


def test_cache_headers(make_client, tmp_path):
    with make_client(frontend_dist=_dist(tmp_path)) as c:
        assert c.get("/main-QJJNGQFR.js").headers["cache-control"] == CACHE_IMMUTABLE
        assert c.get("/icons.svg").headers["cache-control"] == CACHE_REVALIDATE      # may change, not hashed
        assert c.get("/").headers["cache-control"] == CACHE_REVALIDATE
        assert c.get("/dashboard").headers["cache-control"] == CACHE_REVALIDATE      # index via fallback
        etag = c.get("/").headers["etag"]
        assert c.get("/", headers={"If-None-Match": etag}).status_code == 304        # revalidation is cheap
        head = c.head("/main-QJJNGQFR.js")
        assert head.status_code == 200 and head.headers["cache-control"] == CACHE_IMMUTABLE and not head.content


def test_stale_bundle_is_404_not_html(make_client, tmp_path):
    with make_client(frontend_dist=_dist(tmp_path)) as c:
        r = c.get("/main-OLDHASH1.js")                          # name from a cached index.html of an older build
        assert r.status_code == 404 and "app-root" not in r.text
        assert c.get("/media/font-ABCDEFGH.woff2").status_code == 404
        # a route parameter with a dot is still a route, not an asset
        assert "app-root" in c.get("/catalog/qwen2.5-coder:14b-instruct-q4_K_M").text


def test_missing_build_is_logged_and_404(make_client, tmp_path, caplog):
    empty = tmp_path / "empty"
    empty.mkdir()
    spa_log = logging.getLogger("control_center.core.spa")
    spa_log.addHandler(caplog.handler)               # app logging sets propagate=False, caplog sits on root
    try:
        with make_client(frontend_dist=empty) as c:
            assert c.get("/dashboard").status_code == 404
            assert c.get("/api/v1/health").status_code == 200
    finally:
        spa_log.removeHandler(caplog.handler)
    assert any("no index.html" in r.getMessage() for r in caplog.records)


def test_helpers():
    assert cache_control("chunk-AB12CD34.js") == CACHE_IMMUTABLE
    assert cache_control("logo-text.png") == CACHE_REVALIDATE     # lower-case word, not a hash
    assert cache_control("index.html") == CACHE_REVALIDATE
    assert looks_like_asset("main-X.js") and looks_like_asset("a/b/FONT.WOFF2")
    assert not looks_like_asset("dashboard") and not looks_like_asset("catalog/qwen2.5-coder:14b")


def test_etag_matching():
    assert etag_matches('"abc"', '"abc"')
    assert etag_matches('"x", "abc"', '"abc"')
    assert etag_matches('W/"abc"', '"abc"')
    assert etag_matches("*", '"abc"')
    assert not etag_matches('"x"', '"abc"') and not etag_matches(None, '"abc"')
