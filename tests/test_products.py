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


def test_venta_descuenta_stock_y_congela_lineas(env):
    p = env.post("/api/products", json={"name": "Llavero", "price": 80,
                                        "stock": 5}).json()
    r = env.post("/api/sales", json={
        "items": [{"product_id": p["id"], "qty": 2},
                  {"name": "Moño de regalo", "price": 10, "qty": 1}],
        "payment_method": "efectivo", "event": "Bazar Nov"})
    assert r.status_code == 201
    s = r.json()
    assert s["total"] == 170.0                   # 2×80 + 10
    assert s["event"] == "Bazar Nov"
    assert s["items"][0]["sku"] == p["sku"]      # línea congelada con SKU
    assert s["items"][1]["product_id"] is None   # línea manual
    # Stock descontado.
    assert env.get(f"/api/products/by-sku/{p['sku']}").json()["stock"] == 3
    # Precio regateado por línea gana sobre el del catálogo.
    s2 = env.post("/api/sales", json={
        "items": [{"product_id": p["id"], "qty": 1, "price": 70}]}).json()
    assert s2["total"] == 70.0
    # Deshacer la primera venta devuelve su stock (3 + 2 = 5... menos la 2ª).
    env.delete(f"/api/sales/{s['id']}")
    assert env.get(f"/api/products/by-sku/{p['sku']}").json()["stock"] == 4
    # Venta vacía no pasa; producto inexistente tampoco.
    assert env.post("/api/sales", json={"items": []}).status_code == 422
    assert env.post("/api/sales", json={
        "items": [{"product_id": 999, "qty": 1}]}).status_code == 404


def test_resumen_por_evento_y_csv(env):
    p = env.post("/api/products", json={"name": "Llavero", "price": 80,
                                        "stock": 20}).json()
    env.post("/api/sales", json={"items": [{"product_id": p["id"], "qty": 2}],
                                 "payment_method": "efectivo", "event": "Bazar Nov"})
    env.post("/api/sales", json={"items": [{"product_id": p["id"], "qty": 1},
                                           {"name": "Moño", "price": 10, "qty": 1}],
                                 "payment_method": "tarjeta", "event": "Bazar Nov"})
    env.post("/api/sales", json={"items": [{"product_id": p["id"], "qty": 1}]})

    d = env.get("/api/sales/summary").json()
    evs = {g["event"]: g for g in d["events"]}
    g = evs["Bazar Nov"]
    assert g["n_sales"] == 2 and g["pieces"] == 4
    assert g["total"] == 250.0                       # 160 + 80 + 10
    assert g["by_payment"] == {"efectivo": 160.0, "tarjeta": 90.0}
    # Items agregados: 3 llaveros juntos, ordenados por importe.
    assert g["items"][0]["sku"] == p["sku"] and g["items"][0]["qty"] == 3
    assert g["items"][0]["amount"] == 240.0
    assert g["items"][1]["name"] == "Moño"
    assert len(g["sales"]) == 2                      # detalle de cada cobro
    # La venta sin evento cae en su cajón mensual.
    assert any(k.startswith("Sin evento · ") for k in evs)

    csv_text = env.get("/api/sales.csv").text
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("mes,fecha,hora,evento,producto,sku")
    assert len(lines) == 1 + 4                       # una fila por línea vendida
    assert sum("Bazar Nov" in ln for ln in lines) == 3


def test_ventas_entran_a_la_rentabilidad(env):
    p = env.post("/api/products", json={"name": "Figura", "price": 100,
                                        "stock": 10}).json()
    env.post("/api/sales", json={"items": [{"product_id": p["id"], "qty": 3}]})
    d = env.get("/api/expenses/summary").json()
    import datetime as _dt
    key = _dt.datetime.now().strftime("%Y-%m")
    m = next(x for x in d["months"] if x["month"] == key)
    assert m["sales"] == 300.0
    assert m["profit"] == m["income"] + 300.0 - m["expenses"]
