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
    # Parte: 100 g PLA estimado, 2 h; báscula 110 g. El MATERIAL se cobra por
    # báscula: 40 × (110/100) = 44. Máquina: potencia por defecto 100 W → luz
    # 0.4/h + máquina 1.0/h = 1.4/h × 2 h = 2.8. Total = 46.8.
    body = {"name": "Trofeo", "total_weight_g": 110, "parts": [
        {"name": "Raqueta", "material_type": "PLA", "weight_g": 100,
         "quantity": 1, "print_time_s": 7200},
    ]}
    r = env.post("/api/projects", json=body)
    assert r.status_code == 201
    d = r.json()
    assert d["material_by_scale"] is True
    assert d["material_cost"] == pytest.approx(44.0, abs=0.01)       # por báscula
    assert d["material_cost_estimated"] == pytest.approx(40.0, abs=0.01)
    assert d["machine_cost"] == pytest.approx(2.8, abs=0.05)
    assert d["cost_total"] == pytest.approx(46.8, abs=0.05)
    # reconciliación: partes 100 g vs producto 110 g → +10.
    assert d["reconciliation"]["sum_parts_g"] == 100.0
    assert d["reconciliation"]["diff_g"] == pytest.approx(10.0, abs=0.01)


def test_sin_bascula_material_estimado(env):
    # Sin peso medido: el material sigue siendo el estimado por partes.
    d = env.post("/api/projects", json={"name": "P", "parts": [
        {"material_type": "PLA", "weight_g": 100, "quantity": 1}]}).json()
    assert d["material_by_scale"] is False
    assert d["material_cost"] == pytest.approx(40.0, abs=0.01)


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


def test_delete_project_part(env):
    pid = env.post("/api/projects", json={"name": "P", "parts": [
        {"material_type": "PLA", "weight_g": 10, "quantity": 1},
        {"material_type": "PLA", "weight_g": 20, "quantity": 1},
    ]}).json()["id"]
    part_id = env.get("/api/projects").json()[0]["parts"][0]["id"]
    assert env.delete(f"/api/project-parts/{part_id}").status_code == 204
    parts = env.get("/api/projects").json()[0]["parts"]
    assert len(parts) == 1 and parts[0]["weight_g"] == 20
    assert env.delete("/api/project-parts/99999").status_code == 404


def test_gastos_extra_suman_al_proyecto(env):
    body = {"name": "P", "parts": [
        {"material_type": "PLA", "weight_g": 100, "quantity": 1}],
        "extra_expenses": [
            {"label": "Tornillos", "amount": 2.5, "quantity": 4},
            {"label": "Pegamento", "amount": 15, "quantity": 1}]}
    d = env.post("/api/projects", json=body).json()
    assert d["extras_cost"] == pytest.approx(25.0, abs=0.01)   # 10 + 15
    # material 0.1×400=40 + extras 25 (sin tiempo → máquina 0)
    assert d["cost_total"] == pytest.approx(65.0, abs=0.01)
    assert len(d["extra_expenses"]) == 2


def test_update_part_quantity(env):
    pid = env.post("/api/projects", json={"name": "P", "parts": [
        {"material_type": "PLA", "weight_g": 100, "quantity": 1, "print_time_s": 0}]}).json()["id"]
    part = env.get("/api/projects").json()[0]["parts"][0]
    assert env.post(f"/api/project-parts/{part['id']}", json={"quantity": 3}).status_code == 200
    d = env.get("/api/projects").json()[0]
    p = d["parts"][0]
    assert p["quantity"] == 3
    assert p["line_cost"] == pytest.approx(p["unit_cost"] * 3, abs=0.01)   # ×3 exacto
    assert env.post(f"/api/project-parts/{part['id']}", json={"quantity": 0}).status_code == 422
    assert env.post("/api/project-parts/99999", json={"quantity": 2}).status_code == 404


