"""Tests for room assignment, especially the cleaning-window rule."""

from __future__ import annotations

from datetime import timedelta

import pytest

from hotel_mcp.logic.assignment import asignar_habitacion, evaluar_ventana_limpieza

from .conftest import TODAY, TOMORROW, add_reservation


class TestVentanaLimpieza:
    def test_enough_time(self):
        assert evaluar_ventana_limpieza("12:00", 60, "14:00") == (True, 0)

    def test_not_enough_time_reports_shortfall(self):
        ok, faltan = evaluar_ventana_limpieza("12:00", 60, "12:30")
        assert ok is False
        assert faltan == 30

    def test_exactly_enough_is_accepted(self):
        assert evaluar_ventana_limpieza("12:00", 60, "13:00") == (True, 0)

    def test_unknown_arrival_is_permissive(self):
        # We cannot prove the window is too tight, so we do not block the room.
        assert evaluar_ventana_limpieza("12:00", 60, None) == (True, 0)

    def test_unknown_checkout_assumes_hotel_checkout_hour(self):
        # Defaults to 12:00, so 60 minutes of cleaning finishes at 13:00.
        assert evaluar_ventana_limpieza(None, 60, "13:00") == (True, 0)
        assert evaluar_ventana_limpieza(None, 60, "12:30")[0] is False


class TestAsignacion:
    def test_matches_view_preference(self, hotel):
        rid = add_reservation(hotel, huesped_id=2)  # Beto VIP, prefers sea view
        result = asignar_habitacion(hotel, rid)
        assert result["asignacion"] is not None
        assert result["asignacion"]["vista"] == "mar"

    def test_overlapping_reservation_excludes_room(self, hotel):
        add_reservation(hotel, habitacion_id=2, checkin=TOMORROW.isoformat(),
                        checkout=(TOMORROW + timedelta(days=3)).isoformat())
        rid = add_reservation(hotel, huesped_id=2)
        result = asignar_habitacion(hotel, rid)
        assert result["asignacion"]["habitacion"] != "205"

    def test_cleaning_window_rejects_a_free_room(self, hotel):
        """The heart of it: the room is free, but not cleanable in time."""
        # 310 is vacated at 11:00 the morning our guest arrives at 11:15.
        add_reservation(
            hotel,
            habitacion_id=3,
            checkin=(TOMORROW - timedelta(days=2)).isoformat(),
            checkout=TOMORROW.isoformat(),
            hora_checkout_real="11:00",
            estado="checkout",
        )
        rid = add_reservation(hotel, huesped_id=2, hora_checkin_estimada="11:15")
        result = asignar_habitacion(hotel, rid)

        rejected = {r["habitacion"]: r for r in result["descartadas_por_limpieza"]}
        assert "310" in rejected, "a room that cannot be cleaned in time must be rejected"
        # 11:00 + 35 minutes = 11:35, guest arrives 11:15 -> 20 minutes short.
        assert rejected["310"]["faltan_minutos"] == 20
        assert result["asignacion"]["habitacion"] != "310"

    def test_same_room_is_fine_with_a_later_arrival(self, hotel):
        """The same room becomes usable when the guest arrives later."""
        add_reservation(
            hotel,
            habitacion_id=3,
            checkin=(TOMORROW - timedelta(days=2)).isoformat(),
            checkout=TOMORROW.isoformat(),
            hora_checkout_real="11:00",
            estado="checkout",
        )
        rid = add_reservation(hotel, huesped_id=2, hora_checkin_estimada="15:00")
        result = asignar_habitacion(hotel, rid)
        assert result["descartadas_por_limpieza"] == []

    def test_same_day_turnover_is_not_an_overlap(self, hotel):
        """Checkout on day X and check-in on day X is a turnover, not a clash."""
        add_reservation(
            hotel,
            habitacion_id=2,
            checkin=(TOMORROW - timedelta(days=1)).isoformat(),
            checkout=TOMORROW.isoformat(),
            hora_checkout_real="09:00",
            estado="checkout",
        )
        rid = add_reservation(hotel, huesped_id=2, hora_checkin_estimada="16:00")
        result = asignar_habitacion(hotel, rid)
        candidates = [result["asignacion"]["habitacion"]] + [
            a["habitacion"] for a in result["alternativas"]
        ]
        assert "205" in candidates

    def test_accessibility_requirement_is_respected(self, hotel):
        rid = add_reservation(hotel, huesped_id=3)  # requires accessible
        result = asignar_habitacion(hotel, rid)
        assert result["asignacion"]["habitacion"] == "101"

    def test_capacity_filter(self, hotel):
        rid = add_reservation(hotel, personas=4)
        result = asignar_habitacion(hotel, rid)
        assert result["asignacion"]["habitacion"] == "501"  # the only 4-capacity room

    def test_score_breakdown_sums_to_total(self, hotel):
        rid = add_reservation(hotel, huesped_id=2)
        best = asignar_habitacion(hotel, rid)["asignacion"]
        assert sum(best["desglose"].values()) == best["puntaje"]

    def test_alternatives_never_outrank_the_pick(self, hotel):
        rid = add_reservation(hotel, huesped_id=2)
        result = asignar_habitacion(hotel, rid)
        for alt in result["alternativas"]:
            assert alt["puntaje"] <= result["asignacion"]["puntaje"]

    def test_unknown_reservation_raises(self, hotel):
        with pytest.raises(ValueError, match="does not exist"):
            asignar_habitacion(hotel, 9999)

    def test_cancelled_reservation_raises(self, hotel):
        rid = add_reservation(hotel, estado="cancelada")
        with pytest.raises(ValueError, match="cancelada"):
            asignar_habitacion(hotel, rid)
