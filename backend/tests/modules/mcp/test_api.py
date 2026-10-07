"""MCP module end to end: real app, real child processes (fake script and the demo MCP server)."""
import socket
import sys
import time
from pathlib import Path

import psutil
import pytest
from fastapi.testclient import TestClient

from control_center.main import create_app
from control_center.modules.mcp.process import ProcessRecord, save_record

FAKE = Path(__file__).parents[2] / "fixtures" / "mcp_fake.py"
FAST = {"poll_interval_s": 0.05, "check_interval_s": 1, "restart_backoff_s": 0.01,
        "stop_timeout_s": 3, "start_timeout_s": 20, "tools_timeout_s": 10}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fake(*args: str) -> list[str]:
    return ["{python}", str(FAKE), *args]


def demo(port: int) -> list[str]:
    return ["{python}", "-m", "control_center.modules.mcp.demo_server", "--port", str(port)]


@pytest.fixture
def make(settings, tmp_path):
    """make(servers=[...], **mcp_options) -> entered TestClient; all clients are closed after the test."""
    opened = []

    def _make(servers, **options):
        section = {**FAST, **options, "servers": servers}
        for s in servers:
            s.setdefault("cwd", str(tmp_path))
        mods = type(settings.modules).model_validate({"mcp": section})
        client = TestClient(create_app(settings.model_copy(update={"modules": mods})))
        client.__enter__()
        opened.append(client)
        return client

    yield _make
    for c in reversed(opened):
        c.__exit__(None, None, None)


def status(client, key):
    return client.get(f"/api/v1/mcp/servers/{key}").json()


def wait_for(client, key, pred, timeout=20.0):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = status(client, key)
        if pred(last):
            return last
        time.sleep(0.05)
    raise AssertionError(f"condition not reached, last status: {last}")


# ---- basics ----------------------------------------------------------------------------------

def test_module_runs_without_servers(make):
    c = make([])
    states = {m["key"]: m["state"] for m in c.get("/api/v1/meta/modules").json()}
    assert states["mcp"] == "running"
    assert c.get("/api/v1/mcp/servers").json() == []


def test_unknown_server_and_missing_url(make):
    c = make([{"key": "sleepy", "title": "Sleepy", "command": fake("sleep")}])
    assert c.get("/api/v1/mcp/servers/nope").status_code == 404
    assert c.post("/api/v1/mcp/servers/nope/start").status_code == 404
    r = c.get("/api/v1/mcp/servers/sleepy/tools")
    assert r.status_code == 409 and "url" in r.json()["detail"]


def test_invalid_config_fails_only_this_module(settings):
    mods = type(settings.modules).model_validate({"mcp": {"servers": [
        {"key": "a", "title": "A", "command": ["x"], "url": "http://127.0.0.1:9000/mcp"},
        {"key": "b", "title": "B", "command": ["y"], "url": "http://127.0.0.1:9000/mcp"},
    ]}})
    with TestClient(create_app(settings.model_copy(update={"modules": mods}))) as c:
        mcp = next(m for m in c.get("/api/v1/health").json()["modules"] if m["key"] == "mcp")
        assert mcp["state"] == "failed" and "same port" in mcp["error"]
        assert c.get("/api/v1/mcp/servers").status_code == 503
        assert c.get("/api/v1/dashboard/snapshot").status_code == 200        # the others keep running


# ---- start, ready, tools, stop with a real MCP server ----------------------------------------