def test_incluir_en_la_cuenta(env):
    # Parte excluida y extra excluido: se listan pero no suman al total.
    body = {"name": "P", "parts": [
        {"material_type": "PLA", "weight_g": 100, "quantity": 1, "print_time_s": 0},
        {"material_type": "PLA", "weight_g": 50, "quantity": 1, "print_time_s": 0,
         "included": False},
    ], "extra_expenses": [
        {"label": "Argollas", "amount": 10, "quantity": 1},
        {"label": "Prueba descartada", "amount": 99, "quantity": 1, "included": False},
    ]}
    d = env.post("/api/projects", json=body).json()
    # material: solo la parte incluida (0.1 × 400 = 40); extras: solo 10.
    assert d["material_cost"] == pytest.approx(40.0, abs=0.01)
    assert d["extras_cost"] == pytest.approx(10.0, abs=0.01)
    assert d["cost_total"] == pytest.approx(50.0, abs=0.01)
    assert d["parts"][1]["included"] is False
    assert d["parts"][1]["line_cost"] > 0          # se muestra su coste igualmente
    assert d["reconciliation"]["sum_parts_g"] == 100.0   # la excluida no pesa
    # toggle por endpoint: incluir la parte excluida
    pid = d["id"]; part_id = d["parts"][1]["id"]
    assert env.post(f"/api/project-parts/{part_id}", json={"included": True}).status_code == 200
    d2 = env.get("/api/projects").json()[0]
    assert d2["material_cost"] == pytest.approx(60.0, abs=0.01)   # 40 + 20
    # toggle del extra caro
    assert env.post(f"/api/projects/{pid}/extra-included",
                    json={"index": 1, "included": True}).status_code == 200
    d3 = env.get("/api/projects").json()[0]
    assert d3["extras_cost"] == pytest.approx(109.0, abs=0.01)
    assert env.post(f"/api/projects/{pid}/extra-included",
                    json={"index": 9, "included": True}).status_code == 404


def test_piezas_por_cama_divide(env):
    # Gcode de 100 g / 1 h con 4 piezas por cama → por pieza: material 10, y el
    # peso reconciliado es 25 g. line = unit × qty.
    body = {"name": "P", "total_weight_g": None, "parts": [
        {"material_type": "PLA", "weight_g": 100, "quantity": 1,
         "print_time_s": 3600, "pieces_per_bed": 4}]}
    d = env.post("/api/projects", json=body).json()
    p = d["parts"][0]
    assert p["pieces_per_bed"] == 4
    # material por pieza: (0.1 × 400)/4 = 10; máquina: 1.4/4 = 0.35
    assert p["material"] == pytest.approx(10.0, abs=0.01)
    assert p["machine"] == pytest.approx(0.35, abs=0.02)
    assert d["reconciliation"]["sum_parts_g"] == pytest.approx(25.0, abs=0.1)
    # cambiar por endpoint a 2 → material 20
    assert env.post(f"/api/project-parts/{p['id']}", json={"pieces_per_bed": 2}).status_code == 200
    d2 = env.get("/api/projects").json()[0]
    assert d2["parts"][0]["material"] == pytest.approx(20.0, abs=0.01)
    assert env.post(f"/api/project-parts/{p['id']}", json={"pieces_per_bed": 0}).status_code == 422


