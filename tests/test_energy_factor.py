"""Factor de operación eléctrica: multiplica el coste de luz, nunca el kWh."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.cost import CostInputs, compute_cost
from app.db import Base
from app.models import Material, Printer, Setting


@pytest.fixture
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'f.db'}")
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine)
    with Local() as s:
        s.add(Printer(name="A", host="1", purchase_price=0, resale_value=0,
                      amortization_years=1, active_days_per_year=100,
                      active_hours_per_day=10, maintenance_per_hour=0))
        s.add(Material(name="PLA x", material_type="PLA", price_per_kg=400))
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


def test_compute_cost_aplica_el_factor():
    base = CostInputs(energy_kwh=1.0, electricity_price_per_kwh=4.0,
                      filament_weight_g=100, filament_price_per_kg=400)
    con = CostInputs(energy_kwh=1.0, electricity_price_per_kwh=4.0,
                     filament_weight_g=100, filament_price_per_kg=400,
                     energy_overhead_factor=2.5)
    b, c = compute_cost(base), compute_cost(con)
    assert b.energy == 4.0 and c.energy == 10.0     # solo la luz cambia
    assert b.filament == c.filament == 40.0
    assert c.total == pytest.approx(b.total + 6.0)


def test_factor_por_defecto_es_neutro():
    inp = CostInputs(energy_kwh=2.0, electricity_price_per_kwh=3.0)
    assert compute_cost(inp).energy == 6.0


def test_settings_roundtrip(env):
    # Sin fila (BD nueva sin migración): 1.0 neutro.
    assert env.get("/api/settings").json()["energy_overhead_factor"] == 1.0
    d = env.put("/api/settings", json={"energy_overhead_factor": 2.5}).json()
    assert d["energy_overhead_factor"] == 2.5
    assert env.get("/api/settings").json()["energy_overhead_factor"] == 2.5
    # Negativo no se acepta.
    assert env.put("/api/settings", json={"energy_overhead_factor": -1}).status_code == 422


def test_fleet_por_hora_aplica_el_factor(env):
    from app.db import SessionLocal
    from app.services.estimate import fleet_cost_per_hour
    with SessionLocal() as s:
        antes = fleet_cost_per_hour(s, "PLA", 4.0)["machines"][0]["elec_per_h"]
    env.put("/api/settings", json={"energy_overhead_factor": 2.0})
    with SessionLocal() as s:
        despues = fleet_cost_per_hour(s, "PLA", 4.0)["machines"][0]["elec_per_h"]
    assert despues == pytest.approx(antes * 2.0)


def test_get_energy_factor_valores_raros(env):
    from app.db import SessionLocal
    from app.services.ingest import get_energy_factor
    with SessionLocal() as s:
        s.add(Setting(key="energy_overhead_factor", value="no-numero"))
        s.commit()
        assert get_energy_factor(s) == 1.0   # basura → neutro, no revienta
