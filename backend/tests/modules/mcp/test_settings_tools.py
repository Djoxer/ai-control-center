"""Config validation and the tool table shape (no processes, no network)."""
import pytest
from pydantic import ValidationError

from control_center.modules.mcp.settings import McpServerConfig, McpSettings
from control_center.modules.mcp.tools import describe_params


def server(**over):
    return {"key": "rag", "title": "RAG", "command": ["python", "mcp_server.py"], **over}


def test_defaults_are_conservative():
    cfg = McpServerConfig.model_validate(server())
    assert cfg.autostart is False and cfg.restart_on_crash is True and cfg.port is None
    assert McpSettings().servers == []


@pytest.mark.parametrize("url,port", [
    ("http://127.0.0.1:8000/mcp", 8000),
    ("http://localhost/mcp", 80),
    ("https://127.0.0.1/mcp", 443),
])
def test_port_comes_from_the_url(url, port):
    assert McpServerConfig.model_validate(server(url=url)).port == port


@pytest.mark.parametrize("bad", [
    {"key": "Bad Key"},
    {"command": []},
    {"command": [" "]},
    {"url": "127.0.0.1:8000/mcp"},              # no scheme
    {"url": "ftp://127.0.0.1/mcp"},
    {"url": "http://127.0.0.1:99999/mcp"},       # port out of range
])
def test_invalid_server_entries(bad):
    with pytest.raises(ValidationError):
        McpServerConfig.model_validate(server(**bad))


def test_keys_and_ports_must_be_unique():
    with pytest.raises(ValidationError, match="unique"):
        McpSettings.model_validate({"servers": [server(), server()]})
    with pytest.raises(ValidationError, match="same port"):
        McpSettings.model_validate({"servers": [server(url="http://127.0.0.1:8000/mcp"),
                                                server(key="b", url="http://localhost:8000/x")]})


def test_describe_params_of_a_fastmcp_schema():
    # what FastMCP (mcp 1.x) generates for: def search_bent_php(query: str, limit: int = 8)
    schema = {"properties": {"query": {"title": "Query", "type": "string"},
                             "limit": {"default": 8, "title": "Limit", "type": "integer"}},
              "required": ["query"], "type": "object"}
    rows = describe_params(schema)
    assert [(r.name, r.type, r.required, r.default) for r in rows] == [
        ("query", "string", True, None), ("limit", "integer", False, "8")]


def test_describe_params_unions_arrays_and_garbage():
    schema = {"properties": {
        "tag": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "description": "optional"},
        "ids": {"type": "array", "items": {"type": "integer"}},
        "mode": {"type": ["string", "null"]},
        "odd": "not-a-dict",
    }}
    rows = {r.name: r for r in describe_params(schema)}
    assert rows["tag"].type == "string | null" and rows["tag"].default == "null"
    assert rows["tag"].description == "optional"
    assert rows["ids"].type == "integer[]"
    assert rows["mode"].type == "string | null"
    assert rows["odd"].type == "any"
    assert describe_params(None) == [] and describe_params({"properties": []}) == []
