"""Gastos del negocio: CRUD, rentabilidad mensual, CSV y foto del recibo."""

import base64
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db import Base
from app.models import Order, Setting

PNG_1PX = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d4944415478da63fcff9fa10e0003030101e0a2bda40000000049454e44ae426082"
    )
).decode()


@pytest.fixture
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine)
    with Local() as s:
        s.add(Setting(key="currency", value="$"))
        # Ingresos para el cruce de rentabilidad.
        s.add(Order(client="Ana", agreed_price=1000, created_at=datetime(2026, 9, 5)))
        s.add(Order(client="Beto", agreed_price=500, created_at=datetime(2026, 9, 9),
                    manual_status="cancelled"))   # cancelado: no es ingreso
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


def _nuevo(env, **kw):
    body = {"concept": "Bobina PLA", "amount": 300,
            "date": "2026-09-10T12:00:00", "category": "Filamento",
            "invoiced": True, "supplier": "Polymaker"}
    body.update(kw)
    r = env.post("/api/expenses", json=body)
    assert r.status_code == 201
    return r.json()


def test_crud_basico(env):
    x = _nuevo(env)
    assert x["concept"] == "Bobina PLA" and x["currency"] == "$"
    lst = env.get("/api/expenses").json()
    assert len(lst) == 1 and lst[0]["invoiced"] is True
    r = env.put(f"/api/expenses/{x['id']}", json={"concept": "Bobina PLA x2",
                                                  "amount": 600})
    assert r.json()["amount"] == 600
    assert env.delete(f"/api/expenses/{x['id']}").status_code == 204
    assert env.get("/api/expenses").json() == []
    # Monto negativo no se acepta.
    assert env.post("/api/expenses", json={"concept": "x", "amount": -5}).status_code == 422


def test_rentabilidad_mensual(env):
    _nuevo(env)                                              # sep: 300 con factura
    _nuevo(env, concept="Boquillas", amount=100, invoiced=False,
           category="Refacciones", date="2026-09-20T12:00:00")
    _nuevo(env, concept="Renta", amount=50, category="Renta",
           date="2026-08-01T12:00:00", invoiced=False)
    d = env.get("/api/expenses/summary").json()
    sep = next(m for m in d["months"] if m["month"] == "2026-09")
    assert sep["expenses"] == 400.0
    assert sep["invoiced_expenses"] == 300.0
    assert sep["income"] == 1000.0            # el cancelado no cuenta
    assert sep["profit"] == 600.0
    assert sep["by_category"] == {"Filamento": 300.0, "Refacciones": 100.0}
    ago = next(m for m in d["months"] if m["month"] == "2026-08")
    assert ago["expenses"] == 50.0 and ago["income"] == 0.0 and ago["profit"] == -50.0
    assert d["by_category"]["Filamento"] == 300.0


def test_csv_para_contador(env):
    _nuevo(env)
    r = env.get("/api/expenses.csv")
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    head, fila = r.text.splitlines()[:2]
    assert "se_pidio_factura" in head
    assert "2026-09,2026-09-10,Bobina PLA" in fila and ",sí," in fila


def test_foto_del_recibo(env):
    x = _nuevo(env)
    r = env.post("/api/entity-photos", json={
        "entity_type": "expense", "entity_id": x["id"],
        "data_uri": f"data:image/png;base64,{PNG_1PX}"})
    assert r.status_code == 201
    fotos = env.get(f"/api/entity-photos?entity_type=expense&entity_id={x['id']}").json()
    assert len(fotos) == 1
    # La lista de gastos expone la primera foto como photo_url.
    assert env.get("/api/expenses").json()[0]["photo_url"]
    # Borrar el gasto se lleva sus fotos.
    env.delete(f"/api/expenses/{x['id']}")
    assert env.get(f"/api/entity-photos?entity_type=expense&entity_id={x['id']}").json() == []
    # Gasto inexistente: la foto se rechaza.
    assert env.post("/api/entity-photos", json={
        "entity_type": "expense", "entity_id": 999,
        "data_uri": f"data:image/png;base64,{PNG_1PX}"}).status_code == 404