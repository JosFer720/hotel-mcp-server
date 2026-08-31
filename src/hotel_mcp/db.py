"""SQLite access helpers."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = PACKAGE_DIR.parent.parent / "data" / "hotel.db"
SCHEMA_PATH = PACKAGE_DIR / "schema.sql"


def db_path() -> Path:
    """Database file path location."""
    return Path(os.environ.get("HOTEL_DB") or DEFAULT_DB)


@contextmanager
def connect(path: Path | str | None = None, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
    target = Path(path) if path else db_path()
    if readonly and target.exists():
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def preferencias(row: sqlite3.Row | dict) -> dict:
    """Parse the guest preferences JSON blob, tolerating bad data."""
    raw = row["preferencias"] if "preferencias" in row.keys() else None
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}
