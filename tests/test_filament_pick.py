"""Tests de elegir el filamento del gcode (multi-material) y crearlo si falta."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Material, PrintJob, Printer
from app.services.filament import (
    get_or_create_by_parsed,
    parse_all_filaments,
    parse_filament,
)

# Metadata real de un proyecto Voron multi-material (dos filamentos).
MULTI_NAME = (
    'Elegoo PLA+ Black @ Voron 2.4.1 0.4 Nozzle";"'
    'Elegoo PLA Orange  @ Voron 2.4 0.4 Nozzle'
)
META = {"filament_name": MULTI_NAME, "filament_type": "PLA;PLA"}


def test_parse_all_saca_los_dos():
    fils = parse_all_filaments(META)
    names = [f.full_name for f in fils]
    assert names == ["Elegoo PLA+ Black", "Elegoo PLA Orange"]
    assert fils[1].color == "Orange"
    assert fils[1].material_type == "PLA"


def test_parse_all_gcode_de_un_filamento():
    fils = parse_all_filaments({"filament_name": "Elegoo PLA+ Black @ Voron 0.4",
                                "filament_type": "PLA"})
    assert [f.full_name for f in fils] == ["Elegoo PLA+ Black"]


def test_get_or_create_crea_y_reutiliza(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    parsed = parse_filament("Elegoo PLA Orange @ Voron 0.4", "PLA")
    m1 = get_or_create_by_parsed(s, parsed)
    assert m1.name == "Elegoo PLA Orange"
    assert m1.color == "Orange"
    assert m1.auto_created is True
    assert m1.price_per_kg == 0.0
    # Segunda vez: reutiliza, no duplica.
    m2 = get_or_create_by_parsed(s, parse_filament("Elegoo PLA Orange @ otra", "PLA"))
    assert m2.id == m1.id
    assert s.query(Material).filter_by(name="Elegoo PLA Orange").count() == 1
    s.close()


@pytest.fixture
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine)
    with Local() as s:
        s.add(Printer(id=1, name="Voron 2.4", host="10.0.0.1"))
        # El "Black" ya existe (lo cogió el auto-detector); el "Orange" no.
        s.add(Material(id=66, name="Elegoo PLA+ Black", material_type="PLA",
                       color="Black", price_per_kg=350))
        s.add(PrintJob(id=600, printer_id=1, moonraker_job_id="600",
                       filename="Trofeo.gcode", status="completed", material_id=66,
                       print_duration_s=4380, filament_weight_g=23.6, raw_metadata=META,
                       start_time=datetime(2026, 8, 7, tzinfo=timezone.utc),
                       end_time=datetime(2026, 8, 7, 1, tzinfo=timezone.utc)))
        s.commit()

    import app.db as dbmod
    orig = dbmod._engine
    dbmod._engine = engine
    dbmod.SessionLocal.configure(bind=engine)
    from fastapi.testclient import TestClient
    from app.main import app
    yield TestClient(app), Local
    dbmod._engine = orig
    dbmod.SessionLocal.configure(bind=orig)


def test_endpoint_lista_filamentos(env):
    client, _ = env
    r = client.get("/api/jobs/600/filaments")
    assert r.status_code == 200
    d = r.json()
    names = [f["name"] for f in d["filaments"]]
    assert names == ["Elegoo PLA+ Black", "Elegoo PLA Orange"]
    black = d["filaments"][0]
    orange = d["filaments"][1]
    assert black["material_id"] == 66 and black["assigned"] is True
    assert orange["material_id"] is None          # aún no existe → habría que crear


def test_endpoint_set_filament_crea_y_asigna(env):
    client, Local = env
    r = client.post("/api/jobs/600/set-filament", json={"name": "Elegoo PLA Orange"})
    assert r.status_code == 200
    job = r.json()
    # Se creó el material y quedó asignado al job.
    with Local() as s:
        orange = s.query(Material).filter_by(name="Elegoo PLA Orange").one()
        assert job["material_id"] == orange.id
        assert orange.color == "Orange"
        assert orange.auto_created is True


def test_endpoint_set_filament_rechaza_ajeno(env):
    client, _ = env
    r = client.post("/api/jobs/600/set-filament", json={"name": "Filamento Inventado"})
    assert r.status_code == 404