def test_parse_gcode_name():
    from app.services.gcode_name import parse_gcode_name
    # Formato actual: todos los campos entre paréntesis.
    d = parse_gcode_name(
        "Gcode (Maceta Suculenta) (Plate 1) (4obj) (4x) (25431mm3) (PLA) (3h37m) "
        "(~0.20mm Standard) (@Voron 2.4 0.4 Nozzle).gcode")
    assert d == {"base": "Maceta Suculenta", "plate": "Plate 1", "objects": 4,
                 "instances": 4, "volume_mm3": 25431, "filament_type": "PLA",
                 "print_time": "3h37m", "process": "0.20mm Standard",
                 "preset": "Voron 2.4 0.4 Nozzle"}
    # sin proceso, base con guiones y paréntesis dentro del proceso/preset raros
    d = parse_gcode_name(
        "sub/Gcode (Trofeo - Base v2) (Plate 12) (12obj) (12x) (8000mm3) (PLA+) (44m1s) "
        "(@Creality Hi).gcode")
    assert d["base"] == "Trofeo - Base v2" and d["objects"] == 12
    assert d["process"] is None
    # proceso que contiene "@" (habitual en perfiles de Orca)
    d = parse_gcode_name(
        "Gcode (Llavero) (Plate 1) (6obj) (6x) (9000mm3) (PLA) (1h2m) "
        "(~0.20mm Standard @Voron) (@Voron 2.4 0.4 Nozzle).gcode")
    assert d["process"] == "0.20mm Standard @Voron"
    assert d["preset"] == "Voron 2.4 0.4 Nozzle"
    # FORMA CORTA (límite de 255 bytes en la impresora): sin Mx, mm3 ni @preset
    d = parse_gcode_name(
        "Gcode (Miss_Fanny) (Fornite Mario Demo) (2obj) (PLA) (52m28s) "
        "(~process template @Creality Creality Hi 0.4 nozzle).gcode")
    assert d["base"] == "Miss_Fanny" and d["plate"] == "Fornite Mario Demo"
    assert d["objects"] == 2 and d["filament_type"] == "PLA"
    assert d["process"] == "process template @Creality Creality Hi 0.4 nozzle"
    assert d["instances"] is None and d["volume_mm3"] is None and d["preset"] is None
    # corta sin proceso
    d = parse_gcode_name("Gcode (Cubo) () (1obj) (PA-CF) (1d2h5m).gcode")
    assert d["objects"] == 1 and d["process"] is None and d["preset"] is None
    # Compatibilidad: estilo previo (campos con sufijo sin paréntesis, ~/@ a pelo).
    d = parse_gcode_name(
        "Gcode Maceta Suculenta (Plate 1) 4obj 4x 25431mm3 PLA 3h37m @Voron 2.4 0.4 Nozzle.gcode")
    assert d["base"] == "Maceta Suculenta" and d["objects"] == 4 and d["process"] is None
    d = parse_gcode_name(
        "Gcode Llavero (Plate 1) 6obj 6x 9000mm3 PLA 1h2m ~0.16mm Detalle @Creality Hi.gcode")
    assert d["process"] == "0.16mm Detalle" and d["preset"] == "Creality Hi"
    # patrón viejo (plate y objetos pegados) y nombres libres → None
    assert parse_gcode_name(
        "Gcode Maceta - Plate 14x Objects 4x Instances - 25431mm3 - PLA - 3h37m@ Voron.gcode") is None
    assert parse_gcode_name("benchy.gcode") is None
    assert parse_gcode_name(None) is None


def test_add_job_precarga_piezas_por_cama(tmp_path):
    # Nombre con la convención estándar → pieces_per_bed = num_objects del nombre.
    from datetime import datetime, timezone
    from app.models import PrintJob
    engine = create_engine(f"sqlite:///{tmp_path / 'jb.db'}")
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine)
    fname = "Gcode Llavero (Plate 1) 6obj 6x 9000mm3 PLA 1h2m @Voron.gcode"
    with Local() as s:
        s.add(Printer(id=1, name="A", host="1"))
        s.add(PrintJob(id=9, printer_id=1, moonraker_job_id="1", filename=fname,
                       status="completed", print_duration_s=3720, filament_weight_g=54,
                       start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                       end_time=datetime(2026, 1, 1, 2, tzinfo=timezone.utc)))
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
    assert part["pieces_per_bed"] == 6
    dbmod._engine = orig
    dbmod.SessionLocal.configure(bind=orig)
