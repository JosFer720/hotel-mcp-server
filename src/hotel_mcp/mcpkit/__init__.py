"""A small, dependency-free MCP server core built directly on JSON-RPC 2.0.

This package deliberately contains no hotel-specific code, so it can be lifted
into any other MCP server unchanged. It implements the server half of MCP
revision 2025-06-18: the ``initialize`` handshake, ``tools/list``,
``tools/call``, ``ping``, and cancellation -- over the stdio transport.

No MCP SDK is used. Every frame is built, serialized and parsed here.
"""

from .jsonrpc import JsonRpcError, build_error, build_response, dumps
from .server import McpServer, ToolResult
from .stdio_server import StdioServer

__all__ = [
    "JsonRpcError",
    "build_error",
    "build_response",
    "dumps",
    "McpServer",
    "ToolResult",
    "StdioServer",
]

PROTOCOL_VERSION = "2025-06-18"
