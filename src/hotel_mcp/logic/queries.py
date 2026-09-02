"""General front-desk queries."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..db import preferencias

ESTADOS_VIVOS = ("confirmada", "checkin")


def _reserva_dict(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "reservacion_id": r["id"],
        "huesped": r["huesped"],
        "vip": bool(r["vip"]),
        "habitacion": r["numero"],
        "habitacion_asignada": r["numero"] is not None,
        "checkin": r["checkin"],
        "checkout": r["checkout"],
        "hora_checkin_estimada": r["hora_checkin_estimada"],
        "personas": r["personas"],
        "canal": r["canal"],
        "tipo_tarifa": r["tipo_tarifa"],
        "estado": r["estado"],
        "preferencias": preferencias(r),
    }


_SELECT = """
    SELECT r.*, h.nombre AS huesped, h.vip, h.preferencias, hab.numero
      FROM reservaciones r
      LEFT JOIN huespedes h ON h.id = r.huesped_id
      LEFT JOIN habitaciones hab ON hab.id = r.habitacion_id
"""


def buscar_reservaciones(
    conn: sqlite3.Connection,
    huesped: str | None = None,
    fecha: str | None = None,
    habitacion: str | None = None,
    estado: str | None = None,
    solo_sin_asignar: bool = False,
    limite: int = 25,
) -> dict[str, Any]:
    """Find reservations by guest name, date, room, or assignment state."""
    where: list[str] = []
    params: list[Any] = []

    if huesped:
        where.append("LOWER(h.nombre) LIKE ?")
        params.append(f"%{huesped.lower()}%")
    if fecha:
        where.append("r.checkin <= ? AND ? < r.checkout")
        params.extend([fecha, fecha])
    if habitacion:
        where.append("hab.numero = ?")
        params.append(habitacion)
    if estado:
        where.append("r.estado = ?")
        params.append(estado)
    else:
        where.append(f"r.estado IN ({','.join('?' * len(ESTADOS_VIVOS))})")
        params.extend(ESTADOS_VIVOS)
    if solo_sin_asignar:
        where.append("r.habitacion_id IS NULL")

    sql = _SELECT + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY r.checkin, r.id LIMIT ?"
    params.append(max(1, min(limite, 200)))

    rows = conn.execute(sql, params).fetchall()
    return {
        "filtros": {
            "huesped": huesped,
            "fecha": fecha,
            "habitacion": habitacion,
            "estado": estado,
            "solo_sin_asignar": solo_sin_asignar,
        },
        "encontradas": len(rows),
        "reservaciones": [_reserva_dict(r) for r in rows],
    }


def resumen_del_dia(conn: sqlite3.Connection, fecha: str) -> dict[str, Any]:
    """Daily summary of arrivals, departures, and occupancy."""
    llegadas = conn.execute(
        _SELECT + " WHERE r.checkin = ? AND r.estado IN ('confirmada','checkin')"
        " ORDER BY r.hora_checkin_estimada, r.id",
        (fecha,),
    ).fetchall()
    salidas = conn.execute(
        _SELECT + " WHERE r.checkout = ? AND r.estado NOT IN ('cancelada','no_show')"
        " ORDER BY hab.numero",
        (fecha,),
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) AS n FROM habitaciones").fetchone()["n"]
    ocupadas = conn.execute(
        """SELECT COUNT(DISTINCT habitacion_id) AS n FROM reservaciones
            WHERE habitacion_id IS NOT NULL
              AND estado NOT IN ('cancelada','no_show','checkout')
              AND checkin <= ? AND ? < checkout""",
        (fecha, fecha),
    ).fetchone()["n"]

    sin_asignar = [r for r in llegadas if r["numero"] is None]
    vips = [r for r in llegadas if r["vip"]]

    return {
        "fecha": fecha,
        "ocupacion": {
            "habitaciones_totales": total,
            "ocupadas": ocupadas,
            "libres": total - ocupadas,
            "ocupacion_pct": round(ocupadas / total * 100, 1) if total else 0.0,
        },
        "llegadas": {
            "total": len(llegadas),
            "vip": len(vips),
            "sin_habitacion_asignada": len(sin_asignar),
            "detalle": [_reserva_dict(r) for r in llegadas],
        },
        "salidas": {"total": len(salidas), "detalle": [_reserva_dict(r) for r in salidas]},
        "pendientes": {
            "asignar_habitacion": [
                {"reservacion_id": r["id"], "huesped": r["huesped"], "vip": bool(r["vip"]),
                 "hora_checkin_estimada": r["hora_checkin_estimada"]}
                for r in sin_asignar
            ]
        },
    }
