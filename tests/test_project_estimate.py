"""Tests del promedio de flota y la estimación de proyecto multi-gcode."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Material, PrintJob, Printer, Setting
from app.services.estimate import estimate_project, fleet_cost_per_hour

WHEN = datetime(2026, 7, 1, tzinfo=timezone.utc)


@pytest.fixture
def Session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    pla = Material(name="PLA", material_type="PLA", price_per_kg=300, density_g_cm3=1.24)
    # Ender barata (máquina 1.0/h) muy usada; Voron cara (3.0/h) poco usada.
    ender = Printer(name="Ender", host="1", purchase_price=1000, resale_value=0,
                    amortization_years=1, active_days_per_year=100,
                    active_hours_per_day=10, maintenance_per_hour=0.0)   # 1000 h → 1.0/h
    voron = Printer(name="Voron", host="2", purchase_price=3000, resale_value=0,
                    amortization_years=1, active_days_per_year=100,
                    active_hours_per_day=10, maintenance_per_hour=0.0)   # 1000 h → 3.0/h
    s.add_all([pla, ender, voron,
               Setting(key="electricity_price_per_kwh", value="4"),
               Setting(key="currency", value="$")])
    s.commit()
    # Ambas miden 100 W en PLA; Ender con 300 h de uso, Voron con 100 h.
    s.add(PrintJob(printer_id=ender.id, moonraker_job_id="e", material_id=pla.id,
                   status="completed", print_duration_s=300 * 3600, energy_kwh=30.0,
                   start_time=WHEN - timedelta(hours=1), end_time=WHEN))
    s.add(PrintJob(printer_id=voron.id, moonraker_job_id="v", material_id=pla.id,
                   status="completed", print_duration_s=100 * 3600, energy_kwh=10.0,
                   start_time=WHEN - timedelta(hours=1), end_time=WHEN))
    s.commit()
    yield s
    s.close()


def test_fleet_ponderado_por_uso(Session):
    s = Session
    fl = fleet_cost_per_hour(s, "PLA", price_per_kwh=4.0, weighting="usage")
    # total/h: Ender 1.0+0.4=1.4 (300 h), Voron 3.0+0.4=3.4 (100 h).
    # ponderado = (1.4*300 + 3.4*100)/400 = 1.9
    assert fl["avg_per_h"] == pytest.approx(1.9, abs=1e-3)
    assert fl["weighted"] is True
    assert fl["min_per_h"] == pytest.approx(1.4, abs=1e-3)
    assert fl["min_machine"] == "Ender"
    assert fl["max_per_h"] == pytest.approx(3.4, abs=1e-3)
    assert fl["max_machine"] == "Voron"
    assert fl["n_machines"] == 2


def test_fleet_simple_pesa_igual(Session):
    s = Session
    fl = fleet_cost_per_hour(s, "PLA", price_per_kwh=4.0, weighting="simple")
    # simple = (1.4 + 3.4)/2 = 2.4
    assert fl["avg_per_h"] == pytest.approx(2.4, abs=1e-3)
    assert fl["weighted"] is False


def test_deshabilitada_no_cuenta(Session):
    s = Session
    s.query(Printer).filter_by(name="Voron").one().enabled = False
    s.commit()
    fl = fleet_cost_per_hour(s, "PLA", price_per_kwh=4.0, weighting="usage")
    assert fl["n_machines"] == 1
    assert fl["avg_per_h"] == pytest.approx(1.4, abs=1e-3)   # solo la Ender


def test_estimate_project_multi_archivo(Session):
    s = Session
    pla = s.query(Material).filter_by(name="PLA").one()
    files = [
        {"filename": "a.gcode", "weight_g": 100, "time_s": 3600,
         "quantity": 2, "material_id": pla.id},
        {"filename": "b.gcode", "weight_g": 50, "time_s": 1800,
         "quantity": 1, "material_id": pla.id},
    ]
    d = estimate_project(s, files, weighting="usage")
    a, b = d["files"]
    # a: filamento 0.1kg*300=30 + flota 1.9/h * 1h = 31.9; ×2 = 63.8
    assert a["filament"] == pytest.approx(30.0, abs=1e-2)
    assert a["unit_cost"] == pytest.approx(31.9, abs=1e-2)
    assert a["line_cost"] == pytest.approx(63.8, abs=1e-2)
    assert a["unit_low"] == pytest.approx(31.4, abs=1e-2)    # 30 + 1.4
    assert a["unit_high"] == pytest.approx(33.4, abs=1e-2)   # 30 + 3.4
    # b: 0.05kg*300=15 + 1.9*0.5=0.95 = 15.95
    assert b["unit_cost"] == pytest.approx(15.95, abs=1e-2)
    # total del producto = 63.8 + 15.95 = 79.75
    assert d["cost_total"] == pytest.approx(79.75, abs=1e-2)
    assert d["cost_low"] < d["cost_total"] < d["cost_high"]


def test_material_borrado_marca_no_price(Session):
    # Un material_id que ya no existe: filamento 0 pero SE AVISA (no silencioso).
    s = Session
    d = estimate_project(s, [
        {"filename": "x.gcode", "weight_g": 200, "time_s": 3600,
         "quantity": 1, "material_id": 99999},
    ], weighting="usage")
    f = d["files"][0]
    assert f["no_price"] is True
    assert f["filament"] == 0.0


def test_peso_y_tiempo_negativos_acotados(Session):
    s = Session
    pla = s.query(Material).filter_by(name="PLA").one()
    d = estimate_project(s, [
        {"filename": "x.gcode", "weight_g": -100, "time_s": -3600,
         "quantity": 1, "material_id": pla.id},
    ], weighting="usage")
    f = d["files"][0]
    assert f["unit_cost"] >= 0
    assert f["unit_low"] <= f["unit_high"]   # rango coherente


def test_ponderacion_usa_horas_crudas_no_redondeadas(tmp_path):
    # Máquinas con uso sub-hora: el peso NO debe redondearse a décimas (eso
    # anularía una máquina o distorsionaría el promedio).
    engine = create_engine(f"sqlite:///{tmp_path / 'w.db'}")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    pla = Material(name="PLA", material_type="PLA", price_per_kg=0)
    ender = Printer(name="Ender", host="1", purchase_price=1000, resale_value=0,
                    amortization_years=1, active_days_per_year=100,
                    active_hours_per_day=10)   # 1.0/h
    voron = Printer(name="Voron", host="2", purchase_price=3000, resale_value=0,
                    amortization_years=1, active_days_per_year=100,
                    active_hours_per_day=10)   # 3.0/h
    s.add_all([pla, ender, voron])
    s.commit()
    # Ender 144 s (0.04 h) · Voron 216 s (0.06 h), ambos 100 W.
    s.add(PrintJob(printer_id=ender.id, moonraker_job_id="e", material_id=pla.id,
                   status="completed", print_duration_s=144, energy_kwh=0.004))
    s.add(PrintJob(printer_id=voron.id, moonraker_job_id="v", material_id=pla.id,
                   status="completed", print_duration_s=216, energy_kwh=0.006))
    s.commit()
    fl = fleet_cost_per_hour(s, "PLA", price_per_kwh=4.0, weighting="usage")
    # total/h: Ender 1.4, Voron 3.4 · ponderado por horas crudas =
    # (1.4*0.04 + 3.4*0.06)/0.10 = 2.6  (con redondeo a décimas daría 3.4)
    assert fl["avg_per_h"] == pytest.approx(2.6, abs=0.03)
    s.close()


def test_material_sin_precio_marca_no_price(Session):
    s = Session
    free = Material(name="PLA sin precio", material_type="PLA", price_per_kg=0)
    s.add(free); s.commit()
    d = estimate_project(s, [
        {"filename": "x.gcode", "weight_g": 100, "time_s": 3600,
         "quantity": 1, "material_id": free.id},
    ], weighting="usage")
    f = d["files"][0]
    assert f["no_price"] is True
    assert f["filament"] == 0.0            # sin precio → filamento 0
    assert f["unit_cost"] == pytest.approx(1.9, abs=1e-2)   # solo flota