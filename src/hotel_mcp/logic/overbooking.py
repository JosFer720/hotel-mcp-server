"""Overbooking risk estimation based on historical no-show distribution."""

from __future__ import annotations

import random
import sqlite3
from typing import Any

LAPLACE_ALPHA = 1.0
LAPLACE_BETA = 8.0
"""Additive Laplace smoothing for per-segment no-show rates."""

SIMULACIONES = 10_000


def tasas_no_show(conn: sqlite3.Connection) -> dict[tuple[str, str], dict[str, Any]]:
    """Historical no-show rate per channel and rate type."""
    rows = conn.execute(
        """SELECT canal, tipo_tarifa,
                  COUNT(*) AS total,
                  SUM(CASE WHEN estado = 'no_show' THEN 1 ELSE 0 END) AS no_shows
             FROM reservaciones
            WHERE estado IN ('checkout', 'no_show')
            GROUP BY canal, tipo_tarifa"""
    ).fetchall()

    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        total, ns = r["total"], r["no_shows"]
        tasa = (ns + LAPLACE_ALPHA) / (total + LAPLACE_ALPHA + LAPLACE_BETA)
        out[(r["canal"], r["tipo_tarifa"])] = {
            "historico_total": total,
            "historico_no_shows": ns,
            "tasa_cruda": round(ns / total, 4) if total else None,
            "tasa_suavizada": round(tasa, 4),
        }
    return out


def _tasa_global(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN estado='no_show' THEN 1 ELSE 0 END) AS ns
             FROM reservaciones WHERE estado IN ('checkout','no_show')"""
    ).fetchone()
    if not row or not row["total"]:
        return 0.05
    return (row["ns"] + LAPLACE_ALPHA) / (row["total"] + LAPLACE_ALPHA + LAPLACE_BETA)


def calcular_riesgo_overbooking(
    conn: sqlite3.Connection,
    fecha: str,
    umbral_riesgo: float = 0.05,
    simulaciones: int = SIMULACIONES,
    seed: int | None = 12345,
) -> dict[str, Any]:
    """Recommend how many rooms to oversell for fecha."""
    capacidad = conn.execute("SELECT COUNT(*) AS n FROM habitaciones").fetchone()["n"]

    arrivals = conn.execute(
        """SELECT canal, tipo_tarifa, COUNT(*) AS n
             FROM reservaciones
            WHERE checkin = ? AND estado = 'confirmada'
            GROUP BY canal, tipo_tarifa""",
        (fecha,),
    ).fetchall()

    # Rooms occupied by ongoing stays
    ocupadas = conn.execute(
        """SELECT COUNT(*) AS n FROM reservaciones
            WHERE checkin < ? AND checkout > ? AND estado IN ('confirmada','checkin')""",
        (fecha, fecha),
    ).fetchone()["n"]

    tasas = tasas_no_show(conn)
    global_rate = _tasa_global(conn)

    probs: list[float] = []
    desglose: list[dict[str, Any]] = []
    for row in arrivals:
        key = (row["canal"], row["tipo_tarifa"])
        info = tasas.get(key)
        tasa = info["tasa_suavizada"] if info else round(global_rate, 4)
        probs.extend([tasa] * row["n"])
        desglose.append(
            {
                "canal": row["canal"],
                "tarifa": row["tipo_tarifa"],
                "reservas": row["n"],
                "tasa_no_show": tasa,
                "historico": (info or {}).get("historico_total", 0),
                "no_shows_esperados": round(row["n"] * tasa, 2),
            }
        )
    desglose.sort(key=lambda d: -d["reservas"])

    confirmadas = len(probs)
    esperados = sum(probs)

    if confirmadas == 0:
        return {
            "fecha": fecha,
            "capacidad_total": capacidad,
            "habitaciones_ocupadas_por_estancias": ocupadas,
            "reservaciones_confirmadas": 0,
            "no_shows_esperados": 0.0,
            "sobreventa_recomendada": 0,
            "prob_walk": 0.0,
            "umbral_riesgo": umbral_riesgo,
            "desglose_por_canal": [],
            "nota": "No confirmed arrivals for this date; nothing to oversell against.",
        }

    rng = random.Random(seed)
    # Simulate no-show counts via Monte Carlo
    conteos = [0] * (confirmadas + 1)
    for _ in range(simulaciones):
        k = 0
        for p in probs:
            if rng.random() < p:
                k += 1
        conteos[k] += 1

    # Calculate risk per oversell count N
    acumulado = 0.0
    prob_walk_por_n: list[float] = []
    for n in range(confirmadas + 1):
        prob_walk_por_n.append(acumulado)  # P(no_shows < n)
        acumulado += conteos[n] / simulaciones

    recomendada = 0
    prob_walk = 0.0
    for n, p in enumerate(prob_walk_por_n):
        if p <= umbral_riesgo:
            recomendada, prob_walk = n, p
        else:
            break

    libres = max(capacidad - ocupadas - confirmadas, 0)

    return {
        "fecha": fecha,
        "capacidad_total": capacidad,
        "habitaciones_ocupadas_por_estancias": ocupadas,
        "reservaciones_confirmadas": confirmadas,
        "habitaciones_libres": libres,
        "no_shows_esperados": round(esperados, 2),
        "sobreventa_recomendada": recomendada,
        "prob_walk": round(prob_walk, 4),
        "umbral_riesgo": umbral_riesgo,
        "metodo": (
            f"Monte Carlo, {simulaciones} simulaciones sobre una suma de Bernoullis "
            f"independientes; tasas por canal+tarifa con suavizado de Laplace "
            f"(alpha={LAPLACE_ALPHA}, beta={LAPLACE_BETA})"
        ),
        "curva_riesgo": [
            {"sobreventa": n, "prob_walk": round(p, 4)}
            for n, p in enumerate(prob_walk_por_n[: min(12, len(prob_walk_por_n))])
        ],
        "desglose_por_canal": desglose,
    }
