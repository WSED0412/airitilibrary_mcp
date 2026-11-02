"""Entry point: local stdio transport (Claude Desktop / Claude Code).

To go remote later, add http.py next to this file containing only:
    from .server import mcp
    mcp.run("streamable-http")   # or mount mcp.streamable_http_app() under uvicorn
and nothing in server.py / client.py / parsers.py needs to change.
"""
from .server import mcp


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