def test_demo_server_lifecycle_with_tools(make):
    port = free_port()
    c = make([{"key": "demo", "title": "Demo", "command": demo(port), "url": f"http://127.0.0.1:{port}/mcp"}])
    first = c.post("/api/v1/mcp/servers/demo/start").json()
    assert first["state"] in ("starting", "running") and first["pid"]
    assert sys.executable in first["commandLine"]                     # {python} resolved

    st = wait_for(c, "demo", lambda s: s["state"] == "running" and s["tools"] is not None)
    assert st["port"]["open"] is True and st["port"]["managed"] is True
    tools = st["tools"]
    assert tools["error"] is None and tools["serverName"] == "acc-demo"
    assert [t["name"] for t in tools["tools"]] == ["echo", "add", "server_time"]
    add = next(t for t in tools["tools"] if t["name"] == "add")
    assert [(p["name"], p["type"], p["required"], p["default"]) for p in add["params"]] == [
        ("a", "integer", True, None), ("b", "integer", False, "1")]

    again = c.post("/api/v1/mcp/servers/demo/start").json()           # idempotent
    assert again["pid"] == st["pid"]

    fresh = c.get("/api/v1/mcp/servers/demo/tools").json()
    assert len(fresh["tools"]) == 3

    stopped = c.post("/api/v1/mcp/servers/demo/stop").json()
    assert stopped["state"] == "stopped" and stopped["pid"] is None
    assert not psutil.pid_exists(st["pid"]) or psutil.Process(st["pid"]).status() == psutil.STATUS_ZOMBIE
    assert stopped["port"]["open"] is False

    # the output and the supervisor lines are a source on the logs page
    sources = {s["key"]: s for s in c.get("/api/v1/logs/sources").json()}
    assert sources["mcp-demo"]["title"] == "MCP: Demo" and sources["mcp-demo"]["available"]
    entries = c.get("/api/v1/logs/entries", params={"source": "mcp-demo", "limit": 500}).json()["entries"]
    loggers = {e["logger"] for e in entries}
    assert {"output", "supervisor"} <= loggers
    assert any("demo MCP server on" in e["msg"] for e in entries)


