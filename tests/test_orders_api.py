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

    def test_el_historial_del_gcode_NO_marca_impreso(self, env):
        # Aunque el mismo gcode ya tenga impresiones (de otro pedido/pieza), la
        # pieza sigue "en cola" hasta que se marquen copias a mano. El gcode solo
        # sirve para estimar coste, no para decidir si esta pieza se imprimió.
        c, Local = env
        _seed_history(Local, 1, "tapa.gcode", ok=3)
        r = c.post("/api/orders", json={
            "client": "Juan",
            "items": [{"printer_id": 1, "gcode_filename": "tapa.gcode", "quantity": 3}],
        })
        o = r.json()
        assert o["items"][0]["status"] == "queued"
        assert o["items"][0]["printed"] == 0
        assert o["status"] == "queued"

    def test_impreso_solo_cuando_se_marca_a_mano(self, env):
        c, Local = env
        _seed_history(Local, 1, "tapa.gcode", ok=3)   # historial irrelevante
        r = c.post("/api/orders", json={
            "client": "Juan",
            "items": [{"printer_id": 1, "gcode_filename": "tapa.gcode", "quantity": 3,
                       "copy_status": ["done", "done", "done"]}],
        })
        assert r.json()["items"][0]["status"] == "printed"
        assert r.json()["items"][0]["printed"] == 3

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


class TestGastosExtraYAnticipo:
    def test_gastos_del_pedido_restan_del_margen(self, env):
        c, _ = env
        r = c.post("/api/orders", json={
            "client": "ACME", "agreed_price": 100.0,
            "extra_expenses": [{"label": "empaque", "amount": 15}, {"label": "envío", "amount": 5}],
            "items": [],
        })
        o = r.json()
        assert o["margin"]["cost"] == 20.0          # 15 + 5
        assert o["margin"]["profit"] == 80.0         # 100 - 20
        assert len(o["extra_expenses"]) == 2

    def test_gastos_por_pieza_tambien_restan(self, env):
        c, _ = env
        r = c.post("/api/orders", json={
            "client": "ACME", "agreed_price": 50.0,
            "items": [{"printer_id": 1, "gcode_filename": "x.gcode",
                       "extra_expenses": [{"label": "velita", "amount": 8}]}],
        })
        o = r.json()
        assert o["margin"]["cost"] == 8.0
        assert o["items"][0]["extra_expenses"][0]["label"] == "velita"

    def test_importe_negativo_se_acota_a_cero(self, env):
        c, _ = env
        o = c.post("/api/orders", json={"client": "A", "agreed_price": 10,
            "extra_expenses": [{"label": "x", "amount": -5}]}).json()
        assert o["margin"]["cost"] == 0.0

    def test_anticipo_guarda_monto_recibido(self, env):
        c, _ = env
        o = c.post("/api/orders", json={"client": "A", "payment_status": "deposit",
            "deposit_amount": 500, "agreed_price": 2000}).json()
        assert o["payment_status"] == "deposit"
        assert o["deposit_amount"] == 500

    def test_monto_recibido_se_limpia_si_no_es_anticipo(self, env):
        # Si el pago no es anticipo, el monto recibido no se conserva.
        c, _ = env
        o = c.post("/api/orders", json={"client": "A", "payment_status": "paid",
            "deposit_amount": 500}).json()
        assert o["deposit_amount"] is None

    def test_editar_conserva_gastos_reemplazando(self, env):
        c, _ = env
        oid = c.post("/api/orders", json={"client": "A",
            "extra_expenses": [{"label": "a", "amount": 1}]}).json()["id"]
        o = c.put(f"/api/orders/{oid}", json={"client": "A",
            "extra_expenses": [{"label": "b", "amount": 2}, {"label": "c", "amount": 3}]}).json()
        assert len(o["extra_expenses"]) == 2
        assert o["margin"] is None   # sin precio, no hay margen aunque haya gastos


class TestEstadoPorCopia:
    def test_marcar_copias_hechas_se_refleja(self, env):
        c, _ = env
        o = c.post("/api/orders", json={"client": "A", "items": [
            {"printer_id": 1, "gcode_filename": "p.gcode", "quantity": 4,
             "copy_status": ["done", "done", "pending", "pending"]}]}).json()
        it = o["items"][0]
        assert it["printed"] == 2
        assert it["status"] == "partial"
        assert it["copy_status"] == ["done", "done", "pending", "pending"]

    def test_se_rellena_a_la_cantidad(self, env):
        # copy_status más corto que quantity: se completa con "pending".
        c, _ = env
        o = c.post("/api/orders", json={"client": "A", "items": [
            {"printer_id": 1, "gcode_filename": "p.gcode", "quantity": 3,
             "copy_status": ["done"]}]}).json()
        assert o["items"][0]["copy_status"] == ["done", "pending", "pending"]
        assert o["items"][0]["printed"] == 1

    def test_todo_pendiente_no_se_guarda(self, env):
        # Si nada está marcado, copy_status queda vacío (vuelve al automático).
        c, _ = env
        o = c.post("/api/orders", json={"client": "A", "items": [
            {"printer_id": 1, "gcode_filename": "p.gcode", "quantity": 2,
             "copy_status": ["pending", "pending"]}]}).json()
        assert o["items"][0]["copy_status"] == []

    def test_estado_invalido_se_rechaza(self, env):
        c, _ = env
        r = c.post("/api/orders", json={"client": "A", "items": [
            {"printer_id": 1, "gcode_filename": "p.gcode", "copy_status": ["hecho"]}]})
        assert r.status_code == 422


