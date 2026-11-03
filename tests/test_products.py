"""Catálogo de productos: CRUD, SKU auto, coste heredado del proyecto, fotos."""

import base64

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db import Base
from app.models import Material, Printer, Setting

PNG_1PX = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d4944415478da63fcff9fa10e0003030101e0a2bda40000000049454e44ae426082"
    )
).decode()


@pytest.fixture
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}")
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine)
    with Local() as s:
        s.add(Printer(name="A", host="1", purchase_price=1000, resale_value=0,
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


def test_crud_y_sku_auto(env):
    r = env.post("/api/products", json={"name": "Llavero pulpo", "price": 80,
                                        "stock": 5, "category": "Llaveros"})
    assert r.status_code == 201
    p = r.json()
    assert p["sku"] == f"M3D-{p['id']:04d}"     # SKU autogenerado
    assert p["margin"] is None                   # sin coste de referencia
    # Lookup por SKU (el del QR), insensible a mayúsculas.
    assert env.get(f"/api/products/by-sku/{p['sku'].lower()}").json()["id"] == p["id"]
    assert env.get("/api/products/by-sku/NOEXISTE").status_code == 404
    # SKU manual duplicado → 409.
    r2 = env.post("/api/products", json={"name": "Otro", "sku": p["sku"]})
    assert r2.status_code == 409
    # Editar con coste manual: margen calculado.
    r3 = env.put(f"/api/products/{p['id']}", json={"name": "Llavero pulpo",
                                                   "price": 80, "cost_ref": 20})
    assert r3.json()["margin"] == 60.0 and r3.json()["margin_pct"] == 75.0
    assert env.delete(f"/api/products/{p['id']}").status_code == 204
    assert all(x["id"] != p["id"] for x in env.get("/api/products").json())


def test_coste_heredado_del_proyecto(env):
    # Proyecto: 100 g PLA (40) + 2 h × 1.4/h (2.8) = 42.8 de coste real.
    pr = env.post("/api/projects", json={"name": "Pulpo", "parts": [
        {"material_type": "PLA", "weight_g": 100, "quantity": 1,
         "print_time_s": 7200}]}).json()
    p = env.post("/api/products", json={"name": "Pulpo XL", "price": 100,
                                        "project_id": pr["id"]}).json()
    assert p["cost"] == pytest.approx(pr["cost_total"], abs=0.01)
    assert p["margin"] == pytest.approx(100 - pr["cost_total"], abs=0.01)
    assert p["project_name"] == "Pulpo"
    # El coste manual gana sobre el del proyecto.
    p2 = env.put(f"/api/products/{p['id']}", json={
        "name": "Pulpo XL", "price": 100, "project_id": pr["id"], "cost_ref": 10}).json()
    assert p2["cost"] == 10.0
    # Proyecto inexistente → 404.
    assert env.post("/api/products", json={"name": "x", "project_id": 999}).status_code == 404


def test_fotos_de_producto(env):
    p = env.post("/api/products", json={"name": "Figura", "price": 50}).json()
    r = env.post("/api/entity-photos", json={
        "entity_type": "product", "entity_id": p["id"],
        "data_uri": f"data:image/png;base64,{PNG_1PX}"})
    assert r.status_code == 201
    assert env.get("/api/products").json()[0]["photo_url"]
    env.delete(f"/api/products/{p['id']}")
    assert env.get(
        f"/api/entity-photos?entity_type=product&entity_id={p['id']}").json() == []
