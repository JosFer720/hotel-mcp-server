"""Tool registration, schema validation and MCP method dispatch."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .jsonrpc import INVALID_PARAMS, METHOD_NOT_FOUND, JsonRpcError

PROTOCOL_VERSION = "2025-06-18"

ToolFn = Callable[..., Any] | Callable[..., Awaitable[Any]]


@dataclass(slots=True)
class ToolResult:
    """An MCP ``CallToolResult``."""

    content: list[dict[str, Any]] = field(default_factory=list)
    is_error: bool = False
    structured: Any = None

    @classmethod
    def text(cls, text: str) -> ToolResult:
        return cls(content=[{"type": "text", "text": text}])

    @classmethod
    def json(cls, payload: Any) -> ToolResult:
        """Return structured data."""
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        return cls(content=[{"type": "text", "text": text}], structured=payload)

    @classmethod
    def error(cls, message: str) -> ToolResult:
        return cls(content=[{"type": "text", "text": message}], is_error=True)

    def to_wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {"content": self.content, "isError": self.is_error}
        if self.structured is not None:
            out["structuredContent"] = self.structured
        return out


@dataclass(slots=True)
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: ToolFn
    title: str | None = None

    def to_wire(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
        if self.title:
            d["title"] = self.title
        return d


def validate(args: dict[str, Any], schema: dict[str, Any]) -> None:
    """Check arguments against JSON schema."""
    for name in schema.get("required", []):
        if name not in args or args[name] is None:
            raise JsonRpcError(INVALID_PARAMS, f"missing required parameter: {name!r}")

    types: dict[str, tuple[type, ...]] = {
        "string": (str,),
        "integer": (int,),
        "number": (int, float),
        "boolean": (bool,),
        "array": (list,),
        "object": (dict,),
    }

    for name, spec in (schema.get("properties") or {}).items():
        if name not in args or args[name] is None:
            continue
        value = args[name]
        expected = spec.get("type")
        if expected in types:
            # Check bool vs int type
            if expected in ("integer", "number") and isinstance(value, bool):
                raise JsonRpcError(INVALID_PARAMS, f"{name!r} must be a {expected}")
            if not isinstance(value, types[expected]):
                raise JsonRpcError(
                    INVALID_PARAMS,
                    f"{name!r} must be a {expected}, got {type(value).__name__}",
                )
        if "enum" in spec and value not in spec["enum"]:
            raise JsonRpcError(
                INVALID_PARAMS, f"{name!r} must be one of {spec['enum']}, got {value!r}"
            )
        for bound, op in (("minimum", "<"), ("maximum", ">")):
            if bound in spec and isinstance(value, (int, float)):
                if (op == "<" and value < spec[bound]) or (op == ">" and value > spec[bound]):
                    raise JsonRpcError(INVALID_PARAMS, f"{name!r} {bound} is {spec[bound]}")


class McpServer:
    """Holds the tool table and answers MCP methods."""

    def __init__(self, name: str, version: str, instructions: str = "") -> None:
        self.name = name
        self.version = version
        self.instructions = instructions
        self.tools: dict[str, ToolDef] = {}
        self.initialized = False

    def tool(
        self, *, name: str, description: str, input_schema: dict[str, Any], title: str | None = None
    ) -> Callable[[ToolFn], ToolFn]:
        def decorator(fn: ToolFn) -> ToolFn:
            self.tools[name] = ToolDef(name, description, input_schema, fn, title)
            return fn

        return decorator

    async def dispatch(self, method: str, params: dict[str, Any] | None) -> Any:
        params = params or {}
        if method == "initialize":
            return self.initialize(params)
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [t.to_wire() for t in self.tools.values()]}
        if method == "tools/call":
            return await self.call_tool(params)
        raise JsonRpcError(METHOD_NOT_FOUND, f"Method not found: {method}")

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Answer handshake request."""
        self.initialized = True
        result: dict[str, Any] = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self.name, "version": self.version},
        }
        if self.instructions:
            result["instructions"] = self.instructions
        return result

    async def call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run requested tool."""
        name = params.get("name")
        if not isinstance(name, str) or name not in self.tools:
            raise JsonRpcError(INVALID_PARAMS, f"Unknown tool: {name!r}")

        tool = self.tools[name]
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            raise JsonRpcError(INVALID_PARAMS, "'arguments' must be an object")
        validate(args, tool.input_schema)

        try:
            result = tool.fn(**args)
            if inspect.isawaitable(result):
                result = await result
        except JsonRpcError:
            raise
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"{type(exc).__name__}: {exc}").to_wire()

        if isinstance(result, ToolResult):
            return result.to_wire()
        return ToolResult.json(result).to_wire()
