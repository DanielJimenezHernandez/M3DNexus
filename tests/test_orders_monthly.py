"""Resumen mensual de pedidos para contabilidad (por created_at)."""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  (puebla el metadata)
from app.db import Base
from app.models import Order, Setting


@pytest.fixture
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'o.db'}")
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine)
    with Local() as s:
        s.add(Setting(key="currency", value="$"))
        s.add_all([
            Order(client="Ana", agreed_price=500, created_at=datetime(2026, 8, 3)),
            Order(client="Beto", agreed_price=250, created_at=datetime(2026, 8, 20),
                  manual_status="delivered"),
            Order(client="Caro", agreed_price=1000, created_at=datetime(2026, 9, 1)),
            # Cancelado: cuenta aparte y no suma monto.
            Order(client="Dino", agreed_price=999, created_at=datetime(2026, 9, 2),
                  manual_status="cancelled"),
            # Sin precio acordado: entra al conteo pero se reporta.
            Order(client="Eva", agreed_price=None, created_at=datetime(2026, 9, 15)),
            # Con factura propia: suma a ingresos pero NO a la factura general.
            Order(client="Fito", agreed_price=300, created_at=datetime(2026, 9, 20),
                  invoiced=True),
            # Efectivo excluido del reporte: ingresos sí, factura general no.
            Order(client="Gina", agreed_price=200, created_at=datetime(2026, 9, 25),
                  in_report=False),
        ])
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


def test_resumen_por_mes(env):
    d = env.get("/api/orders/monthly").json()
    assert d["currency"] == "$"
    sep, ago = d["months"][0], d["months"][1]   # descendente
    assert ago["month"] == "2026-08"
    assert ago["orders"] == 2 and ago["total"] == 750.0 and ago["delivered"] == 1
    assert ago["total_invoice"] == 750.0         # nada facturado ni excluido
    assert sep["month"] == "2026-09"
    assert sep["orders"] == 4                    # Caro+Eva+Fito+Gina (Dino no)
    # Ingresos totales: TODO lo entrado = 1000 + 300 + 200.
    assert sep["total"] == 1500.0
    # Factura general: sin factura propia Y marcado incluir → solo Caro.
    assert sep["total_invoice"] == 1000.0
    assert sep["cancelled"] == 1 and sep["sin_precio"] == 1


def test_csv_relacion_para_contador(env):
    r = env.get("/api/orders/monthly.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    body = r.text
    head = body.splitlines()[0]
    assert "mes,fecha_entrada,pedido,cliente" in head
    assert "requirio_factura" in head and "en_factura_general" in head
    assert "2026-08,2026-08-03" in body and "Ana" in body
    assert "cancelled" in body                   # el cancelado va en la relación
    assert "delivered" in body
    # Fito requirió factura (sí → fuera de la general); Gina quedó fuera (no).
    fito = next(l for l in body.splitlines() if "Fito" in l)
    assert ",sí,no," in fito
    gina = next(l for l in body.splitlines() if "Gina" in l)
    assert ",no,no," in gina


def test_fechas_de_cobro(env):
    # Crear con fecha de entrada explícita.
    r = env.post("/api/orders", json={
        "client": "Hugo", "agreed_price": 400, "payment_status": "pending",
        "created_at": "2026-07-15T12:00:00", "items": []})
    assert r.status_code == 201
    o = r.json()
    assert str(o["created_at"]).startswith("2026-07-15")
    assert o["deposit_date"] is None and o["paid_date"] is None
    body = {"client": "Hugo", "agreed_price": 400, "items": [],
            "created_at": "2026-07-15T12:00:00"}
    # Pasa a anticipo sin fecha → se sella sola (hoy).
    o = env.put(f"/api/orders/{o['id']}", json={**body, "payment_status": "deposit",
                                                "deposit_amount": 100}).json()
    assert o["deposit_date"] is not None
    sellada = o["deposit_date"]
    # Pasa a pagado con fecha explícita → la del usuario gana; el anticipo queda.
    o = env.put(f"/api/orders/{o['id']}", json={
        **body, "payment_status": "paid",
        "deposit_date": sellada, "paid_date": "2026-08-02T12:00:00"}).json()
    assert str(o["paid_date"]).startswith("2026-08-02")
    assert o["deposit_date"] == sellada
    # Y el CSV lleva ambas fechas.
    txt = env.get("/api/orders/monthly.csv").text
    assert "fecha_anticipo" in txt.splitlines()[0]
    hugo = next(l for l in txt.splitlines() if "Hugo" in l)
    assert "2026-08-02" in hugo
