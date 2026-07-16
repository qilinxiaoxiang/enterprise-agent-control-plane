from __future__ import annotations

from tool_server.server import mcp


def run() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    run()
