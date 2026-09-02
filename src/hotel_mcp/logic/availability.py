"""Room availability for a date range."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any


def _next_day(fecha: str) -> str:
    return (date.fromisoformat(fecha) + timedelta(days=1)).isoformat()


def consultar_disponibilidad(
    conn: sqlite3.Connection,
    fecha: str,
    fecha_fin: str | None = None,
    tipo: str | None = None,
    vista: str | None = None,
) -> dict[str, Any]:
    """Report which rooms are free for each night in a range."""
    fecha_fin = fecha_fin or fecha
    if date.fromisoformat(fecha_fin) < date.fromisoformat(fecha):
        raise ValueError("fecha_fin cannot be earlier than fecha")

    filters: list[str] = []
    params: list[Any] = []
    if tipo:
        filters.append("tipo = ?")
        params.append(tipo)
    if vista:
        filters.append("vista = ?")
        params.append(vista)
    where = (" WHERE " + " AND ".join(filters)) if filters else ""

    rooms = conn.execute(
        f"SELECT id, numero, tipo, capacidad, vista, accesible, tarifa_base"
        f" FROM habitaciones{where} ORDER BY numero",
        params,
    ).fetchall()
    total = len(rooms)

    noches: list[dict[str, Any]] = []
    day = date.fromisoformat(fecha)
    last = date.fromisoformat(fecha_fin)

    while day <= last:
        d = day.isoformat()
        ocupadas = {
            r["habitacion_id"]
            for r in conn.execute(
                """SELECT DISTINCT habitacion_id FROM reservaciones
                    WHERE habitacion_id IS NOT NULL
                      AND estado NOT IN ('cancelada','no_show','checkout')
                      AND checkin <= ? AND ? < checkout""",
                (d, d),
            ).fetchall()
        }
        libres = [r for r in rooms if r["id"] not in ocupadas]

        # Unassigned arrivals count
        sin_asignar = conn.execute(
            """SELECT COUNT(*) AS n FROM reservaciones
                WHERE habitacion_id IS NULL AND checkin = ?
                  AND estado NOT IN ('cancelada','no_show')""",
            (d,),
        ).fetchone()["n"]

        noches.append(
            {
                "fecha": d,
                "habitaciones_totales": total,
                "ocupadas": total - len(libres),
                "libres": len(libres),
                "ocupacion_pct": round((total - len(libres)) / total * 100, 1) if total else 0.0,
                "llegadas_sin_habitacion_asignada": sin_asignar,
                "libres_por_tipo": _group(libres, "tipo"),
                "libres_por_vista": _group(libres, "vista"),
                "habitaciones_libres": [
                    {
                        "numero": r["numero"],
                        "tipo": r["tipo"],
                        "capacidad": r["capacidad"],
                        "vista": r["vista"],
                        "accesible": bool(r["accesible"]),
                        "tarifa_base": r["tarifa_base"],
                    }
                    for r in libres
                ],
            }
        )
        day += timedelta(days=1)

    return {
        "desde": fecha,
        "hasta": fecha_fin,
        "filtros": {"tipo": tipo, "vista": vista},
        "noches": noches,
        "resumen": {
            "habitaciones_consideradas": total,
            "minimo_libres_en_el_rango": min((n["libres"] for n in noches), default=0),
            "noches_consultadas": len(noches),
        },
    }


def _group(rows: list[sqlite3.Row], column: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        key = r[column] or "sin_definir"
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))
