"""JSON-RPC 2.0 frame helpers for the server side."""

from __future__ import annotations

import json
from typing import Any

VERSION = "2.0"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class JsonRpcError(Exception):
    """Exception for JSON-RPC protocol failures."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def dumps(frame: dict[str, Any]) -> str:
    """Serialize a frame for the wire."""
    return json.dumps(frame, ensure_ascii=False, separators=(",", ":"))


def build_response(msg_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": VERSION, "id": msg_id, "result": result}


def build_error(msg_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": VERSION, "id": msg_id, "error": err}


def build_notification(method: str, params: dict | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {"jsonrpc": VERSION, "method": method}
    if params is not None:
        frame["params"] = params
    return frame