class TestGastosConCantidad:
    def test_precio_por_cantidad(self, env):
        # 4 velas a 12 + 4 bolsas a 3 = 48 + 12 = 60 de coste.
        c, _ = env
        o = c.post("/api/orders", json={"client": "Monica", "agreed_price": 2200,
            "extra_expenses": [
                {"label": "velas", "amount": 12, "quantity": 4},
                {"label": "bolsas celofán", "amount": 3, "quantity": 4}],
            "items": []}).json()
        assert o["margin"]["cost"] == 60.0
        assert o["margin"]["profit"] == 2140.0
        assert o["extra_expenses"][0]["quantity"] == 4

    def test_cantidad_por_defecto_es_uno(self, env):
        c, _ = env
        o = c.post("/api/orders", json={"client": "A", "agreed_price": 100,
            "extra_expenses": [{"label": "x", "amount": 25}]}).json()
        assert o["margin"]["cost"] == 25.0
        assert o["extra_expenses"][0]["quantity"] == 1

    def test_cantidad_minima_uno(self, env):
        c, _ = env
        o = c.post("/api/orders", json={"client": "A", "agreed_price": 100,
            "extra_expenses": [{"label": "x", "amount": 10, "quantity": 0}]}).json()
        assert o["extra_expenses"][0]["quantity"] == 1
        assert o["margin"]["cost"] == 10.0


class TestPostProcesado:
    def test_coste_por_minuto_a_nivel_de_pedido(self, env):
        # Tarifa 120/h, 45 min totales del pedido → 0.75h × 120 = 90.
        c, _ = env
        o = c.post("/api/orders", json={"client": "A", "agreed_price": 500,
            "postproc_rate": 120, "postproc_minutes": 45,
            "items": [{"printer_id": 1, "gcode_filename": "x.gcode", "quantity": 4}]}).json()
        assert o["postproc_cost"] == 90.0
        assert o["postproc_minutes"] == 45
        assert o["postproc_rate"] == 120
        assert o["margin"]["cost"] == 90.0

    def test_sin_tarifa_no_hay_coste(self, env):
        c, _ = env
        o = c.post("/api/orders", json={"client": "A", "postproc_minutes": 60,
            "items": []}).json()
        assert o["postproc_cost"] == 0.0

    def test_sin_minutos_no_hay_coste(self, env):
        c, _ = env
        o = c.post("/api/orders", json={"client": "A", "postproc_rate": 100,
            "items": []}).json()
        assert o["postproc_cost"] == 0.0
        assert o["postproc_minutes"] == 0

    def test_minutos_negativos_a_cero(self, env):
        c, _ = env
        o = c.post("/api/orders", json={"client": "A", "postproc_rate": 60,
            "postproc_minutes": -10, "items": []}).json()
        assert o["postproc_minutes"] == 0

    def test_postproc_y_gastos_se_suman_al_coste(self, env):
        # 30 min a 60/h = 30 de postproc + 20 de gastos = 50 de coste.
        c, _ = env
        o = c.post("/api/orders", json={"client": "A", "agreed_price": 200,
            "postproc_rate": 60, "postproc_minutes": 30,
            "extra_expenses": [{"label": "empaque", "amount": 20}],
            "items": []}).json()
        assert o["margin"]["cost"] == 50.0
        assert o["margin"]["profit"] == 150.0

    def test_la_pieza_ya_no_lleva_postproc(self, env):
        c, _ = env
        o = c.post("/api/orders", json={"client": "A",
            "items": [{"printer_id": 1, "gcode_filename": "x.gcode"}]}).json()
        assert "postproc_minutes" not in o["items"][0]


def test_add_item_endpoint(env):
    c, _ = env
    oid = c.post("/api/orders", json={"client": "ACME", "items": []}).json()["id"]
    r = c.post(f"/api/orders/{oid}/items",
               json={"printer_id": 1, "gcode_filename": "nueva.gcode", "quantity": 2})
    assert r.status_code == 201
    orders = c.get("/api/orders").json()
    orders = orders["orders"] if isinstance(orders, dict) else orders
    o = next(x for x in orders if x["id"] == oid)
    assert len(o["items"]) == 1
    assert o["items"][0]["gcode_filename"] == "nueva.gcode"
    assert o["items"][0]["quantity"] == 2


def test_add_item_order_no_existe(env):
    c, _ = env
    r = c.post("/api/orders/99999/items",
               json={"printer_id": 1, "gcode_filename": "x.gcode"})
    assert r.status_code == 404


def test_delete_order_item(env):
    c, _ = env
    o = c.post("/api/orders", json={"client": "ACME", "items": [
        {"printer_id": 1, "gcode_filename": "a.gcode"},
        {"printer_id": 1, "gcode_filename": "b.gcode"},
    ]}).json()
    item_id = o["items"][0]["id"]
    assert c.delete(f"/api/order-items/{item_id}").status_code == 204
    orders = c.get("/api/orders").json()
    orders = orders["orders"] if isinstance(orders, dict) else orders
    quedan = next(x for x in orders if x["id"] == o["id"])["items"]
    assert len(quedan) == 1 and quedan[0]["gcode_filename"] == "b.gcode"
    assert c.delete("/api/order-items/99999").status_code == 404