def test_tools_of_a_closed_port_report_an_error_not_a_500(make):
    port = free_port()
    c = make([{"key": "gone", "title": "Gone", "command": fake("sleep"), "url": f"http://127.0.0.1:{port}/mcp"}])
    r = c.get("/api/v1/mcp/servers/gone/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["tools"] == [] and body["error"]


# ---- crashes ---------------------------------------------------------------------------------

def test_crash_is_reported_with_output_and_restarts_are_limited(make):
    c = make([{"key": "crashy", "title": "Crashy", "command": fake("print-exit", "3")}], max_restarts=2)
    c.post("/api/v1/mcp/servers/crashy/start")
    st = wait_for(c, "crashy", lambda s: s["state"] == "crashed" and "aufgegeben" in (s["lastError"] or ""))
    assert st["exitCode"] == 3
    assert "ERROR: boom - fake crash" in st["recentOutput"]
    assert st["restarts"] == 2
    # stopping a crashed server acknowledges it
    st = c.post("/api/v1/mcp/servers/crashy/stop").json()
    assert st["state"] == "stopped" and st["lastError"] is None


def test_no_automatic_restart_when_disabled(make):
    c = make([{"key": "once", "title": "Once", "command": fake("print-exit", "1"), "restart_on_crash": False}])
    c.post("/api/v1/mcp/servers/once/start")
    st = wait_for(c, "once", lambda s: s["state"] == "crashed")
    time.sleep(0.3)
    st = status(c, "once")
    assert st["state"] == "crashed" and st["restarts"] == 0 and "Neustart" not in st["lastError"]


# ---- port conflicts and readiness -------------------------------------------------------------

def test_port_taken_by_someone_else_blocks_start(make):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        c = make([{"key": "busy", "title": "Busy", "command": fake("listen", str(port)),
                   "url": f"http://127.0.0.1:{port}/mcp"}])
        st = status(c, "busy")
        assert st["port"]["open"] is True and st["port"]["managed"] is False      # visible before any click
        assert st["port"]["pid"] == psutil.Process().pid
        r = c.post("/api/v1/mcp/servers/busy/start")
        assert r.status_code == 409
        assert f"Port {port} ist schon belegt" in r.json()["detail"] and str(psutil.Process().pid) in r.json()["detail"]
        st = status(c, "busy")
        assert st["state"] == "stopped"
        assert st["lastError"] is None                  # the port info says it, live; no stale message


def test_running_without_open_port_after_timeout(make):
    port = free_port()
    c = make([{"key": "mute", "title": "Mute", "command": fake("sleep"), "url": f"http://127.0.0.1:{port}/mcp"}],
             start_timeout_s=0.3)
    c.post("/api/v1/mcp/servers/mute/start")
    st = wait_for(c, "mute", lambda s: s["state"] == "running")
    assert "noch nicht offen" in st["lastError"]


def test_ready_when_the_port_opens(make):
    port = free_port()
    c = make([{"key": "plain", "title": "Plain", "command": fake("listen", str(port)),
               "url": f"http://127.0.0.1:{port}/mcp"}])
    c.post("/api/v1/mcp/servers/plain/start")
    st = wait_for(c, "plain", lambda s: s["state"] == "running")
    assert st["lastError"] is None and st["port"]["managed"] is True


def test_restart_gives_a_new_process(make):
    c = make([{"key": "sleepy", "title": "Sleepy", "command": fake("sleep")}])
    first = c.post("/api/v1/mcp/servers/sleepy/start").json()
    assert first["state"] == "running"                                # no url: nothing to wait for
    second = c.post("/api/v1/mcp/servers/sleepy/restart").json()
    assert second["state"] == "running" and second["pid"] != first["pid"]
    c.post("/api/v1/mcp/servers/sleepy/stop")


def test_start_errors_are_409_with_a_message(make, tmp_path):
    c = make([{"key": "typo", "title": "Typo", "command": ["no-such-program-xyz"]},
              {"key": "nodir", "title": "No dir", "command": fake("sleep"), "cwd": str(tmp_path / "missing")}])
    r = c.post("/api/v1/mcp/servers/typo/start")
    assert r.status_code == 409 and "nicht im PATH" in r.json()["detail"]
    assert status(c, "typo")["lastError"] == r.json()["detail"]
    assert "Arbeitsordner fehlt" in c.post("/api/v1/mcp/servers/nodir/start").json()["detail"]


# ---- autostart, shutdown, leftovers ----------------------------------------------------------

def test_autostart_and_children_end_with_the_control_center(settings, tmp_path):
    mods = type(settings.modules).model_validate({"mcp": {**FAST, "servers": [
        {"key": "auto", "title": "Auto", "command": fake("sleep"), "autostart": True, "cwd": str(tmp_path)}]}})
    with TestClient(create_app(settings.model_copy(update={"modules": mods}))) as c:
        st = status(c, "auto")
        assert st["state"] == "running"
        pid = st["pid"]
        assert (settings.data_dir / "mcp" / "auto.json").exists()     # PID file while it runs
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    assert not (settings.data_dir / "mcp" / "auto.json").exists()


def test_leftover_from_a_crashed_run_is_stopped_at_startup(make, settings, tmp_path):
    orphan = psutil.Popen([sys.executable, str(FAKE), "sleep"], cwd=tmp_path)
    save_record(settings.data_dir / "mcp" / "old.json", ProcessRecord(orphan.pid, orphan.create_time(), ["x"]))
    make([{"key": "old", "title": "Old", "command": fake("sleep")}])
    end = time.monotonic() + 5
    while orphan.is_running() and orphan.status() != psutil.STATUS_ZOMBIE and time.monotonic() < end:
        time.sleep(0.05)
    assert not orphan.is_running() or orphan.status() == psutil.STATUS_ZOMBIE


# ---- cross-site protection of write actions --------------------------------------------------

@pytest.mark.parametrize("headers,expected", [
    ({}, 200),                                                         # curl, scripts
    ({"Sec-Fetch-Site": "same-origin"}, 200),                          # our own page
    ({"Sec-Fetch-Site": "cross-site", "Origin": "http://evil.example"}, 403),
    ({"Sec-Fetch-Site": "same-site", "Origin": "http://testserver:8080"}, 403),   # other port, same host
    ({"Origin": "http://evil.example"}, 403),                          # old browser without Sec-Fetch-*
    ({"Origin": "http://testserver"}, 200),                            # old browser, own origin
])
def test_write_actions_refuse_cross_site_requests(make, headers, expected):
    c = make([{"key": "sleepy", "title": "Sleepy", "command": fake("sleep")}])
    r = c.post("/api/v1/mcp/servers/sleepy/stop", headers=headers)
    assert r.status_code == expected, r.text
    if expected == 403:
        assert "fremden Seite" in r.json()["detail"]


def test_reading_is_not_guarded(make):
    c = make([])
    assert c.get("/api/v1/mcp/servers", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_configured_cors_origin_is_allowed(make, settings):
    c = make([{"key": "sleepy", "title": "Sleepy", "command": fake("sleep")}])
    c.app.state.ctx.settings.cors_origins = ["http://localhost:4200"]
    r = c.post("/api/v1/mcp/servers/sleepy/stop",
               headers={"Sec-Fetch-Site": "same-site", "Origin": "http://localhost:4200"})
    assert r.status_code == 200
