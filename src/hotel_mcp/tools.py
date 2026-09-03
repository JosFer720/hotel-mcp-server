"""MCP tool definitions and schemas."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .db import connect
from .logic.assignment import asignar_habitacion as _asignar
from .logic.availability import consultar_disponibilidad as _disponibilidad
from .logic.booking import CANALES, TIPOS_TARIFA
from .logic.booking import confirmar_asignacion as _confirmar
from .logic.booking import crear_reservacion as _crear
from .logic.comprobante import generar_comprobante as _comprobante
from .logic.queries import buscar_reservaciones as _buscar
from .logic.queries import resumen_del_dia as _resumen
from .logic.housekeeping import programar_limpieza as _programar
from .logic.overbooking import calcular_riesgo_overbooking as _riesgo
from .mcpkit import McpServer, ToolResult

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SERVER_NAME = "hotel-mcp"
SERVER_VERSION = "0.1.0"

INSTRUCTIONS = """\
Internal hotel operations server.

Everyday questions:
- consultar_disponibilidad: how many rooms are free on a date or range, with the
  actual list, and breakdowns by type and view.
- buscar_reservaciones: find reservations by guest name, date, room or state.
- resumen_del_dia: arrivals, departures, occupancy and pending assignments for
  one day, in a single call.

Decisions:

- asignar_habitacion: choose the best room for a reservation, respecting guest
  preferences and validating that housekeeping can finish before the guest
  arrives.
- calcular_riesgo_overbooking: recommend how many rooms to oversell for a date,
  based on the real no-show history for each booking channel and rate type.
- programar_limpieza: build the day's cleaning schedule for the staff actually
  on shift, and flag rooms that will not be ready in time.

Writes (these change stored data):
- crear_reservacion: register a new booking, left unassigned on purpose. Two
  steps: call it once without confirmado to see what would be created, show
  that to the user, then call it again with confirmado=true once they agree.
- confirmar_asignacion: commit a room to a reservation. Until this succeeds,
  asignar_habitacion has only made a recommendation and nothing is booked.
- generar_comprobante: write a printable .xlsx proof of reservation.

The usual path for a new booking:

    crear_reservacion -> asignar_habitacion -> confirmar_asignacion
                                            -> generar_comprobante

For an existing one, start at buscar_reservaciones to turn a name into an id.

Start with consultar_disponibilidad or resumen_del_dia for general questions
about occupancy; the other tools act on one specific reservation or shift.

