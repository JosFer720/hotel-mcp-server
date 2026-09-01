"""Tests for the housekeeping scheduler."""

from __future__ import annotations

from datetime import timedelta

from hotel_mcp.logic.housekeeping import programar_limpieza

from .conftest import TODAY, TOMORROW, add_reservation


def _staff(conn, names_shifts, fecha, disponible=1):
    for i, (nombre, turno) in enumerate(names_shifts, start=1):
        conn.execute(
            "INSERT INTO personal_limpieza (id,nombre,turno) VALUES (?,?,?)", (i, nombre, turno)
        )
        conn.execute(
            "INSERT INTO turnos_disponibles (personal_id,fecha,disponible) VALUES (?,?,?)",
            (i, fecha, disponible),
        )
    conn.commit()


def _checkout(conn, room, **kw):
    kw.setdefault("estado", "checkout")
    return add_reservation(
        conn,
        habitacion_id=room,
        checkin=(TODAY - timedelta(days=2)).isoformat(),
        checkout=TODAY.isoformat(),
        hora_checkout_real="10:00",
        **kw,
    )


def _arrival(conn, room, hora, huesped_id=1):
    return add_reservation(
        conn,
        habitacion_id=room,
        checkin=TODAY.isoformat(),
        checkout=TOMORROW.isoformat(),
        hora_checkin_estimada=hora,
        huesped_id=huesped_id,
    )


class TestProgramacion:
    def test_no_staff_leaves_everything_unassigned(self, hotel):
        _checkout(hotel, 1)
        result = programar_limpieza(hotel, TODAY.isoformat())
        assert result["asignaciones"] == []
        assert len(result["sin_asignar"]) == 1

    def test_rooms_without_arrivals_have_no_deadline(self, hotel):
        _staff(hotel, [("Marta", "matutino")], TODAY.isoformat())
        _checkout(hotel, 1)
        result = programar_limpieza(hotel, TODAY.isoformat())
        task = result["asignaciones"][0]["orden"][0]
        assert task["deadline"] is None
        assert result["en_riesgo"] == []

    def test_deadline_bound_rooms_are_scheduled_first(self, hotel):
        _staff(hotel, [("Marta", "matutino")], TODAY.isoformat())
        _checkout(hotel, 1)  # no arrival
        _checkout(hotel, 2)
        _arrival(hotel, 2, "12:00")  # urgent
        result = programar_limpieza(hotel, TODAY.isoformat())
        order = [t["habitacion"] for t in result["asignaciones"][0]["orden"]]
        assert order[0] == "205", "the room with a deadline must be cleaned first"

    def test_vip_outranks_a_normal_guest_at_the_same_hour(self, hotel):
        _staff(hotel, [("Marta", "matutino")], TODAY.isoformat())
        _checkout(hotel, 1)
        _arrival(hotel, 1, "14:00", huesped_id=1)
        _checkout(hotel, 2)
        _arrival(hotel, 2, "14:00", huesped_id=2)  # VIP
        result = programar_limpieza(hotel, TODAY.isoformat())
        order = [t["habitacion"] for t in result["asignaciones"][0]["orden"]]
        assert order.index("205") < order.index("101")

    def test_flags_a_room_that_cannot_be_ready_in_time(self, hotel):
        """One worker, four rooms, and an arrival too early to make."""
        _staff(hotel, [("Marta", "matutino")], TODAY.isoformat())
        for room in (1, 2, 3):
            _checkout(hotel, room)
            _arrival(hotel, room, "09:00")  # shift starts 08:00; 35min each
        result = programar_limpieza(hotel, TODAY.isoformat())
        assert result["en_riesgo"], "an impossible deadline must be reported"
        flagged = result["en_riesgo"][0]
        assert flagged["retraso_minutos"] > 0
        assert flagged["fin_estimado"] > flagged["deadline"]

    def test_more_staff_removes_the_risk(self, hotel):
        _staff(hotel, [("A", "matutino"), ("B", "matutino"), ("C", "matutino")],
               TODAY.isoformat())
        for room in (1, 2, 3):
            _checkout(hotel, room)
            _arrival(hotel, room, "09:00")
        result = programar_limpieza(hotel, TODAY.isoformat())
        assert result["en_riesgo"] == []

    def test_load_is_shared_between_workers(self, hotel):
        _staff(hotel, [("A", "matutino"), ("B", "matutino")], TODAY.isoformat())
        for room in (1, 2, 3, 4):
            _checkout(hotel, room)
        result = programar_limpieza(hotel, TODAY.isoformat())
        loads = [a["carga_minutos"] for a in result["asignaciones"]]
        assert len(loads) == 2
        assert max(loads) - min(loads) <= 60  # roughly one task apart

    def test_unavailable_staff_are_not_scheduled(self, hotel):
        _staff(hotel, [("Ausente", "matutino")], TODAY.isoformat(), disponible=0)
        _checkout(hotel, 1)
        result = programar_limpieza(hotel, TODAY.isoformat())
        assert result["resumen"]["personal_disponible"] == 0

    def test_shift_filter(self, hotel):
        _staff(hotel, [("Manana", "matutino"), ("Tarde", "vespertino")], TODAY.isoformat())
        _checkout(hotel, 1)
        result = programar_limpieza(hotel, TODAY.isoformat(), turno="vespertino")
        assert result["resumen"]["personal_disponible"] == 1
        assert result["asignaciones"][0]["turno"] == "vespertino"

    def test_cancelled_stays_need_no_cleaning(self, hotel):
        _staff(hotel, [("Marta", "matutino")], TODAY.isoformat())
        _checkout(hotel, 1, estado="cancelada")
        result = programar_limpieza(hotel, TODAY.isoformat())
        assert result["resumen"]["habitaciones_por_limpiar"] == 0
