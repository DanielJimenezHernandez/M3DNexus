"""Tests del motor de costes (funciones puras)."""

import math

from app.cost import (
    CostInputs,
    compute_cost,
    mm_to_grams,
)
from app.models import Printer


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


def test_resale_reduces_depreciation():
    # Solo se deprecia (precio − reventa): 500 − 300 = 200 sobre 5000 h = 0.04/h.
    inp = CostInputs(
        print_duration_hours=2.0,
        machine_purchase_price=500.0,
        machine_resale_value=300.0,
        machine_lifetime_hours=5000.0,
    )
    c = compute_cost(inp)
    assert math.isclose(c.depreciation, 0.08, abs_tol=1e-6)


def test_resale_above_price_never_negative():
    # Si la reventa supera el precio no se deprecia por debajo de 0.
    inp = CostInputs(
        print_duration_hours=10.0,
        machine_purchase_price=100.0,
        machine_resale_value=500.0,
        machine_lifetime_hours=1000.0,
    )
    assert compute_cost(inp).depreciation == 0.0


def test_maintenance_is_a_separate_component():
    inp = CostInputs(
        print_duration_hours=4.0,
        maintenance_per_hour=0.25,   # 4 h × 0.25 = 1.00
    )
    c = compute_cost(inp)
    assert c.maintenance == 1.00
    assert c.depreciation == 0.0
    assert math.isclose(c.total, 1.00, abs_tol=1e-6)


def test_maintenance_and_depreciation_both_in_total():
    inp = CostInputs(
        energy_kwh=0.5,
        electricity_price_per_kwh=0.20,          # 0.10
        filament_weight_g=50,
        filament_price_per_kg=20.0,              # 1.00
        print_duration_hours=2.0,
        machine_purchase_price=500.0,
        machine_resale_value=100.0,
        machine_lifetime_hours=5000.0,           # (400/5000)*2 = 0.16
        maintenance_per_hour=0.10,               # 2*0.10 = 0.20
    )
    c = compute_cost(inp)
    assert c.energy == 0.10
    assert c.filament == 1.00
    assert math.isclose(c.depreciation, 0.16, abs_tol=1e-6)
    assert math.isclose(c.maintenance, 0.20, abs_tol=1e-6)
    assert math.isclose(c.total, 1.46, abs_tol=1e-6)


def test_negative_maintenance_clamped_to_zero():
    inp = CostInputs(print_duration_hours=3.0, maintenance_per_hour=-1.0)
    assert compute_cost(inp).maintenance == 0.0


def test_machine_per_hour_clamps_negative_maintenance():
    # (1000 − 0)/1000 h = 1.0/h de amortización; el mantenimiento negativo no resta.
    p = Printer(purchase_price=1000.0, resale_value=0.0, amortization_years=1,
                active_days_per_year=100, active_hours_per_day=10,  # 1000 h
                maintenance_per_hour=-5.0)
    assert p.amortization_per_hour == 1.0
    assert p.machine_per_hour == 1.0   # no baja de la amortización
