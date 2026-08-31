"""Generate a realistic mock database."""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .db import DEFAULT_DB, connect, create_schema

CANALES = ["directo", "booking", "expedia", "agencia", "corporativo"]
TARIFAS = ["reembolsable", "no_reembolsable", "prepagada"]
VISTAS = ["mar", "ciudad", "jardin", "interior"]

# Ground-truth no-show probability per (channel, rate type). The tool has to
# recover these from the generated history.
NO_SHOW = {
    ("directo", "reembolsable"): 0.07,
    ("directo", "no_reembolsable"): 0.03,
    ("directo", "prepagada"): 0.01,
    ("booking", "reembolsable"): 0.16,
    ("booking", "no_reembolsable"): 0.05,
    ("booking", "prepagada"): 0.02,
    ("expedia", "reembolsable"): 0.14,
    ("expedia", "no_reembolsable"): 0.05,
    ("expedia", "prepagada"): 0.02,
    ("agencia", "reembolsable"): 0.09,
    ("agencia", "no_reembolsable"): 0.04,
    ("agencia", "prepagada"): 0.01,
    ("corporativo", "reembolsable"): 0.06,
    ("corporativo", "no_reembolsable"): 0.02,
    ("corporativo", "prepagada"): 0.01,
}

CANAL_WEIGHTS = [0.22, 0.30, 0.18, 0.15, 0.15]
TARIFA_WEIGHTS = [0.45, 0.33, 0.22]

TIPOS = [
    # (tipo, capacidad, minutos_limpieza, tarifa_base)
    ("individual", 1, 25, 85.0),
    ("doble", 2, 35, 130.0),
    ("doble", 3, 40, 150.0),
    ("suite", 4, 60, 280.0),
]


def _rooms(rng: random.Random, n: int) -> list[tuple]:
    rooms = []
    for i in range(n):
        piso = i // 12 + 1  # 12 rooms per floor
        idx = i % 12
        if idx >= 10:
            tipo, cap, mins, base = TIPOS[3]
        elif idx >= 7:
            tipo, cap, mins, base = TIPOS[2]
        elif idx >= 2:
            tipo, cap, mins, base = TIPOS[1]
        else:
            tipo, cap, mins, base = TIPOS[0]
        # Sea view on the high-numbered side, higher floors see more of it.
        if idx >= 8:
            vista = "mar"
        elif idx >= 5:
            vista = "ciudad"
        elif idx >= 2:
            vista = "jardin"
        else:
            vista = "interior"
        # Price rises with the floor and with a sea view.
        tarifa = round(base * (1 + 0.03 * (piso - 1)) * (1.25 if vista == "mar" else 1.0), 2)
        accesible = 1 if piso <= 2 and idx < 2 else 0
        rooms.append((f"{piso}{idx + 1:02d}", piso, tipo, cap, vista, accesible, tarifa, mins))
    return rooms


def _guest_prefs(rng: random.Random) -> str | None:
    prefs: dict = {}
    if rng.random() < 0.45:
        prefs["vista"] = rng.choices(VISTAS, weights=[0.45, 0.25, 0.2, 0.1])[0]
    if rng.random() < 0.25:
        prefs["piso_min"] = rng.choice([2, 3, 4])
    if rng.random() < 0.07:
        prefs["accesible"] = True
    return json.dumps(prefs, ensure_ascii=False) if prefs else None


def seed(
    path: Path | str | None = None,
    *,
    rooms: int = 60,
    guests: int = 400,
    staff: int = 12,
    years: int = 2,
    seed_value: int = 20250825,
) -> dict[str, int]:
    """Build the database from scratch. Returns row counts."""
    try:
        from faker import Faker
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "faker is required to seed the database: pip install 'hotel-mcp-server[seed]'"
        ) from exc

    rng = random.Random(seed_value)
    fake = Faker("es_MX")
    Faker.seed(seed_value)

    target = Path(path) if path else DEFAULT_DB
    if target.exists():
        target.unlink()

    with connect(target) as conn:
        create_schema(conn)
        counts = _populate(conn, rng, fake, rooms, guests, staff, years)
        conn.commit()
    return counts


