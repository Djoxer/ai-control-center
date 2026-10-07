"""Tiny MCP server for the second PC and for tests: something to start, stop and list tools of
without Qdrant, Ollama or the real RAG server.

    uv run python -m control_center.modules.mcp.demo_server --port 8701

As [modules.mcp] entry (see control-center.example.toml):
    command = ["{python}", "-m", "control_center.modules.mcp.demo_server", "--port", "8701"]
    url = "http://127.0.0.1:8701/mcp"
"""
from __future__ import annotations

import argparse
from datetime import datetime

from mcp.server.mcpserver import MCPServer

server = MCPServer("acc-demo", instructions="Demo server of the AI Control Center - no real data.")


@server.tool()
def echo(text: str) -> str:
    """Gibt den Text unverändert zurück."""
    return text


@server.tool()
def add(a: int, b: int = 1) -> int:
    """Addiert zwei ganze Zahlen."""
    return a + b


@server.tool()
def server_time() -> str:
    """Uhrzeit des Rechners, auf dem der Demo-Server läuft (ISO 8601)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="AI Control Center demo MCP server")
    parser.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = only this machine (default)")
    parser.add_argument("--port", type=int, default=8701)
    args = parser.parse_args(argv)
    print(f"demo MCP server on http://{args.host}:{args.port}/mcp", flush=True)
    server.run(transport="streamable-http", host=args.host, port=args.port, streamable_http_path="/mcp")


if __name__ == "__main__":
    main()