All dates are YYYY-MM-DD and all times are 24-hour HH:MM.
"""


def _check_date(value: str, field: str) -> None:
    if not _DATE.match(value or ""):
        raise ValueError(f"{field} must be formatted YYYY-MM-DD, got {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} is not a real date: {value!r}") from exc


ASIGNAR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reservacion_id": {
            "type": "integer",
            "description": "ID of the reservation to assign a room to.",
            "minimum": 1,
        },
        "preferencias_override": {
            "type": "object",
            "description": (
                "Optional preferences that override the ones stored on the guest "
                "profile, for requests made at the desk."
            ),
            "properties": {
                "vista": {
                    "type": "string",
                    "enum": ["mar", "ciudad", "jardin", "interior"],
                    "description": "Requested view.",
                },
                "piso_min": {"type": "integer", "description": "Lowest acceptable floor."},
                "accesible": {
                    "type": "boolean",
                    "description": "Guest requires a wheelchair-accessible room.",
                },
            },
        },
        "hora_checkin_estimada": {
            "type": "string",
            "description": (
                "Override the estimated arrival time (HH:MM). Changing this can "
                "change the answer: an earlier arrival may leave too little time "
                "to clean a room that would otherwise be the best match."
            ),
        },
    },
    "required": ["reservacion_id"],
}

OVERBOOKING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fecha": {"type": "string", "description": "Date to evaluate, YYYY-MM-DD."},
        "umbral_riesgo": {
            "type": "number",
            "description": (
                "Maximum acceptable probability of having to walk a guest. "
                "Defaults to 0.05."
            ),
            "minimum": 0.0,
            "maximum": 1.0,
        },
    },
    "required": ["fecha"],
}

LIMPIEZA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fecha": {"type": "string", "description": "Day to schedule, YYYY-MM-DD."},
        "turno": {
            "type": "string",
            "enum": ["matutino", "vespertino"],
            "description": "Restrict to one shift. Omit to schedule both.",
        },
    },
    "required": ["fecha"],
}


DISPONIBILIDAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fecha": {"type": "string", "description": "First night to check, YYYY-MM-DD."},
        "fecha_fin": {
            "type": "string",
            "description": "Last night to check, YYYY-MM-DD. Omit to check a single night.",
        },
        "tipo": {
            "type": "string",
            "enum": ["individual", "doble", "suite"],
            "description": "Only consider rooms of this type.",
        },
        "vista": {
            "type": "string",
            "enum": ["mar", "ciudad", "jardin", "interior"],
            "description": "Only consider rooms with this view.",
        },
    },
    "required": ["fecha"],
}

BUSCAR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "huesped": {"type": "string", "description": "Full or partial guest name."},
        "fecha": {
            "type": "string",
            "description": "Any stay covering this night, YYYY-MM-DD.",
        },
        "habitacion": {"type": "string", "description": "Room number, e.g. 402."},
        "estado": {
            "type": "string",
            "enum": ["confirmada", "checkin", "checkout", "no_show", "cancelada"],
            "description": "Filter by reservation state. Defaults to live reservations.",
        },
        "solo_sin_asignar": {
            "type": "boolean",
            "description": "Only reservations that still have no room assigned.",
        },
        "limite": {"type": "integer", "description": "Maximum rows. Default 25.", "minimum": 1},
    },
}

RESUMEN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"fecha": {"type": "string", "description": "Day to summarize, YYYY-MM-DD."}},
    "required": ["fecha"],
}

CREAR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "huesped": {
            "type": "string",
            "description": (
                "Full guest name. An existing profile with the same name is reused; "
                "otherwise a new one is created."
            ),
        },
        "checkin": {"type": "string", "description": "Arrival date, YYYY-MM-DD."},
        "checkout": {
            "type": "string",
            "description": "Departure date, YYYY-MM-DD. Must be after checkin.",
        },
        "personas": {
            "type": "integer",
            "description": "Party size. Defaults to 1.",
            "minimum": 1,
        },
        "canal": {
            "type": "string",
            "enum": list(CANALES),
            "description": "Booking channel. Defaults to directo.",
        },
        "tipo_tarifa": {
            "type": "string",
            "enum": list(TIPOS_TARIFA),
            "description": "Rate type. Defaults to reembolsable.",
        },
        "hora_checkin_estimada": {
            "type": "string",
            "description": (
                "Estimated arrival time, HH:MM. Worth asking for: without it the "
                "cleaning-window check cannot reject a room that will not be ready."
            ),
        },
        "vip": {"type": "boolean", "description": "Mark the guest as VIP."},
        "preferencias": {
            "type": "object",
            "description": "Stored guest preferences, e.g. {\"vista\": \"mar\", \"piso_min\": 3}.",
            "properties": {
                "vista": {"type": "string", "enum": ["mar", "ciudad", "jardin", "interior"]},
                "piso_min": {"type": "integer"},
                "accesible": {"type": "boolean"},
            },
        },
        "confirmado": {
            "type": "boolean",
            "description": (
                "Leave this out (or false) on the first call: nothing is written and "
                "you get back the booking that would be created, to show the user. "
                "Set it to true only on a second call, after the user has approved "
                "those exact details."
            ),
        },
    },
    "required": ["huesped", "checkin", "checkout"],
}

CONFIRMAR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reservacion_id": {"type": "integer", "description": "Reservation to update.", "minimum": 1},
        "habitacion": {
            "type": "string",
            "description": "Room number to commit, e.g. 402. Normally the one asignar_habitacion recommended.",
        },
    },
    "required": ["reservacion_id", "habitacion"],
}

COMPROBANTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reservacion_id": {
            "type": "integer",
            "description": "Reservation to issue the receipt for.",
            "minimum": 1,
        },
        "destino": {
            "type": "string",
            "description": (
                "Optional full path for the .xlsx. Omit to write it into the "
                "server's export directory under an automatic name."
            ),
        },
    },
    "required": ["reservacion_id"],
}


def build_server() -> McpServer:
    server = McpServer(SERVER_NAME, SERVER_VERSION, INSTRUCTIONS)

    @server.tool(
        name="consultar_disponibilidad",
        title="Room availability",
        description=(
            "How many rooms are free on a date, or across a range of nights. Returns the "
            "count and the actual list of free rooms with type, capacity, view and rate, "
            "plus breakdowns by type and view, and how many arrivals that day still have "
            "no room assigned. Use this for any general question about availability or "
            "occupancy. A room counts as occupied on a night when a live reservation "
            "satisfies checkin <= night < checkout, so the checkout day itself is free."
        ),
        input_schema=DISPONIBILIDAD_SCHEMA,
    )
    def consultar_disponibilidad(
        fecha: str,
        fecha_fin: str | None = None,
        tipo: str | None = None,
        vista: str | None = None,
    ) -> ToolResult:
        _check_date(fecha, "fecha")
        if fecha_fin:
            _check_date(fecha_fin, "fecha_fin")
        with connect(readonly=True) as conn:
            return ToolResult.json(_disponibilidad(conn, fecha, fecha_fin, tipo, vista))

    @server.tool(
        name="buscar_reservaciones",
        title="Find reservations",
        description=(
            "Find reservations by guest name (partial, case-insensitive), by a night the "
            "stay covers, by room number, or by state. Set solo_sin_asignar to list "
            "arrivals that still need a room. Use this to turn a guest's name into a "
            "reservacion_id before calling asignar_habitacion."
        ),
        input_schema=BUSCAR_SCHEMA,
    )
    def buscar_reservaciones(
        huesped: str | None = None,
        fecha: str | None = None,
        habitacion: str | None = None,
        estado: str | None = None,
        solo_sin_asignar: bool = False,
        limite: int = 25,
    ) -> ToolResult:
        if fecha:
            _check_date(fecha, "fecha")
        with connect(readonly=True) as conn:
            return ToolResult.json(
                _buscar(conn, huesped, fecha, habitacion, estado, solo_sin_asignar, limite)
            )

    @server.tool(
        name="resumen_del_dia",
        title="Daily summary",
        description=(
            "Everything about one day in a single call: arrivals with their estimated "
            "times and VIP flags, departures, occupancy, and which arrivals still have no "
            "room assigned. This is the natural starting point for questions like 'how "
            "does today look' or 'who is arriving'."
        ),
        input_schema=RESUMEN_SCHEMA,
    )
    def resumen_del_dia(fecha: str) -> ToolResult:
        _check_date(fecha, "fecha")
        with connect(readonly=True) as conn:
            return ToolResult.json(_resumen(conn, fecha))

    @server.tool(
        name="asignar_habitacion",
        title="Assign a room",
        description=(
            "Choose the best available room for a reservation. Filters by party size, "
            "excludes rooms with overlapping stays, and rejects rooms that cannot be "
            "cleaned before the guest arrives (previous checkout time plus the room's "
            "cleaning duration must not exceed the estimated arrival). Returns the best "
            "match plus two alternatives, each with its score broken down, and lists the "
            "rooms rejected specifically for the cleaning window with how many minutes "
            "short they were."
        ),
        input_schema=ASIGNAR_SCHEMA,
    )
    def asignar_habitacion(
        reservacion_id: int,
        preferencias_override: dict | None = None,
        hora_checkin_estimada: str | None = None,
    ) -> ToolResult:
        with connect() as conn:
            if hora_checkin_estimada:
                # Apply temporary in-memory update
                conn.execute(
                    "UPDATE reservaciones SET hora_checkin_estimada = ? WHERE id = ?",
                    (hora_checkin_estimada, reservacion_id),
                )
            result = _asignar(conn, reservacion_id, preferencias_override)
            conn.rollback()
        if result["asignacion"] is None:
            # Determine failure reason
            descartes = result["resumen_descartes"]
            if descartes["por_limpieza"]:
                result["nota"] = (
                    f"No room satisfies every constraint. {descartes['por_limpieza']} were "
                    "rejected only because housekeeping cannot finish in time -- see "
                    "descartadas_por_limpieza; a later check-in time would free them up."
                )
            else:
                result["nota"] = (
                    "No room is free for those dates: all "
                    f"{descartes['evaluadas']} rooms of a suitable size are already booked "
                    "for part of the stay. Try consultar_disponibilidad on nearby dates, "
                    "or a shorter stay."
                )
        return ToolResult.json(result)

    @server.tool(
        name="calcular_riesgo_overbooking",
        title="Overbooking risk",
        description=(
            "Recommend how many rooms to oversell for a date. Learns the no-show rate "
            "for each booking channel and rate type from the property's own history "
            "(Laplace-smoothed for thin segments), models the night's no-shows as a sum "
            "of independent Bernoulli trials, and runs a Monte Carlo simulation to find "
            "the largest oversell that keeps the probability of walking a guest at or "
            "below the given threshold."
        ),
        input_schema=OVERBOOKING_SCHEMA,
    )
    def calcular_riesgo_overbooking(fecha: str, umbral_riesgo: float = 0.05) -> ToolResult:
        _check_date(fecha, "fecha")
        with connect(readonly=True) as conn:
            return ToolResult.json(_riesgo(conn, fecha, umbral_riesgo))

    @server.tool(
        name="programar_limpieza",
        title="Housekeeping schedule",
        description=(
            "Build the day's cleaning schedule. Lists every room checking out, marks "
            "those with a same-day arrival as deadline-bound (VIP arrivals first), and "
            "distributes the work earliest-deadline-first across the staff actually on "
            "shift, balancing total minutes. Reports any room that will not be ready "
            "before its incoming guest arrives."
        ),
        input_schema=LIMPIEZA_SCHEMA,
    )
    def programar_limpieza(fecha: str, turno: str | None = None) -> ToolResult:
        _check_date(fecha, "fecha")
        with connect(readonly=True) as conn:
            return ToolResult.json(_programar(conn, fecha, turno))

    @server.tool(
        name="crear_reservacion",
        title="Create a reservation",
        description=(
            "WRITES TO THE DATABASE, IN TWO STEPS. Register a new booking for a guest, "
            "reusing an existing guest profile when the name matches and creating one "
            "otherwise. Requires the guest name and both dates; party size, channel, "
            "rate type, estimated arrival time, VIP flag and preferences are optional. "
            "Call it FIRST without confirmado: nothing is written, and you get back "
            "the booking that would be created (with requiere_confirmacion true) so "
            "you can read it back to the user. Call it AGAIN with the same arguments "
            "plus confirmado=true once they approve; only then is it inserted and a "
            "reservacion_id returned. The reservation has no room until you call "
            "asignar_habitacion and then confirmar_asignacion."
        ),
        input_schema=CREAR_SCHEMA,
    )
    def crear_reservacion(
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
    ) -> ToolResult:
        _check_date(checkin, "checkin")
        _check_date(checkout, "checkout")
        # Connect in read-only mode until confirmed
        with connect(readonly=not confirmado) as conn:
            return ToolResult.json(
                _crear(
                    conn, huesped, checkin, checkout, personas, canal, tipo_tarifa,
                    hora_checkin_estimada, vip, preferencias, confirmado,
                )
            )

    @server.tool(
        name="confirmar_asignacion",
        title="Confirm a room assignment",
        description=(
            "WRITES TO THE DATABASE. Commit a room to a reservation. asignar_habitacion "
            "only recommends and changes nothing; this is what makes the assignment "
            "real. Every constraint is re-checked against current data first -- "
            "capacity, overlapping stays and the cleaning window -- so a recommendation "
            "that has gone stale is rejected with the reason instead of being applied."
        ),
        input_schema=CONFIRMAR_SCHEMA,
    )
    def confirmar_asignacion(reservacion_id: int, habitacion: str) -> ToolResult:
        with connect() as conn:
            return ToolResult.json(_confirmar(conn, reservacion_id, habitacion))

    @server.tool(
        name="generar_comprobante",
        title="Issue a reservation receipt",
        description=(
            "WRITES A FILE. Produce a printable proof of reservation as an .xlsx "
            "spreadsheet: folio, guest, room, dates, nights, nightly rate and total. "
            "Use it when a guest asks for written confirmation that their booking "
            "exists. Returns the absolute path of the file plus the same details as "
            "JSON, so the receipt can be read back without another query."
        ),
        input_schema=COMPROBANTE_SCHEMA,
    )
    def generar_comprobante(reservacion_id: int, destino: str | None = None) -> ToolResult:
        with connect(readonly=True) as conn:
            return ToolResult.json(_comprobante(conn, reservacion_id, destino))

    return server
