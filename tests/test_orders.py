"""Tests del motor de estados de pedidos (lógica pura, sin BD)."""

from datetime import datetime, timezone

from app.services.orders import (
    HistoryCount,
    ITEM_DONE,
    ITEM_FAILED,
    ITEM_PARTIAL,
    ITEM_PRINTED,
    ITEM_PRINTING,
    ITEM_QUEUED,
    ITEM_UNASSIGNED,
    ItemStatus,
    LiveMatch,
    ORDER_DRAFT,
    ORDER_PARTIAL,
    ORDER_PRINTED,
    ORDER_PRINTING,
    ORDER_QUEUED,
    due_bucket,
    item_status,
    order_margin,
    order_status,
)

NOW = datetime(2026, 7, 24, tzinfo=timezone.utc)


def _st(**kw):
    base = dict(printer_id=1, gcode_filename="pieza.gcode", quantity=1,
                manual=None, live=None, history=None)
    base.update(kw)
    return item_status(**base)


class TestEstadoDePieza:
    def test_sin_impresora_o_gcode_esta_sin_asignar(self):
        assert _st(printer_id=None).status == ITEM_UNASSIGNED
        assert _st(gcode_filename=None).status == ITEM_UNASSIGNED

    def test_asignada_sin_historial_esta_en_cola(self):
        assert _st().status == ITEM_QUEUED

    def test_en_curso_si_casa_con_lo_vivo(self):
        live = LiveMatch(printer_id=1, filename="pieza.gcode", progress=0.63, eta_s=600)
        s = _st(live=live)
        assert s.status == ITEM_PRINTING
        assert s.progress == 0.63
        assert s.eta_s == 600

    def test_lo_vivo_de_otra_impresora_no_cuenta(self):
        live = LiveMatch(printer_id=2, filename="pieza.gcode", progress=0.5)
        assert _st(live=live).status == ITEM_QUEUED

    def test_impresa_cuando_el_historial_cubre_la_cantidad(self):
        s = _st(quantity=3, history=HistoryCount(ok=3))
        assert s.status == ITEM_PRINTED
        assert s.printed == 3 and s.quantity == 3

    def test_parcial_si_faltan_copias(self):
        s = _st(quantity=5, history=HistoryCount(ok=2))
        assert s.status == ITEM_PARTIAL
        assert s.printed == 2

    def test_una_copia_nueva_en_curso_sobre_lo_ya_impreso(self):
        # Ya salieron 2 de 5 y ahora se imprime otra: sigue 'imprimiendo'.
        live = LiveMatch(printer_id=1, filename="pieza.gcode", progress=0.2)
        s = _st(quantity=5, history=HistoryCount(ok=2), live=live)
        assert s.status == ITEM_PRINTING
        assert s.printed == 2

    def test_fallida_si_solo_hay_fallos(self):
        s = _st(history=HistoryCount(ok=0, failed=2))
        assert s.status == ITEM_FAILED
        assert s.failed_seen == 2

    def test_override_done_manda(self):
        s = _st(quantity=4, manual="done", history=HistoryCount(ok=0))
        assert s.status == ITEM_DONE
        assert s.printed == 4      # se da por completa

    def test_override_failed_manda(self):
        live = LiveMatch(printer_id=1, filename="pieza.gcode", progress=0.9)
        assert _st(manual="failed", live=live).status == ITEM_FAILED


class TestEstadoDePedido:
    def test_sin_piezas_es_borrador(self):
        assert order_status(None, []) == ORDER_DRAFT

    def test_cualquier_pieza_en_curso_manda(self):
        items = [ItemStatus(ITEM_PRINTED), ItemStatus(ITEM_PRINTING),
                 ItemStatus(ITEM_QUEUED)]
        assert order_status(None, items) == ORDER_PRINTING

    def test_una_pieza_sin_asignar_deja_el_pedido_en_borrador(self):
        items = [ItemStatus(ITEM_PRINTED), ItemStatus(ITEM_UNASSIGNED)]
        assert order_status(None, items) == ORDER_DRAFT

    def test_todas_impresas_es_impreso(self):
        items = [ItemStatus(ITEM_PRINTED), ItemStatus(ITEM_DONE)]
        assert order_status(None, items) == ORDER_PRINTED

    def test_algunas_hechas_es_parcial(self):
        items = [ItemStatus(ITEM_PRINTED), ItemStatus(ITEM_QUEUED)]
        assert order_status(None, items) == ORDER_PARTIAL

    def test_todas_en_cola_es_en_cola(self):
        items = [ItemStatus(ITEM_QUEUED), ItemStatus(ITEM_QUEUED)]
        assert order_status(None, items) == ORDER_QUEUED

    def test_override_manual_gana(self):
        items = [ItemStatus(ITEM_PRINTING)]
        assert order_status("delivered", items) == "delivered"
        assert order_status("cancelled", items) == "cancelled"


class TestUrgencia:
    def test_ordena_por_cercania(self):
        from datetime import timedelta
        d = lambda days: NOW + timedelta(days=days)
        assert due_bucket(d(-1), NOW)[1] == "vencido"
        assert due_bucket(d(0), NOW)[1] == "vence hoy"
        assert due_bucket(d(1), NOW)[1] == "mañana"
        assert due_bucket(None, NOW)[0] > due_bucket(d(30), NOW)[0]
        # El rango crece con la lejanía (para ordenar).
        assert due_bucket(d(-1), NOW)[0] < due_bucket(d(0), NOW)[0] < due_bucket(d(2), NOW)[0]


class TestMargen:
    def test_calcula_ganancia_y_porcentaje(self):
        m = order_margin(cost=142.0, price=350.0)
        assert m["profit"] == 208.0
        assert m["margin_pct"] == 59.4

    def test_sin_precio_no_hay_margen(self):
        assert order_margin(cost=100.0, price=None) is None

    def test_coste_cero_no_revienta(self):
        m = order_margin(cost=None, price=50.0)
        assert m["profit"] == 50.0 and m["cost"] == 0.0
