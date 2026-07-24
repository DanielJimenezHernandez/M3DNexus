"""Tests de la API de pedidos (crear, resolver estado, cola)."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Order, PrintJob, Printer


@pytest.fixture
def env(tmp_path):
    """Motor propio + cliente FastAPI apuntando a esa BD, sin recargas."""
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine)
    with Local() as s:
        s.add(Printer(id=1, name="Voron 2.4", host="10.0.0.1"))
        s.add(Printer(id=2, name="Ender 3V2", host="10.0.0.2"))
        s.commit()

    # Inyecta este engine en el módulo db para que las rutas lo usen.
    import app.db as dbmod
    orig_engine, orig_local = dbmod._engine, dbmod.SessionLocal
    dbmod._engine = engine
    dbmod.SessionLocal.configure(bind=engine)

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    yield client, Local
    dbmod._engine = orig_engine
    dbmod.SessionLocal.configure(bind=orig_local.kw["bind"])


def _seed_history(Local, printer_id, filename, ok=1, status="completed"):
    with Local() as s:
        for i in range(ok):
            s.add(PrintJob(printer_id=printer_id, moonraker_job_id=f"{filename}-{i}",
                           filename=filename, status=status,
                           start_time=datetime(2026, 1, 1, i, tzinfo=timezone.utc),
                           end_time=datetime(2026, 1, 2, i, tzinfo=timezone.utc)))
        s.commit()


class TestCrearYResolver:
    def test_pedido_nuevo_sin_piezas_es_borrador(self, env):
        c, _ = env
        r = c.post("/api/orders", json={"client": "ACME", "items": []})
        assert r.status_code == 201
        assert r.json()["status"] == "draft"
        assert r.json()["client"] == "ACME"

    def test_pieza_asignada_sin_historial_en_cola(self, env):
        c, _ = env
        r = c.post("/api/orders", json={
            "client": "ACME",
            "items": [{"printer_id": 1, "gcode_filename": "pieza.gcode", "quantity": 2}],
        })
        o = r.json()
        assert o["status"] == "queued"
        assert o["items"][0]["status"] == "queued"
        assert o["items"][0]["quantity"] == 2

    def test_estado_impreso_se_deduce_del_historial(self, env):
        c, Local = env
        _seed_history(Local, 1, "tapa.gcode", ok=3)
        r = c.post("/api/orders", json={
            "client": "Juan",
            "items": [{"printer_id": 1, "gcode_filename": "tapa.gcode", "quantity": 3}],
        })
        o = r.json()
        assert o["items"][0]["status"] == "printed"
        assert o["items"][0]["printed"] == 3
        assert o["status"] == "printed"

    def test_parcial_si_faltan_copias(self, env):
        c, Local = env
        _seed_history(Local, 1, "tapa.gcode", ok=2)
        r = c.post("/api/orders", json={
            "client": "Juan",
            "items": [{"printer_id": 1, "gcode_filename": "tapa.gcode", "quantity": 5}],
        })
        assert r.json()["items"][0]["status"] == "partial"

    def test_override_manual_del_pedido_gana(self, env):
        c, Local = env
        r = c.post("/api/orders", json={
            "client": "Juan", "manual_status": "delivered",
            "items": [{"printer_id": 1, "gcode_filename": "x.gcode"}],
        })
        assert r.json()["status"] == "delivered"

    def test_valida_estados_invalidos(self, env):
        c, _ = env
        assert c.post("/api/orders", json={"client": "X", "payment_status": "regalado"}).status_code == 422
        assert c.post("/api/orders", json={"client": "X", "manual_status": "raro"}).status_code == 422


class TestMargen:
    def test_precio_acordado_da_margen(self, env):
        c, _ = env
        r = c.post("/api/orders", json={
            "client": "ACME", "agreed_price": 350.0,
            "items": [{"printer_id": 1, "gcode_filename": "x.gcode"}],
        })
        m = r.json()["margin"]
        assert m is not None
        assert m["price"] == 350.0

    def test_sin_precio_no_hay_margen(self, env):
        c, _ = env
        r = c.post("/api/orders", json={"client": "ACME", "items": []})
        assert r.json()["margin"] is None


class TestColaYCiclo:
    def test_la_cola_agrupa_por_impresora(self, env):
        c, _ = env
        c.post("/api/orders", json={"client": "A",
            "items": [{"printer_id": 1, "gcode_filename": "a.gcode"}]})
        c.post("/api/orders", json={"client": "B",
            "items": [{"printer_id": 2, "gcode_filename": "b.gcode"}]})
        q = c.get("/api/orders/queue").json()
        printers = {row["printer"] for row in q}
        assert printers == {"Voron 2.4", "Ender 3V2"}

    def test_entregado_sale_de_la_cola(self, env):
        c, _ = env
        c.post("/api/orders", json={"client": "A", "manual_status": "delivered",
            "items": [{"printer_id": 1, "gcode_filename": "a.gcode"}]})
        assert c.get("/api/orders/queue").json() == []

    def test_editar_reemplaza_piezas(self, env):
        c, _ = env
        oid = c.post("/api/orders", json={"client": "A",
            "items": [{"printer_id": 1, "gcode_filename": "a.gcode"}]}).json()["id"]
        r = c.put(f"/api/orders/{oid}", json={"client": "A (editado)",
            "items": [{"printer_id": 2, "gcode_filename": "b.gcode", "quantity": 4}]})
        o = r.json()
        assert o["client"] == "A (editado)"
        assert len(o["items"]) == 1
        assert o["items"][0]["quantity"] == 4

    def test_borrar_pedido(self, env):
        c, Local = env
        oid = c.post("/api/orders", json={"client": "A", "items": []}).json()["id"]
        assert c.delete(f"/api/orders/{oid}").status_code == 204
        with Local() as s:
            assert s.query(Order).count() == 0


class TestCarpetaLocal:
    def test_ruta_compuesta_unix(self, env):
        c, _ = env
        c.put("/api/settings", json={"orders_folder_base": "/home/dani/Pedidos"})
        o = c.post("/api/orders", json={"client": "A", "folder": "112", "items": []}).json()
        assert o["folder"] == "112"
        assert o["folder_path"] == "/home/dani/Pedidos/112"

    def test_ruta_compuesta_windows_respeta_backslash(self, env):
        c, _ = env
        c.put("/api/settings", json={"orders_folder_base": "D:\\3D\\Pedidos"})
        o = c.post("/api/orders", json={"client": "A", "folder": "01", "items": []}).json()
        assert o["folder_path"] == "D:\\3D\\Pedidos\\01"

    def test_sin_base_no_hay_ruta_pero_si_carpeta(self, env):
        c, _ = env
        o = c.post("/api/orders", json={"client": "A", "folder": "07", "items": []}).json()
        assert o["folder"] == "07"
        assert o["folder_path"] == "07"   # sin base, solo el número

    def test_sin_carpeta_no_hay_ruta(self, env):
        c, _ = env
        c.put("/api/settings", json={"orders_folder_base": "/x"})
        o = c.post("/api/orders", json={"client": "A", "items": []}).json()
        assert o["folder"] is None
        assert o["folder_path"] is None

    def test_el_ajuste_persiste(self, env):
        c, _ = env
        c.put("/api/settings", json={"orders_folder_base": "/base/Pedidos"})
        assert c.get("/api/settings").json()["orders_folder_base"] == "/base/Pedidos"
