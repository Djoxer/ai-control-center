def test_spa_fallback_and_guards(make_client, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<app-root></app-root>", encoding="utf-8")
    (dist / "main.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    with make_client(frontend_dist=dist) as c:
        assert c.get("/main.js").text == "console.log(1)"
        assert "app-root" in c.get("/dashboard").text            # deep link -> index.html
        assert c.get("/api/v1/nope").status_code == 404          # API stays JSON 404
        assert "nope" not in c.get("/..%2Fsecret.txt").text     # traversal blocked
        assert c.get("/api/v1/health").status_code == 200        # core routes win over catch-all