def _populate(
    conn: sqlite3.Connection,
    rng: random.Random,
    fake,
    n_rooms: int,
    n_guests: int,
    n_staff: int,
    years: int,
) -> dict[str, int]:
    conn.executemany(
        "INSERT INTO habitaciones "
        "(numero, piso, tipo, capacidad, vista, accesible, tarifa_base, minutos_limpieza) "
        "VALUES (?,?,?,?,?,?,?,?)",
        _rooms(rng, n_rooms),
    )
    room_rows = conn.execute("SELECT id, capacidad FROM habitaciones").fetchall()
    room_ids = [r["id"] for r in room_rows]
    capacity_of = {r["id"]: r["capacidad"] for r in room_rows}

    conn.executemany(
        "INSERT INTO huespedes (nombre, vip, preferencias) VALUES (?,?,?)",
        [
            (fake.name(), 1 if rng.random() < 0.12 else 0, _guest_prefs(rng))
            for _ in range(n_guests)
        ],
    )
    guest_ids = [r[0] for r in conn.execute("SELECT id FROM huespedes").fetchall()]

    turnos = ["matutino", "vespertino"]
    conn.executemany(
        "INSERT INTO personal_limpieza (nombre, turno) VALUES (?,?)",
        [(fake.name(), turnos[i % 2]) for i in range(n_staff)],
    )
    staff_ids = [r[0] for r in conn.execute("SELECT id FROM personal_limpieza").fetchall()]

    today = date.today()
    start = today - timedelta(days=365 * years)
    end = today + timedelta(days=60)  # a forward book so "tomorrow" has data

    # Staff availability: everyone works most days, with days off.
    shifts = []
    d = today - timedelta(days=7)
    while d <= end:
        for sid in staff_ids:
            shifts.append((sid, d.isoformat(), 1 if rng.random() < 0.82 else 0))
        d += timedelta(days=1)
    conn.executemany(
        "INSERT INTO turnos_disponibles (personal_id, fecha, disponible) VALUES (?,?,?)",
        shifts,
    )

    reservations = _reservations(rng, guest_ids, room_ids, capacity_of, start, end, today)
    conn.executemany(
        "INSERT INTO reservaciones "
        "(huesped_id, habitacion_id, checkin, checkout, hora_checkin_estimada, "
        " hora_checkout_real, personas, canal, tipo_tarifa, estado) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        reservations,
    )

    return {
        "habitaciones": n_rooms,
        "huespedes": n_guests,
        "personal_limpieza": n_staff,
        "turnos_disponibles": len(shifts),
        "reservaciones": len(reservations),
    }


def _reservations(
    rng: random.Random,
    guest_ids: list[int],
    room_ids: list[int],
    capacity_of: dict[int, int],
    start: date,
    end: date,
    today: date,
) -> list[tuple]:
    """Walk the calendar, filling rooms night by night."""
    rows: list[tuple] = []
    # Track next free date for each room
    free_from: dict[int, date] = {rid: start for rid in room_ids}

    day = start
    while day <= end:
        weekend = day.weekday() >= 4
        peak = day.month in (6, 7, 12)
        target = 0.62 + (0.16 if weekend else 0) + (0.12 if peak else 0)
        target = min(target, 0.94)

        for room_id in room_ids:
            if free_from[room_id] > day:
                continue
            if rng.random() > target:
                continue

            nights = rng.choices([1, 2, 3, 4, 5, 7], weights=[0.2, 0.3, 0.2, 0.12, 0.1, 0.08])[0]
            checkout = day + timedelta(days=nights)

            canal = rng.choices(CANALES, weights=CANAL_WEIGHTS)[0]
            tarifa = rng.choices(TARIFAS, weights=TARIFA_WEIGHTS)[0]

            # Checkin and checkout estimates
            hora_checkin = rng.choices(
                ["11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "18:00", "20:00"],
                weights=[0.05, 0.07, 0.10, 0.24, 0.18, 0.14, 0.13, 0.09],
            )[0]
            hora_checkout = rng.choices(
                ["08:00", "09:00", "10:00", "11:00", "12:00", "13:00"],
                weights=[0.10, 0.20, 0.28, 0.25, 0.12, 0.05],
            )[0]

            if day >= today:
                estado = "confirmada"
                salida_real = None
            else:
                p = NO_SHOW[(canal, tarifa)]
                if rng.random() < p:
                    estado = "no_show"
                    salida_real = None
                elif rng.random() < 0.04:
                    estado = "cancelada"
                    salida_real = None
                else:
                    estado = "checkout"
                    salida_real = hora_checkout

            personas = min(rng.choices([1, 2, 3, 4], weights=[0.3, 0.45, 0.15, 0.1])[0],
                           capacity_of[room_id])

            rows.append(
                (
                    rng.choice(guest_ids),
                    room_id,
                    day.isoformat(),
                    checkout.isoformat(),
                    hora_checkin,
                    salida_real,
                    personas,
                    canal,
                    tarifa,
                    estado,
                )
            )
            # Free room immediately for no-shows or cancellations
            free_from[room_id] = day if estado in ("no_show", "cancelada") else checkout

        day += timedelta(days=1)

    # Add unassigned upcoming reservations
    for _ in range(40):
        checkin = today + timedelta(days=rng.randint(0, 21))
        nights = rng.choices([1, 2, 3, 4], weights=[0.3, 0.35, 0.2, 0.15])[0]
        rows.append(
            (
                rng.choice(guest_ids),
                None,
                checkin.isoformat(),
                (checkin + timedelta(days=nights)).isoformat(),
                rng.choices(
                    ["11:00", "13:00", "14:00", "15:00", "16:00", "19:00"],
                    weights=[0.12, 0.16, 0.26, 0.18, 0.16, 0.12],
                )[0],
                None,
                rng.choices([1, 2, 3, 4], weights=[0.3, 0.45, 0.15, 0.1])[0],
                rng.choices(CANALES, weights=CANAL_WEIGHTS)[0],
                rng.choices(TARIFAS, weights=TARIFA_WEIGHTS)[0],
                "confirmada",
            )
        )
    return rows


def main() -> None:
    counts = seed()
    for table, n in counts.items():
        print(f"{table:22} {n:>7}")
    print(f"\ndatabase: {DEFAULT_DB}")


if __name__ == "__main__":
    main()
