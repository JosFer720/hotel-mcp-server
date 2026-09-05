"""The stdio transport, server side.

Newline-delimited JSON on stdin/stdout, per MCP's stdio transport binding.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from typing import Any

from .jsonrpc import (
    INTERNAL_ERROR,
    PARSE_ERROR,
    JsonRpcError,
    build_error,
    build_response,
    dumps,
)
from .server import McpServer


class StdioServer:
    """Reads frames from stdin, dispatches them, writes replies to stdout."""

    def __init__(self, server: McpServer) -> None:
        self.server = server
        self._tasks: dict[Any, asyncio.Task] = {}
        self._out = sys.stdout
        self._write_lock = asyncio.Lock()

    def _guard_stdout(self) -> None:
        """Redirect sys.stdout to stderr to prevent stream corruption."""
        self._out = sys.stdout
        sys.stdout = sys.stderr

    async def serve(self) -> None:
        self._guard_stdout()
        queue = self._start_stdin_reader()

        while True:
            line = await queue.get()
            if line is None:
                break  # stdin closed: the client is shutting us down.
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            await self._handle_line(text)

        for task in self._tasks.values():
            task.cancel()

    async def _handle_line(self, text: str) -> None:
        try:
            frame = json.loads(text)
        except json.JSONDecodeError as exc:
            await self._write(build_error(None, PARSE_ERROR, f"Parse error: {exc}"))
            return

        if isinstance(frame, list):
            for item in frame:
                await self._route(item)
            return
        await self._route(frame)

    async def _route(self, frame: dict[str, Any]) -> None:
        if not isinstance(frame, dict):
            return
        method = frame.get("method")
        msg_id = frame.get("id")

        if method is None:
            return  # A response to something we asked; we ask nothing.

        if msg_id is None:
            await self._notification(method, frame.get("params"))
            return

        # Run each request in its own task
        task = asyncio.create_task(self._request(msg_id, method, frame.get("params")))
        self._tasks[msg_id] = task
        task.add_done_callback(lambda _t, i=msg_id: self._tasks.pop(i, None))

    async def _request(self, msg_id: Any, method: str, params: dict | None) -> None:
        try:
            result = await self.server.dispatch(method, params)
        except JsonRpcError as exc:
            await self._write(build_error(msg_id, exc.code, exc.message, exc.data))
        except asyncio.CancelledError:
            # Request cancelled
            raise
        except Exception as exc:  # noqa: BLE001
            await self._write(build_error(msg_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"))
        else:
            await self._write(build_response(msg_id, result))

    async def _notification(self, method: str, params: dict | None) -> None:
        if method == "notifications/cancelled":
            target = (params or {}).get("requestId")
            task = self._tasks.get(target)
            if task is not None:
                task.cancel()

    async def _write(self, frame: dict[str, Any]) -> None:
        payload = dumps(frame)
        async with self._write_lock:
            self._out.write(payload + "\n")
            self._out.flush()

    @staticmethod
    def _start_stdin_reader() -> "asyncio.Queue[bytes | None]":
        """Read stdin on a background thread and forward lines through a queue."""
        loop = asyncio.get_running_loop()
        queue: "asyncio.Queue[bytes | None]" = asyncio.Queue()

        def _read_loop() -> None:
            stdin = sys.stdin.buffer
            while True:
                line = stdin.readline()
                if not line:
                    loop.call_soon_threadsafe(queue.put_nowait, None)
                    return
                loop.call_soon_threadsafe(queue.put_nowait, line)

        threading.Thread(target=_read_loop, daemon=True, name="stdin-reader").start()
        return queue
