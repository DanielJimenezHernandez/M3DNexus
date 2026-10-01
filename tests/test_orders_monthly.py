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
    assert sep["month"] == "2026-09"
    assert sep["orders"] == 2                    # Caro + Eva (Dino cancelado no)
    assert sep["total"] == 1000.0                # el cancelado no suma
    assert sep["cancelled"] == 1 and sep["sin_precio"] == 1


def test_csv_relacion_para_contador(env):
    r = env.get("/api/orders/monthly.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    body = r.text
    assert "mes,fecha_entrada,pedido,cliente" in body.splitlines()[0]
    assert "2026-08,2026-08-03" in body and "Ana" in body
    assert "cancelled" in body                   # el cancelado va en la relación
    assert "delivered" in body
