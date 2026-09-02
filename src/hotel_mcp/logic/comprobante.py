"""Proof-of-reservation receipts generator."""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from ..db import preferencias
from ..xlsx import timestamp, write_sheet

HOTEL_NAME = os.environ.get("HOTEL_NAME", "Hotel Demo UVG")


def export_dir() -> Path:
    """Where receipts are written."""
    return Path(os.environ.get("HOTEL_EXPORT_DIR") or "comprobantes")


def _slug(text: str) -> str:
    """Filename-safe guest name fragment."""
    limpio = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-")
    return (limpio[:40] or "huesped")


def generar_comprobante(
    conn: sqlite3.Connection,
    reservacion_id: int,
    destino: str | None = None,
) -> dict[str, Any]:
    """Render reservation to an .xlsx receipt."""
    r = conn.execute(
        """SELECT r.*, h.nombre AS huesped, h.vip, h.preferencias,
                  hab.numero, hab.tipo, hab.vista, hab.piso, hab.tarifa_base, hab.capacidad
             FROM reservaciones r
             LEFT JOIN huespedes h   ON h.id = r.huesped_id
             LEFT JOIN habitaciones hab ON hab.id = r.habitacion_id
            WHERE r.id = ?""",
        (reservacion_id,),
    ).fetchone()
    if r is None:
        raise ValueError(f"reservation {reservacion_id} does not exist")

    noches = (date.fromisoformat(r["checkout"]) - date.fromisoformat(r["checkin"])).days
    tarifa = r["tarifa_base"]
    total = round(tarifa * noches, 2) if tarifa is not None else None
    folio = f"R-{r['id']:05d}"
    emitido = timestamp()

    habitacion = (
        f"{r['numero']}  ({r['tipo']}, vista {r['vista']}, piso {r['piso']})"
        if r["numero"]
        else "sin asignar"
    )
    prefs = preferencias(r)

    filas: list[list[Any]] = [
        [("COMPROBANTE DE RESERVACION", "bold")],
        [HOTEL_NAME],
        [],
        [("Folio", "bold"), folio],
        [("Huesped", "bold"), r["huesped"]],
        [("VIP", "bold"), bool(r["vip"])],
        [("Habitacion", "bold"), habitacion],
        [("Personas", "bold"), r["personas"]],
        [],
        [("Check-in", "bold"), r["checkin"]],
        [("Hora estimada", "bold"), r["hora_checkin_estimada"] or "sin definir"],
        [("Check-out", "bold"), r["checkout"]],
        [("Noches", "bold"), noches],
        [],
        [("Tarifa por noche", "bold"), tarifa if tarifa is not None else "pendiente de asignar"],
        [("Total estimado", "bold"), total if total is not None else "pendiente de asignar"],
        [("Canal", "bold"), r["canal"]],
        [("Tipo de tarifa", "bold"), r["tipo_tarifa"]],
        [("Estado", "bold"), str(r["estado"]).upper()],
    ]
    if prefs:
        filas.append([])
        filas.append([("Preferencias", "bold")])
        filas.extend([[f"  {k}", str(v)] for k, v in prefs.items()])
    filas += [
        [],
        [("Emitido", "bold"), emitido],
        ["Documento generado por hotel-mcp para uso interno."],
    ]

    ruta = (
        Path(destino)
        if destino
        else export_dir() / f"comprobante-{folio}-{_slug(r['huesped'])}.xlsx"
    )
    write_sheet(ruta, filas, title="Comprobante", widths=[22, 44])

    return {
        "archivo": str(ruta.resolve()),
        "folio": folio,
        "reservacion_id": r["id"],
        "huesped": r["huesped"],
        "vip": bool(r["vip"]),
        "habitacion": r["numero"],
        "habitacion_asignada": r["numero"] is not None,
        "checkin": r["checkin"],
        "checkout": r["checkout"],
        "hora_checkin_estimada": r["hora_checkin_estimada"],
        "noches": noches,
        "personas": r["personas"],
        "tarifa_por_noche": tarifa,
        "total_estimado": total,
        "canal": r["canal"],
        "tipo_tarifa": r["tipo_tarifa"],
        "estado": r["estado"],
        "emitido": emitido,
    }
