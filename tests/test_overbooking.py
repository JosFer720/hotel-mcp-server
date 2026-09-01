"""Tests for the overbooking risk model."""

from __future__ import annotations

from datetime import timedelta

from hotel_mcp.logic.overbooking import calcular_riesgo_overbooking, tasas_no_show

from .conftest import TOMORROW, add_reservation


def _history(conn, canal, tarifa, total, no_shows):
    for i in range(total):
        add_reservation(
            conn,
            habitacion_id=None,
            checkin=(TOMORROW - timedelta(days=30 + i)).isoformat(),
            checkout=(TOMORROW - timedelta(days=29 + i)).isoformat(),
            canal=canal,
            tipo_tarifa=tarifa,
            estado="no_show" if i < no_shows else "checkout",
        )


class TestTasas:
    def test_smoothing_pulls_thin_segments_off_zero(self, hotel):
        """A segment with no observed no-shows must not report a rate of 0."""
        _history(hotel, "directo", "prepagada", total=3, no_shows=0)
        rates = tasas_no_show(hotel)
        entry = rates[("directo", "prepagada")]
        assert entry["tasa_cruda"] == 0.0
        assert entry["tasa_suavizada"] > 0.0

    def test_smoothing_barely_moves_a_large_segment(self, hotel):
        _history(hotel, "booking", "reembolsable", total=400, no_shows=60)
        entry = tasas_no_show(hotel)[("booking", "reembolsable")]
        assert abs(entry["tasa_suavizada"] - 0.15) < 0.02

    def test_cancellations_are_excluded(self, hotel):
        _history(hotel, "directo", "reembolsable", total=10, no_shows=2)
        add_reservation(
            hotel,
            checkin=(TOMORROW - timedelta(days=5)).isoformat(),
            canal="directo",
            tipo_tarifa="reembolsable",
            estado="cancelada",
        )
        # A cancellation releases inventory in advance; it never occupied the
        # night, so it must not dilute the no-show rate.
        assert tasas_no_show(hotel)[("directo", "reembolsable")]["historico_total"] == 10


class TestRiesgo:
    def test_no_arrivals_is_handled(self, hotel):
        result = calcular_riesgo_overbooking(hotel, TOMORROW.isoformat())
        assert result["reservaciones_confirmadas"] == 0
        assert result["sobreventa_recomendada"] == 0

    def test_risk_curve_is_monotonic(self, hotel):
        """P(walk) can only grow as you oversell more."""
        _history(hotel, "booking", "reembolsable", total=200, no_shows=30)
        for _ in range(20):
            add_reservation(hotel, canal="booking", tipo_tarifa="reembolsable")
        result = calcular_riesgo_overbooking(
            hotel, TOMORROW.isoformat(), simulaciones=3000, seed=7
        )
        curve = [p["prob_walk"] for p in result["curva_riesgo"]]
        assert curve == sorted(curve)
        assert curve[0] == 0.0  # overselling nothing can never force a walk

    def test_recommendation_respects_the_threshold(self, hotel):
        _history(hotel, "booking", "reembolsable", total=300, no_shows=45)
        for _ in range(30):
            add_reservation(hotel, canal="booking", tipo_tarifa="reembolsable")
        result = calcular_riesgo_overbooking(
            hotel, TOMORROW.isoformat(), umbral_riesgo=0.10, simulaciones=4000, seed=11
        )
        assert result["prob_walk"] <= 0.10
        assert result["sobreventa_recomendada"] >= 1

    def test_looser_threshold_never_recommends_less(self, hotel):
        _history(hotel, "booking", "reembolsable", total=300, no_shows=45)
        for _ in range(30):
            add_reservation(hotel, canal="booking", tipo_tarifa="reembolsable")
        strict = calcular_riesgo_overbooking(
            hotel, TOMORROW.isoformat(), umbral_riesgo=0.02, simulaciones=4000, seed=3
        )
        loose = calcular_riesgo_overbooking(
            hotel, TOMORROW.isoformat(), umbral_riesgo=0.25, simulaciones=4000, seed=3
        )
        assert loose["sobreventa_recomendada"] >= strict["sobreventa_recomendada"]

    def test_prepaid_segment_yields_fewer_expected_no_shows(self, hotel):
        """Segment mix must actually drive the answer."""
        _history(hotel, "booking", "reembolsable", total=200, no_shows=40)
        _history(hotel, "directo", "prepagada", total=200, no_shows=2)

        for _ in range(20):
            add_reservation(hotel, canal="booking", tipo_tarifa="reembolsable")
        risky = calcular_riesgo_overbooking(
            hotel, TOMORROW.isoformat(), simulaciones=2000, seed=5
        )

        hotel.execute("DELETE FROM reservaciones WHERE estado='confirmada'")
        for _ in range(20):
            add_reservation(hotel, canal="directo", tipo_tarifa="prepagada")
        safe = calcular_riesgo_overbooking(
            hotel, TOMORROW.isoformat(), simulaciones=2000, seed=5
        )

        assert risky["no_shows_esperados"] > safe["no_shows_esperados"] * 3

    def test_is_deterministic_for_a_fixed_seed(self, hotel):
        _history(hotel, "booking", "reembolsable", total=100, no_shows=15)
        for _ in range(15):
            add_reservation(hotel, canal="booking", tipo_tarifa="reembolsable")
        a = calcular_riesgo_overbooking(hotel, TOMORROW.isoformat(), simulaciones=2000, seed=42)
        b = calcular_riesgo_overbooking(hotel, TOMORROW.isoformat(), simulaciones=2000, seed=42)
        assert a["prob_walk"] == b["prob_walk"]
