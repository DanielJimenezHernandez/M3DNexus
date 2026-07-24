"""Tests del motor de costes (funciones puras)."""

import math

from app.cost import (
    CostInputs,
    compute_cost,
    mm_to_grams,
)


def test_mm_to_grams_pla_one_meter():
    # 1 m de PLA 1.75 mm (densidad 1.24) ≈ 2.98 g.
    grams = mm_to_grams(1000, diameter_mm=1.75, density_g_cm3=1.24)
    assert math.isclose(grams, 2.982, abs_tol=0.01)


def test_mm_to_grams_zero():
    assert mm_to_grams(0) == 0.0
    assert mm_to_grams(-5) == 0.0


def test_compute_cost_full_breakdown():
    inp = CostInputs(
        energy_kwh=0.5,
        electricity_price_per_kwh=0.20,        # 0.10 €
        filament_weight_g=50,
        filament_price_per_kg=20.0,            # 1.00 €
        print_duration_hours=2.0,
        machine_purchase_price=500.0,
        machine_lifetime_hours=5000.0,         # 0.10 €/h → 0.20 €
    )
    c = compute_cost(inp)
    assert c.energy == 0.10
    assert c.filament == 1.00
    assert c.depreciation == 0.20
    assert math.isclose(c.total, 1.30, abs_tol=1e-6)


def test_compute_cost_no_depreciation_when_no_lifetime():
    inp = CostInputs(
        print_duration_hours=10.0,
        machine_purchase_price=999.0,
        machine_lifetime_hours=0.0,  # evita división por cero
    )
    assert compute_cost(inp).depreciation == 0.0


def test_compute_cost_failed_print_still_costs():
    # Impresión cancelada a media: solo consumió algo de energía y filamento.
    inp = CostInputs(
        energy_kwh=0.1,
        electricity_price_per_kwh=0.20,
        filament_weight_g=5,
        filament_price_per_kg=20.0,
        print_duration_hours=0.25,
    )
    c = compute_cost(inp)
    assert c.total > 0
    assert c.energy == 0.02
    assert c.filament == 0.10


def test_compute_cost_empty_is_zero():
    assert compute_cost(CostInputs()).total == 0.0
