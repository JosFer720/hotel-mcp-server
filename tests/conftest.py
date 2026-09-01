"""Shared fixtures: a tiny hand-built database with known properties.

The tests use a purpose-built database rather than the seeded one so each
assertion can be about an exact, hand-chosen situation.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from hotel_mcp.db import create_schema

TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    create_schema(c)
    yield c
    c.close()


@pytest.fixture
def hotel(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Four rooms, three guests, and no reservations yet."""
    conn.executemany(
        "INSERT INTO habitaciones "
        "(id,numero,piso,tipo,capacidad,vista,accesible,tarifa_base,minutos_limpieza) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (1, "101", 1, "doble", 2, "interior", 1, 100.0, 35),
            (2, "205", 2, "doble", 2, "mar", 0, 160.0, 35),
            (3, "310", 3, "doble", 2, "mar", 0, 175.0, 35),
            (4, "501", 5, "suite", 4, "mar", 0, 320.0, 60),
        ],
    )
    conn.executemany(
        "INSERT INTO huespedes (id,nombre,vip,preferencias) VALUES (?,?,?,?)",
        [
            (1, "Ana Normal", 0, None),
            (2, "Beto VIP", 1, '{"vista":"mar"}'),
            (3, "Caro Accesible", 0, '{"accesible":true}'),
        ],
    )
    conn.commit()
    return conn


def add_reservation(conn: sqlite3.Connection, **kw) -> int:
    """Insert a reservation, defaulting everything not specified."""
    row = {
        "huesped_id": 1,
        "habitacion_id": None,
        "checkin": TOMORROW.isoformat(),
        "checkout": (TOMORROW + timedelta(days=2)).isoformat(),
        "hora_checkin_estimada": "14:00",
        "hora_checkout_real": None,
        "personas": 2,
        "canal": "directo",
        "tipo_tarifa": "reembolsable",
        "estado": "confirmada",
        **kw,
    }
    cur = conn.execute(
        "INSERT INTO reservaciones "
        "(huesped_id,habitacion_id,checkin,checkout,hora_checkin_estimada,"
        " hora_checkout_real,personas,canal,tipo_tarifa,estado) "
        "VALUES (:huesped_id,:habitacion_id,:checkin,:checkout,:hora_checkin_estimada,"
        " :hora_checkout_real,:personas,:canal,:tipo_tarifa,:estado)",
        row,
    )
    conn.commit()
    return cur.lastrowid
