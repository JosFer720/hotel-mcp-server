"""Entry point: ``python -m hotel_mcp`` speaks MCP over stdio."""

from __future__ import annotations

import asyncio
import sys

from .mcpkit import StdioServer
from .tools import build_server


def main() -> None:
    server = build_server()
    try:
        asyncio.run(StdioServer(server).serve())
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"hotel-mcp fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
