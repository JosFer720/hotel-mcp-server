"""Room assignment with cleaning-window validation."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..db import preferencias

DEFAULT_CHECKOUT = "12:00"
"""Assumed checkout time when no time recorded."""


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
class Candidate:
    numero: str
    habitacion_id: int
    puntaje: int
    desglose: dict[str, Any] = field(default_factory=dict)
    tarifa: float = 0.0
    tipo: str = ""
    vista: str | None = None
    piso: int = 0


@dataclass(slots=True)
class Rejected:
    numero: str
    motivo: str
    faltan_minutos: int | None = None
    detalle: str = ""


def evaluar_ventana_limpieza(
    hora_checkout_anterior: str | None,
    minutos_limpieza: int,
    hora_checkin: str | None,
) -> tuple[bool, int]:
    """Check if room can be cleaned in time for check-in."""
    salida = _minutes(hora_checkout_anterior) or _minutes(DEFAULT_CHECKOUT) or 720
    llegada = _minutes(hora_checkin)
    if llegada is None:
        return True, 0
    listo = salida + minutos_limpieza
    if listo <= llegada:
        return True, 0
    return False, listo - llegada


def asignar_habitacion(
    conn: sqlite3.Connection,
    reservacion_id: int,
    preferencias_override: dict | None = None,
) -> dict[str, Any]:
    """Pick the best room for a reservation."""
    reserva = conn.execute(
        """SELECT r.*, h.nombre AS huesped, h.vip, h.preferencias
             FROM reservaciones r LEFT JOIN huespedes h ON h.id = r.huesped_id
            WHERE r.id = ?""",
        (reservacion_id,),
    ).fetchone()
    if reserva is None:
        raise ValueError(f"reservation {reservacion_id} does not exist")
    if reserva["estado"] in ("cancelada", "no_show"):
        raise ValueError(
            f"reservation {reservacion_id} is {reserva['estado']}; nothing to assign"
        )

    prefs = {**preferencias(reserva), **(preferencias_override or {})}
    vip = bool(reserva["vip"])
    personas = reserva["personas"] or 1
    checkin, checkout = reserva["checkin"], reserva["checkout"]
    hora_checkin = reserva["hora_checkin_estimada"]

    rooms = conn.execute(
        "SELECT * FROM habitaciones WHERE capacidad >= ? ORDER BY numero", (personas,)
    ).fetchall()

    candidatos: list[Candidate] = []
    descartadas_limpieza: list[Rejected] = []
    descartadas_otras: list[Rejected] = []

    for room in rooms:
        if prefs.get("accesible") and not room["accesible"]:
            descartadas_otras.append(
                Rejected(room["numero"], "no_accesible", detalle="guest requires accessibility")
            )
            continue

        # Overlap test
        overlap = conn.execute(
            """SELECT id FROM reservaciones
                WHERE habitacion_id = ? AND id != ?
                  AND estado NOT IN ('cancelada','no_show')
                  AND checkin < ? AND ? < checkout
                LIMIT 1""",
            (room["id"], reservacion_id, checkout, checkin),
        ).fetchone()
        if overlap is not None:
            descartadas_otras.append(Rejected(room["numero"], "ocupada", detalle="dates overlap"))
            continue

        # Same-day turnover check
        previa = conn.execute(
            """SELECT hora_checkout_real FROM reservaciones
                WHERE habitacion_id = ? AND id != ?
                  AND checkout = ? AND estado NOT IN ('cancelada','no_show')
                ORDER BY id DESC LIMIT 1""",
            (room["id"], reservacion_id, checkin),
        ).fetchone()

        if previa is not None:
            ok, faltan = evaluar_ventana_limpieza(
                previa["hora_checkout_real"], room["minutos_limpieza"], hora_checkin
            )
            if not ok:
                salida = previa["hora_checkout_real"] or DEFAULT_CHECKOUT
                listo = _minutes(salida) + room["minutos_limpieza"]
                descartadas_limpieza.append(
                    Rejected(
                        room["numero"],
                        "ventana_limpieza",
                        faltan_minutos=faltan,
                        detalle=(
                            f"previous checkout {salida} + {room['minutos_limpieza']}min "
                            f"cleaning = ready {_hhmm(listo)}, guest arrives {hora_checkin}"
                        ),
                    )
                )
                continue

        candidatos.append(_score(room, prefs, vip, personas))

    candidatos.sort(key=lambda c: (-c.puntaje, c.tarifa))

    return {
        "reservacion": {
            "id": reservacion_id,
            "huesped": reserva["huesped"],
            "vip": vip,
            "personas": personas,
            "checkin": checkin,
            "checkout": checkout,
            "hora_checkin_estimada": hora_checkin,
            "preferencias_aplicadas": prefs,
        },
        "asignacion": _as_dict(candidatos[0]) if candidatos else None,
        "alternativas": [_as_dict(c) for c in candidatos[1:3]],
        "descartadas_por_limpieza": [
            {
                "habitacion": r.numero,
                "faltan_minutos": r.faltan_minutos,
                "detalle": r.detalle,
            }
            for r in descartadas_limpieza
        ],
        "resumen_descartes": {
            "por_limpieza": len(descartadas_limpieza),
            "por_ocupacion_o_accesibilidad": len(descartadas_otras),
            "candidatas": len(candidatos),
            "evaluadas": len(rooms),
        },
    }


def _score(room: sqlite3.Row, prefs: dict, vip: bool, personas: int) -> Candidate:
    """Score a candidate room."""
    desglose: dict[str, Any] = {}
    total = 50
    desglose["base"] = 50

    if prefs.get("vista"):
        if room["vista"] == prefs["vista"]:
            total += 25
            desglose["vista_coincide"] = 25
        else:
            total -= 10
            desglose["vista_no_coincide"] = -10

    if prefs.get("piso_min") is not None:
        if room["piso"] >= prefs["piso_min"]:
            total += 10
            desglose["piso_minimo_ok"] = 10
        else:
            total -= 15
            desglose["piso_bajo_lo_pedido"] = -15

    if prefs.get("accesible") and room["accesible"]:
        total += 15
        desglose["accesible"] = 15

    if vip:
        # VIPs get a nudge toward higher floors and sea views.
        bonus = 8 + (5 if room["vista"] == "mar" else 0) + min(room["piso"], 5)
        total += bonus
        desglose["prioridad_vip"] = bonus

    # Penalise wasting a large room on a small party: a 4-person suite given to
    # a single guest is a room the hotel cannot sell to a family later.
    desperdicio = room["capacidad"] - personas
    if desperdicio > 0:
        penalty = min(desperdicio * 6, 18)
        total -= penalty
        desglose["desperdicio_capacidad"] = -penalty

    return Candidate(
        numero=room["numero"],
        habitacion_id=room["id"],
        puntaje=total,
        desglose=desglose,
        tarifa=room["tarifa_base"],
        tipo=room["tipo"],
        vista=room["vista"],
        piso=room["piso"],
    )


def _as_dict(c: Candidate) -> dict[str, Any]:
    return {
        "habitacion": c.numero,
        "habitacion_id": c.habitacion_id,
        "puntaje": c.puntaje,
        "desglose": c.desglose,
        "tipo": c.tipo,
        "vista": c.vista,
        "piso": c.piso,
        "tarifa_base": c.tarifa,
    }
