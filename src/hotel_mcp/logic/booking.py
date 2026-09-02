"""Write operations for reservations and assignments."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from .assignment import DEFAULT_CHECKOUT, evaluar_ventana_limpieza

CANALES = ("directo", "booking", "expedia", "agencia", "corporativo")
TIPOS_TARIFA = ("reembolsable", "no_reembolsable", "prepagada")


def _parse_fecha(value: str, campo: str) -> date:
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{campo} must be a real date as YYYY-MM-DD, got {value!r}") from exc


def _parse_hora(value: str | None) -> str | None:
    """Normalise HH:MM time string."""
    if not value:
        return None
    parts = str(value).split(":")
    try:
        h, m = int(parts[0]), int(parts[1])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"hora_checkin_estimada must be HH:MM, got {value!r}") from exc
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"hora_checkin_estimada is not a real time: {value!r}")
    return f"{h:02d}:{m:02d}"


def _buscar_o_crear_huesped(
    conn: sqlite3.Connection,
    nombre: str,
    vip: bool | None,
    preferencias: dict | None,
) -> tuple[int, bool]:
    """Resolve a guest by name, creating profile if missing."""
    limpio = " ".join(nombre.split())
    if not limpio:
        raise ValueError("the guest name cannot be empty")

    rows = conn.execute(
        "SELECT id FROM huespedes WHERE LOWER(nombre) = ? ORDER BY id",
        (limpio.lower(),),
    ).fetchall()

    if rows:
        huesped_id = rows[0]["id"]
        sets, params = [], []
        if vip is not None:
            sets.append("vip = ?")
            params.append(1 if vip else 0)
        if preferencias:
            sets.append("preferencias = ?")
            params.append(json.dumps(preferencias, ensure_ascii=False))
        if sets:
            params.append(huesped_id)
            conn.execute(f"UPDATE huespedes SET {', '.join(sets)} WHERE id = ?", params)
        return huesped_id, False

    cur = conn.execute(
        "INSERT INTO huespedes (nombre, vip, preferencias) VALUES (?, ?, ?)",
        (
            limpio,
            1 if vip else 0,
            json.dumps(preferencias, ensure_ascii=False) if preferencias else None,
        ),
    )
    return int(cur.lastrowid), True


def _validar_reserva(
    conn: sqlite3.Connection,
    huesped: str,
    checkin: str,
    checkout: str,
    personas: int,
    canal: str,
    tipo_tarifa: str,
    hora_checkin_estimada: str | None,
) -> dict[str, Any]:
    """Validate and normalise proposed booking fields."""
    d_in = _parse_fecha(checkin, "checkin")
    d_out = _parse_fecha(checkout, "checkout")
    if d_out <= d_in:
        raise ValueError(f"checkout ({checkout}) must be after checkin ({checkin})")

    personas = int(personas)
    if personas < 1:
        raise ValueError(f"personas must be at least 1, got {personas}")
    capacidad_max = conn.execute("SELECT MAX(capacidad) AS m FROM habitaciones").fetchone()["m"]
    if capacidad_max and personas > capacidad_max:
        raise ValueError(
            f"no room holds {personas} people; the largest has capacity {capacidad_max}"
        )

    if canal not in CANALES:
        raise ValueError(f"canal must be one of {', '.join(CANALES)}, got {canal!r}")
    if tipo_tarifa not in TIPOS_TARIFA:
        raise ValueError(
            f"tipo_tarifa must be one of {', '.join(TIPOS_TARIFA)}, got {tipo_tarifa!r}"
        )

    limpio = " ".join(huesped.split())
    if not limpio:
        raise ValueError("the guest name cannot be empty")

    existente = conn.execute(
        "SELECT id FROM huespedes WHERE LOWER(nombre) = ? ORDER BY id",
        (limpio.lower(),),
    ).fetchone()

    return {
        "huesped": limpio,
        "huesped_id": existente["id"] if existente else None,
        "huesped_nuevo": existente is None,
        "checkin": checkin,
        "checkout": checkout,
        "noches": (d_out - d_in).days,
        "hora_checkin_estimada": _parse_hora(hora_checkin_estimada),
        "personas": personas,
        "canal": canal,
        "tipo_tarifa": tipo_tarifa,
    }


def _aviso_duplicado(
    conn: sqlite3.Connection, huesped_id: int | None, checkin: str, checkout: str
) -> str | None:
    """Flag potential duplicate booking."""
    if huesped_id is None:
        return None
    dup = conn.execute(
        """SELECT id FROM reservaciones
            WHERE huesped_id = ? AND checkin = ? AND checkout = ?
              AND estado NOT IN ('cancelada','no_show')
            LIMIT 1""",
        (huesped_id, checkin, checkout),
    ).fetchone()
    if dup is None:
        return None
    return (
        f"This guest already has reservation {dup['id']} for the same dates. "
        "Point that out before creating another one."
    )


def crear_reservacion(
    conn: sqlite3.Connection,
    huesped: str,
    checkin: str,
    checkout: str,
    personas: int = 1,
    canal: str = "directo",
    tipo_tarifa: str = "reembolsable",
    hora_checkin_estimada: str | None = None,
    vip: bool | None = None,
    preferencias: dict | None = None,
    confirmado: bool = False,
) -> dict[str, Any]:
    """Register a new reservation, unassigned."""
    propuesta = _validar_reserva(
        conn, huesped, checkin, checkout, personas, canal, tipo_tarifa, hora_checkin_estimada
    )
    aviso = _aviso_duplicado(conn, propuesta["huesped_id"], checkin, checkout)

    if not confirmado:
        preview: dict[str, Any] = {
            "requiere_confirmacion": True,
            "creado": False,
            "reserva_propuesta": {
                **propuesta,
                "vip": vip,
                "preferencias": preferencias,
                "estado": "confirmada",
                "habitacion": None,
            },
            "instruccion": (
                "Nothing has been written. Show these details to the user, in their "
                "own language, and call crear_reservacion again with the same "
                "arguments plus confirmado=true only after they approve. If they "
                "correct something, call again with the corrected values and no "
                "confirmado."
            ),
        }
        if aviso:
            preview["aviso"] = aviso
        return preview

    huesped_id, creado = _buscar_o_crear_huesped(conn, huesped, vip, preferencias)
    cur = conn.execute(
        """INSERT INTO reservaciones
             (huesped_id, habitacion_id, checkin, checkout, hora_checkin_estimada,
              personas, canal, tipo_tarifa, estado)
           VALUES (?, NULL, ?, ?, ?, ?, ?, ?, 'confirmada')""",
        (
            huesped_id,
            checkin,
            checkout,
            propuesta["hora_checkin_estimada"],
            propuesta["personas"],
            canal,
            tipo_tarifa,
        ),
    )
    conn.commit()

    resultado: dict[str, Any] = {
        **propuesta,
        "reservacion_id": int(cur.lastrowid),
        "huesped_id": huesped_id,
        "huesped_nuevo": creado,
        "creado": True,
        "estado": "confirmada",
        "habitacion": None,
        "siguiente_paso": (
            "Call asignar_habitacion with this reservacion_id to pick a room, "
            "then confirmar_asignacion to make it stick."
        ),
    }
    if aviso:
        resultado["aviso"] = aviso
    return resultado


def confirmar_asignacion(
    conn: sqlite3.Connection,
    reservacion_id: int,
    habitacion: str,
) -> dict[str, Any]:
    """Commit a room to a reservation."""
    reserva = conn.execute(
        """SELECT r.*, h.nombre AS huesped, h.vip
             FROM reservaciones r LEFT JOIN huespedes h ON h.id = r.huesped_id
            WHERE r.id = ?""",
        (reservacion_id,),
    ).fetchone()
    if reserva is None:
        raise ValueError(f"reservation {reservacion_id} does not exist")
    if reserva["estado"] in ("cancelada", "no_show"):
        raise ValueError(f"reservation {reservacion_id} is {reserva['estado']}; nothing to assign")

    room = conn.execute(
        "SELECT * FROM habitaciones WHERE numero = ?", (str(habitacion),)
    ).fetchone()
    if room is None:
        raise ValueError(f"room {habitacion!r} does not exist")

    personas = reserva["personas"] or 1
    if room["capacidad"] < personas:
        raise ValueError(
            f"room {room['numero']} holds {room['capacidad']}, the party is {personas}"
        )

    checkin, checkout = reserva["checkin"], reserva["checkout"]

    overlap = conn.execute(
        """SELECT r.id, h.nombre AS huesped, r.checkin, r.checkout
             FROM reservaciones r LEFT JOIN huespedes h ON h.id = r.huesped_id
            WHERE r.habitacion_id = ? AND r.id != ?
              AND r.estado NOT IN ('cancelada','no_show')
              AND r.checkin < ? AND ? < r.checkout
            LIMIT 1""",
        (room["id"], reservacion_id, checkout, checkin),
    ).fetchone()
    if overlap is not None:
        raise ValueError(
            f"room {room['numero']} is taken: reservation {overlap['id']} "
            f"({overlap['huesped']}) runs {overlap['checkin']} to {overlap['checkout']}. "
            f"Run asignar_habitacion again to get a current recommendation."
        )

    previa = conn.execute(
        """SELECT hora_checkout_real FROM reservaciones
            WHERE habitacion_id = ? AND id != ? AND checkout = ?
              AND estado NOT IN ('cancelada','no_show')
            ORDER BY id DESC LIMIT 1""",
        (room["id"], reservacion_id, checkin),
    ).fetchone()
    if previa is not None:
        ok, faltan = evaluar_ventana_limpieza(
            previa["hora_checkout_real"], room["minutos_limpieza"], reserva["hora_checkin_estimada"]
        )
        if not ok:
            salida = previa["hora_checkout_real"] or DEFAULT_CHECKOUT
            raise ValueError(
                f"room {room['numero']} cannot be cleaned in time: previous checkout "
                f"{salida} plus {room['minutos_limpieza']}min of cleaning finishes "
                f"{faltan}min after the guest arrives at "
                f"{reserva['hora_checkin_estimada']}."
            )

    anterior = conn.execute(
        "SELECT numero FROM habitaciones WHERE id = ?", (reserva["habitacion_id"],)
    ).fetchone() if reserva["habitacion_id"] else None

    conn.execute(
        "UPDATE reservaciones SET habitacion_id = ? WHERE id = ?",
        (room["id"], reservacion_id),
    )
    conn.commit()

    return {
        "reservacion_id": reservacion_id,
        "huesped": reserva["huesped"],
        "vip": bool(reserva["vip"]),
        "habitacion": room["numero"],
        "habitacion_anterior": anterior["numero"] if anterior else None,
        "tipo": room["tipo"],
        "vista": room["vista"],
        "piso": room["piso"],
        "tarifa_base": room["tarifa_base"],
        "checkin": checkin,
        "checkout": checkout,
        "hora_checkin_estimada": reserva["hora_checkin_estimada"],
        "confirmada": True,
    }
