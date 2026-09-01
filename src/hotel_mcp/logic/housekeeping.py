"""Daily housekeeping schedule generator."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

TURNOS = {
    "matutino": ("08:00", "16:00"),
    "vespertino": ("14:00", "22:00"),
}

SIN_DEADLINE = 24 * 60
"""Sort key for rooms with no arrival today."""


def _minutes(hhmm: str | None) -> int | None:
    if not hhmm:
        return None
    try:
        h, m = hhmm.split(":")[:2]
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _hhmm(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


@dataclass(slots=True)
class Tarea:
    habitacion: str
    habitacion_id: int
    minutos: int
    deadline: int | None
    vip: bool
    prioridad: int
    motivo: str
    inicio: int = 0
    fin: int = 0
    asignada_a: str = ""
    turno: str = ""


@dataclass(slots=True)
class Trabajador:
    nombre: str
    turno: str
    libre_desde: int
    fin_turno: int
    carga: int = 0
    tareas: list[Tarea] = field(default_factory=list)


def programar_limpieza(
    conn: sqlite3.Connection, fecha: str, turno: str | None = None
) -> dict[str, Any]:
    """Build the cleaning schedule for fecha."""
    salidas = conn.execute(
        """SELECT r.id, r.habitacion_id, r.hora_checkout_real,
                  hab.numero, hab.minutos_limpieza
             FROM reservaciones r
             JOIN habitaciones hab ON hab.id = r.habitacion_id
            WHERE r.checkout = ? AND r.estado NOT IN ('cancelada','no_show')
            ORDER BY hab.numero""",
        (fecha,),
    ).fetchall()

    tareas: list[Tarea] = []
    for salida in salidas:
        llegada = conn.execute(
            """SELECT r.hora_checkin_estimada, h.vip, h.nombre
                 FROM reservaciones r LEFT JOIN huespedes h ON h.id = r.huesped_id
                WHERE r.habitacion_id = ? AND r.checkin = ?
                  AND r.estado NOT IN ('cancelada','no_show')
                ORDER BY r.id DESC LIMIT 1""",
            (salida["habitacion_id"], fecha),
        ).fetchone()

        if llegada is not None:
            deadline = _minutes(llegada["hora_checkin_estimada"])
            vip = bool(llegada["vip"])
            prioridad = 1 if vip else 2
            motivo = (
                f"same-day arrival at {llegada['hora_checkin_estimada']}"
                f"{' (VIP)' if vip else ''}"
            )
        else:
            deadline, vip, prioridad = None, False, 3
            motivo = "checkout, no arrival today"

        tareas.append(
            Tarea(
                habitacion=salida["numero"],
                habitacion_id=salida["habitacion_id"],
                minutos=salida["minutos_limpieza"],
                deadline=deadline,
                vip=vip,
                prioridad=prioridad,
                motivo=motivo,
            )
        )

    personal = conn.execute(
        """SELECT p.id, p.nombre, p.turno
             FROM personal_limpieza p
             JOIN turnos_disponibles t ON t.personal_id = p.id
            WHERE t.fecha = ? AND t.disponible = 1
              AND (? IS NULL OR p.turno = ?)
            ORDER BY p.turno, p.nombre""",
        (fecha, turno, turno),
    ).fetchall()

    trabajadores = [
        Trabajador(
            nombre=p["nombre"],
            turno=p["turno"],
            libre_desde=_minutes(TURNOS[p["turno"]][0]) or 480,
            fin_turno=_minutes(TURNOS[p["turno"]][1]) or 960,
        )
        for p in personal
        if p["turno"] in TURNOS
    ]

    if not trabajadores:
        # Handle no available staff
        nota = (
            f"No housekeeping staff is rostered for {fecha}"
            + (f" on the {turno} shift" if turno else "")
            + (
                f", and {len(tareas)} room(s) need cleaning. Check turnos_disponibles."
                if tareas
                else ". There is also nothing to clean that day."
            )
        )
        return {
            "fecha": fecha,
            "turno": turno or "ambos",
            "nota": nota,
            "asignaciones": [],
            "en_riesgo": [],
            "sin_asignar": [
                {"habitacion": t.habitacion, "motivo": "no housekeeping staff on shift"}
                for t in tareas
            ],
            "resumen": {
                "habitaciones_por_limpiar": len(tareas),
                "personal_disponible": 0,
            },
        }

    # Sort tasks by deadline and priority
    tareas.sort(key=lambda t: (t.deadline if t.deadline is not None else SIN_DEADLINE,
                               t.prioridad, -t.minutos))

    en_riesgo: list[dict[str, Any]] = []
    sin_asignar: list[dict[str, Any]] = []

    for tarea in tareas:
        elegibles = [w for w in trabajadores if w.libre_desde + tarea.minutos <= w.fin_turno]
        if not elegibles:
            sin_asignar.append(
                {
                    "habitacion": tarea.habitacion,
                    "minutos": tarea.minutos,
                    "motivo": "no shift has enough time left",
                }
            )
            continue

        # Assign to worker who frees up soonest
        worker = min(elegibles, key=lambda w: (w.libre_desde, w.carga))
        tarea.inicio = worker.libre_desde
        tarea.fin = worker.libre_desde + tarea.minutos
        tarea.asignada_a = worker.nombre
        tarea.turno = worker.turno

        worker.libre_desde = tarea.fin
        worker.carga += tarea.minutos
        worker.tareas.append(tarea)

        if tarea.deadline is not None and tarea.fin > tarea.deadline:
            en_riesgo.append(
                {
                    "habitacion": tarea.habitacion,
                    "deadline": _hhmm(tarea.deadline),
                    "fin_estimado": _hhmm(tarea.fin),
                    "retraso_minutos": tarea.fin - tarea.deadline,
                    "vip": tarea.vip,
                    "asignada_a": tarea.asignada_a,
                }
            )

    asignaciones = [
        {
            "personal": w.nombre,
            "turno": w.turno,
            "carga_minutos": w.carga,
            "orden": [
                {
                    "habitacion": t.habitacion,
                    "inicio_estimado": _hhmm(t.inicio),
                    "fin_estimado": _hhmm(t.fin),
                    "minutos": t.minutos,
                    "deadline": _hhmm(t.deadline) if t.deadline is not None else None,
                    "vip": t.vip,
                    "motivo": t.motivo,
                }
                for t in w.tareas
            ],
        }
        for w in trabajadores
        if w.tareas
    ]

    return {
        "fecha": fecha,
        "turno": turno or "ambos",
        "asignaciones": asignaciones,
        "en_riesgo": en_riesgo,
        "sin_asignar": sin_asignar,
        "resumen": {
            "habitaciones_por_limpiar": len(tareas),
            "con_llegada_el_mismo_dia": sum(1 for t in tareas if t.deadline is not None),
            "vip": sum(1 for t in tareas if t.vip),
            "personal_disponible": len(trabajadores),
            "minutos_totales": sum(t.minutos for t in tareas),
            "en_riesgo": len(en_riesgo),
        },
    }
