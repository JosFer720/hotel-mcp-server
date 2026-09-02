"""Tests for the write paths: creating reservations and committing rooms."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from hotel_mcp.logic.booking import confirmar_asignacion, crear_reservacion

from .conftest import TOMORROW, add_reservation

DAY_AFTER = (TOMORROW + timedelta(days=1)).isoformat()
THREE_DAYS = (TOMORROW + timedelta(days=3)).isoformat()


def test_creates_the_guest_when_the_name_is_new(hotel: sqlite3.Connection) -> None:
    out = crear_reservacion(
        hotel, "Nuevo Huesped", TOMORROW.isoformat(), DAY_AFTER, confirmado=True
    )

    assert out["huesped_nuevo"] is True
    assert out["estado"] == "confirmada"
    assert out["habitacion"] is None, "a new reservation must start unassigned"
    assert out["noches"] == 1
    stored = hotel.execute(
        "SELECT nombre FROM huespedes WHERE id = ?", (out["huesped_id"],)
    ).fetchone()
    assert stored["nombre"] == "Nuevo Huesped"


def test_reuses_an_existing_guest_regardless_of_case(hotel: sqlite3.Connection) -> None:
    out = crear_reservacion(
        hotel, "  beto   VIP ", TOMORROW.isoformat(), DAY_AFTER, confirmado=True
    )

    assert out["huesped_nuevo"] is False
    assert out["huesped_id"] == 2
    assert hotel.execute("SELECT COUNT(*) n FROM huespedes").fetchone()["n"] == 3


def test_updates_preferences_on_an_existing_guest(hotel: sqlite3.Connection) -> None:
    crear_reservacion(
        hotel, "Ana Normal", TOMORROW.isoformat(), DAY_AFTER,
        preferencias={"vista": "jardin"}, vip=True, confirmado=True,
    )
    row = hotel.execute("SELECT vip, preferencias FROM huespedes WHERE id = 1").fetchone()
    assert row["vip"] == 1
    assert "jardin" in row["preferencias"]


def test_flags_a_probable_double_entry(hotel: sqlite3.Connection) -> None:
    first = crear_reservacion(
        hotel, "Ana Normal", TOMORROW.isoformat(), DAY_AFTER, confirmado=True
    )
    second = crear_reservacion(
        hotel, "Ana Normal", TOMORROW.isoformat(), DAY_AFTER, confirmado=True
    )

    # Not rejected -- one family can legitimately need two rooms -- but flagged.
    assert "aviso" in second
    assert str(first["reservacion_id"]) in second["aviso"]


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"checkout": TOMORROW.isoformat()}, "must be after"),
        ({"personas": 0}, "at least 1"),
        ({"personas": 99}, "no room holds"),
        ({"canal": "telepatia"}, "canal must be one of"),
        ({"tipo_tarifa": "gratis"}, "tipo_tarifa must be one of"),
        ({"hora_checkin_estimada": "25:00"}, "not a real time"),
        ({"hora_checkin_estimada": "tarde"}, "must be HH:MM"),
        ({"checkin": "manana"}, "YYYY-MM-DD"),
        ({"huesped": "   "}, "cannot be empty"),
    ],
)
def test_rejects_bad_input(hotel: sqlite3.Connection, kwargs: dict, fragment: str) -> None:
    args = {
        "huesped": "Ana Normal",
        "checkin": TOMORROW.isoformat(),
        "checkout": DAY_AFTER,
        "confirmado": True,
        **kwargs,
    }
    with pytest.raises(ValueError, match=fragment):
        crear_reservacion(hotel, **args)
    assert hotel.execute("SELECT COUNT(*) n FROM reservaciones").fetchone()["n"] == 0


def test_confirming_persists_the_room(hotel: sqlite3.Connection) -> None:
    rid = add_reservation(hotel, huesped_id=2)

    out = confirmar_asignacion(hotel, rid, "205")

    assert out["confirmada"] is True
    assert out["habitacion"] == "205"
    stored = hotel.execute(
        "SELECT habitacion_id FROM reservaciones WHERE id = ?", (rid,)
    ).fetchone()
    assert stored["habitacion_id"] == 2


def test_confirming_rejects_a_room_taken_since_the_recommendation(
    hotel: sqlite3.Connection,
) -> None:
    """The race the revalidation exists for.

    Two clerks are recommended the same room; the second confirmation must not
    silently overwrite the first.
    """
    first = add_reservation(hotel, huesped_id=1)
    second = add_reservation(hotel, huesped_id=2)
    confirmar_asignacion(hotel, first, "205")

    with pytest.raises(ValueError, match="is taken"):
        confirmar_asignacion(hotel, second, "205")

    still_unassigned = hotel.execute(
        "SELECT habitacion_id FROM reservaciones WHERE id = ?", (second,)
    ).fetchone()
    assert still_unassigned["habitacion_id"] is None


def test_confirming_rejects_a_room_that_cannot_be_cleaned_in_time(
    hotel: sqlite3.Connection,
) -> None:
    """Room 205 empties at 12:30 and needs 35 minutes; the guest lands at 12:45."""
    add_reservation(
        hotel,
        habitacion_id=2,
        checkin=(TOMORROW - timedelta(days=2)).isoformat(),
        checkout=TOMORROW.isoformat(),
        hora_checkout_real="12:30",
        estado="checkout",
    )
    arriving = add_reservation(hotel, hora_checkin_estimada="12:45")

    with pytest.raises(ValueError, match="cannot be cleaned in time"):
        confirmar_asignacion(hotel, arriving, "205")


def test_confirming_rejects_a_room_that_is_too_small(hotel: sqlite3.Connection) -> None:
    rid = add_reservation(hotel, personas=4)
    with pytest.raises(ValueError, match="holds 2, the party is 4"):
        confirmar_asignacion(hotel, rid, "205")


def test_confirming_rejects_unknown_rooms_and_reservations(hotel: sqlite3.Connection) -> None:
    rid = add_reservation(hotel)
    with pytest.raises(ValueError, match="does not exist"):
        confirmar_asignacion(hotel, rid, "9999")
    with pytest.raises(ValueError, match="does not exist"):
        confirmar_asignacion(hotel, 4242, "205")


def test_confirming_reports_a_room_change(hotel: sqlite3.Connection) -> None:
    rid = add_reservation(hotel, habitacion_id=2)
    out = confirmar_asignacion(hotel, rid, "310")
    assert out["habitacion_anterior"] == "205"
    assert out["habitacion"] == "310"


def test_cancelled_reservations_cannot_be_assigned(hotel: sqlite3.Connection) -> None:
    rid = add_reservation(hotel, estado="cancelada")
    with pytest.raises(ValueError, match="cancelada"):
        confirmar_asignacion(hotel, rid, "205")


class TestConfirmationGate:
    """Creating a reservation must take two calls.

    The gate is in the code rather than the prompt because a prompt is advice:
    told to read the details back and wait, the model complied on one run and
    wrote immediately on the next.
    """

    def test_the_first_call_writes_nothing(self, hotel: sqlite3.Connection) -> None:
        out = crear_reservacion(hotel, "Nadie Aun", TOMORROW.isoformat(), DAY_AFTER)

        assert out["requiere_confirmacion"] is True
        assert out["creado"] is False
        assert "reservacion_id" not in out
        assert hotel.execute("SELECT COUNT(*) n FROM reservaciones").fetchone()["n"] == 0
        assert hotel.execute("SELECT COUNT(*) n FROM huespedes").fetchone()["n"] == 3, (
            "an unconfirmed booking must not create the guest either"
        )

    def test_the_preview_shows_exactly_what_would_be_written(
        self, hotel: sqlite3.Connection
    ) -> None:
        out = crear_reservacion(
            hotel, "Beto VIP", TOMORROW.isoformat(), THREE_DAYS,
            personas=2, canal="booking", tipo_tarifa="prepagada",
            hora_checkin_estimada="3:05",
        )

        propuesta = out["reserva_propuesta"]
        assert propuesta["huesped"] == "Beto VIP"
        assert propuesta["huesped_nuevo"] is False
        assert propuesta["noches"] == 3, "TOMORROW to TOMORROW+3 is three nights"
        assert propuesta["hora_checkin_estimada"] == "03:05", "times are normalised"
        assert propuesta["canal"] == "booking"

    def test_the_preview_validates_before_asking_the_user(
        self, hotel: sqlite3.Connection
    ) -> None:
        """Bad input must fail at the preview, not after someone approves it."""
        with pytest.raises(ValueError, match="must be after"):
            crear_reservacion(hotel, "Ana Normal", DAY_AFTER, TOMORROW.isoformat())
        with pytest.raises(ValueError, match="canal must be one of"):
            crear_reservacion(
                hotel, "Ana Normal", TOMORROW.isoformat(), DAY_AFTER, canal="telepatia"
            )

    def test_confirming_writes_the_previewed_booking(self, hotel: sqlite3.Connection) -> None:
        args = dict(huesped="Nueva Persona", checkin=TOMORROW.isoformat(), checkout=DAY_AFTER)
        preview = crear_reservacion(hotel, **args)
        out = crear_reservacion(hotel, **args, confirmado=True)

        assert out["creado"] is True
        assert out["reservacion_id"] > 0
        for field in ("huesped", "checkin", "checkout", "noches", "personas", "canal"):
            assert out[field] == preview["reserva_propuesta"][field], (
                f"{field} changed between preview and write"
            )

    def test_the_preview_still_flags_a_probable_duplicate(
        self, hotel: sqlite3.Connection
    ) -> None:
        crear_reservacion(
            hotel, "Ana Normal", TOMORROW.isoformat(), DAY_AFTER, confirmado=True
        )
        preview = crear_reservacion(hotel, "Ana Normal", TOMORROW.isoformat(), DAY_AFTER)

        assert "aviso" in preview, "the user should hear about it before confirming"
