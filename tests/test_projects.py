"""Tests de proyectos: precio medio por tipo y coste real (material + máquina)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Material, Printer, Setting


@pytest.fixture
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine)
    with Local() as s:
        # Impresora con coste de máquina 1.0/h (1000 / (1×100×10 = 1000 h)).
        s.add(Printer(name="A", host="1", purchase_price=1000, resale_value=0,
                      amortization_years=1, active_days_per_year=100,
                      active_hours_per_day=10, maintenance_per_hour=0))
        s.add(Material(name="PLA x", material_type="PLA", price_per_kg=350))
        s.add(Material(name="PLA y", material_type="PLA", price_per_kg=450))  # promedio 400
        s.add(Material(name="Sin precio", material_type="PETG", price_per_kg=0))
        s.add(Setting(key="electricity_price_per_kwh", value="4"))
        s.add(Setting(key="currency", value="$"))
        s.commit()
    import app.db as dbmod
    orig = dbmod._engine
    dbmod._engine = engine
    dbmod.SessionLocal.configure(bind=engine)
    from fastapi.testclient import TestClient
    from app.main import app
    yield TestClient(app)
    dbmod._engine = orig
    dbmod.SessionLocal.configure(bind=orig)


def test_precio_medio_por_tipo(env):
    d = env.get("/api/projects/material-prices").json()
    assert d["prices"]["PLA"] == 400.0    # (350 + 450)/2
    assert "PETG" not in d["prices"]       # sin precio > 0 no cuenta


def test_coste_material_mas_maquina(env):
    # Parte: 100 g PLA, 2 h. Material = 0.1 × 400 = 40. Sin energía medida, la
    # potencia cae al defecto 100 W → luz 0.4/h; máquina 1.0/h → flota 1.4/h.
    # Máquina = 2 h × 1.4 = 2.8. Total = 42.8.
    body = {"name": "Trofeo", "total_weight_g": 110, "parts": [
        {"name": "Raqueta", "material_type": "PLA", "weight_g": 100,
         "quantity": 1, "print_time_s": 7200},
    ]}
    r = env.post("/api/projects", json=body)
    assert r.status_code == 201
    d = r.json()
    assert d["material_cost"] == pytest.approx(40.0, abs=0.01)
    assert d["machine_cost"] == pytest.approx(2.8, abs=0.05)
    assert d["cost_total"] == pytest.approx(42.8, abs=0.05)
    # reconciliación: partes 100 g vs producto 110 g → +10.
    assert d["reconciliation"]["sum_parts_g"] == 100.0
    assert d["reconciliation"]["diff_g"] == pytest.approx(10.0, abs=0.01)


def test_cantidad_multiplica(env):
    body = {"name": "P", "parts": [
        {"material_type": "PLA", "weight_g": 50, "quantity": 4, "print_time_s": 0},
    ]}
    d = env.post("/api/projects", json=body).json()
    # 0.05 kg × 400 = 20 por unidad × 4 = 80; sin tiempo → máquina 0.
    assert d["material_cost"] == pytest.approx(80.0, abs=0.01)
    assert d["machine_cost"] == 0.0
    assert d["parts"][0]["line_cost"] == pytest.approx(80.0, abs=0.01)


def test_editar_y_borrar(env):
    pid = env.post("/api/projects", json={"name": "X", "parts": []}).json()["id"]
    r = env.put(f"/api/projects/{pid}", json={"name": "X2", "parts": [
        {"material_type": "PLA", "weight_g": 10, "quantity": 1}]})
    assert r.status_code == 200 and r.json()["name"] == "X2"
    assert len(r.json()["parts"]) == 1
    assert env.delete(f"/api/projects/{pid}").status_code == 204
    assert env.get("/api/projects").json() == []


def test_gcode_info_del_ultimo_print(tmp_path):
    from datetime import datetime, timezone
    from app.models import PrintJob
    engine = create_engine(f"sqlite:///{tmp_path / 'g.db'}")
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine)
    with Local() as s:
        s.add(Printer(id=1, name="A", host="1"))
        s.add(Material(id=5, name="PETG x", material_type="PETG", price_per_kg=400))
        s.add(PrintJob(printer_id=1, moonraker_job_id="1", filename="parte.gcode",
                       material_id=5, status="completed", print_duration_s=5400,
                       filament_weight_g=42,
                       start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                       end_time=datetime(2026, 1, 2, tzinfo=timezone.utc)))
        s.commit()
    import app.db as dbmod
    orig = dbmod._engine
    dbmod._engine = engine
    dbmod.SessionLocal.configure(bind=engine)
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    d = c.get("/api/gcode-info?printer_id=1&filename=parte.gcode").json()
    assert d["found"] is True
    assert d["material_type"] == "PETG"
    assert d["print_time_s"] == 5400   # tiempo REAL del print
    assert c.get("/api/gcode-info?filename=noexiste.gcode").json()["found"] is False
    dbmod._engine = orig
    dbmod.SessionLocal.configure(bind=orig)


def test_reconciliacion_factor_de_calibracion(env):
    body = {"name": "P", "total_weight_g": 110, "parts": [
        {"material_type": "PLA", "weight_g": 100, "quantity": 1}]}
    rec = env.post("/api/projects", json=body).json()["reconciliation"]
    assert rec["error_pct"] == pytest.approx(10.0, abs=0.1)   # báscula 10% > estimado
    assert rec["factor"] == pytest.approx(1.1, abs=0.01)       # ×1.1 para calibrar


def test_add_job_como_parte(tmp_path):
    from datetime import datetime, timezone
    from app.models import PrintJob
    engine = create_engine(f"sqlite:///{tmp_path / 'j.db'}")
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine)
    with Local() as s:
        s.add(Printer(id=1, name="A", host="1"))
        s.add(Material(id=5, name="PLA x", material_type="PLA", price_per_kg=350))
        s.add(PrintJob(id=9, printer_id=1, moonraker_job_id="1", filename="parte.gcode",
                       material_id=5, status="completed", print_duration_s=5400,
                       filament_weight_g=42,
                       start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                       end_time=datetime(2026, 1, 2, tzinfo=timezone.utc)))
        s.commit()
    import app.db as dbmod
    orig = dbmod._engine
    dbmod._engine = engine
    dbmod.SessionLocal.configure(bind=engine)
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    pid = c.post("/api/projects", json={"name": "P", "parts": []}).json()["id"]
    assert c.post(f"/api/projects/{pid}/add-job", json={"job_id": 9}).status_code == 201
    part = c.get("/api/projects").json()[0]["parts"][0]
    assert part["gcode_filename"] == "parte.gcode"
    assert part["material_type"] == "PLA"
    assert part["weight_g"] == 42        # peso estimado del slicer
    assert part["print_time_s"] == 5400  # tiempo REAL
    dbmod._engine = orig
    dbmod.SessionLocal.configure(bind=orig)
