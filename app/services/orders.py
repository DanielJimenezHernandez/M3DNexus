"""Estado de los pedidos, deducido de la realidad de las impresoras.

El estado de impresión de cada pieza no se teclea: se cruza su gcode con el
seguimiento en vivo (¿se está imprimiendo ahora?) y con el historial (¿ya salió,
cuántas copias?). Solo lo que la máquina no puede saber —entregado, cancelado—
se guarda como override manual.

Todo aquí es lógica pura sobre datos ya cargados; las consultas viven en las
rutas. Así el cálculo de estados se testea sin base de datos ni Moonraker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Estados derivados de una pieza (orden de avance).
ITEM_UNASSIGNED = "unassigned"   # sin impresora o sin gcode
ITEM_QUEUED = "queued"           # asignada, aún sin imprimir
ITEM_PRINTING = "printing"       # ahora mismo en la máquina
ITEM_PARTIAL = "partial"         # impresa alguna copia, faltan
ITEM_PRINTED = "printed"         # todas las copias hechas
ITEM_FAILED = "failed"           # el último intento falló
ITEM_DONE = "done"               # marcada a mano como terminada

# Estados agregados de un pedido.
ORDER_DRAFT = "draft"
ORDER_QUEUED = "queued"
ORDER_PRINTING = "printing"
ORDER_PARTIAL = "partial"
ORDER_PRINTED = "printed"
ORDER_DELIVERED = "delivered"
ORDER_CANCELLED = "cancelled"
ORDER_ON_HOLD = "on_hold"

_FINISHED_OK = {"completed"}
_FINISHED_FAIL = {"cancelled", "error", "klippy_shutdown", "interrupted"}


@dataclass
class LiveMatch:
    """Lo que hace falta del seguimiento en vivo para casar una pieza."""

    printer_id: int
    filename: str
    progress: float = 0.0
    eta_s: float | None = None


@dataclass
class HistoryCount:
    """Recuento de impresiones de un gcode en una impresora tras cierta fecha."""

    ok: int = 0
    failed: int = 0
    last_end: datetime | None = None


@dataclass
class ItemStatus:
    status: str
    printed: int = 0
    quantity: int = 1
    progress: float | None = None      # 0..1 si está imprimiendo
    eta_s: float | None = None
    failed_seen: int = 0

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "printed": self.printed,
            "quantity": self.quantity,
            "progress": self.progress,
            "eta_s": self.eta_s,
            "failed_seen": self.failed_seen,
        }


def item_status(
    *,
    printer_id: int | None,
    gcode_filename: str | None,
    quantity: int,
    manual: str | None,
    live: LiveMatch | None,
    history: HistoryCount | None,
) -> ItemStatus:
    """Estado de una pieza a partir de su asignación, lo vivo y el historial."""
    qty = max(1, quantity or 1)
    hist = history or HistoryCount()

    # Override manual: manda salvo el matiz de contar copias ya hechas.
    if manual == "done":
        return ItemStatus(ITEM_DONE, printed=qty, quantity=qty)
    if manual == "failed":
        return ItemStatus(ITEM_FAILED, printed=hist.ok, quantity=qty,
                          failed_seen=hist.failed)

    if not printer_id or not gcode_filename:
        return ItemStatus(ITEM_UNASSIGNED, printed=0, quantity=qty)

    printing = (
        live is not None
        and live.printer_id == printer_id
        and live.filename == gcode_filename
    )

    if hist.ok >= qty:
        # Todas las copias hechas (y no hay una nueva en curso encima).
        if printing:
            return ItemStatus(ITEM_PRINTING, printed=hist.ok, quantity=qty,
                              progress=live.progress, eta_s=live.eta_s,
                              failed_seen=hist.failed)
        return ItemStatus(ITEM_PRINTED, printed=hist.ok, quantity=qty,
                          failed_seen=hist.failed)

    if printing:
        return ItemStatus(ITEM_PRINTING, printed=hist.ok, quantity=qty,
                          progress=live.progress, eta_s=live.eta_s,
                          failed_seen=hist.failed)
    if hist.ok > 0:
        return ItemStatus(ITEM_PARTIAL, printed=hist.ok, quantity=qty,
                          failed_seen=hist.failed)
    if hist.failed > 0:
        return ItemStatus(ITEM_FAILED, printed=0, quantity=qty,
                          failed_seen=hist.failed)
    return ItemStatus(ITEM_QUEUED, printed=0, quantity=qty)


# Prioridad para agregar: cuanto más avanzado o activo, mayor número.
_ITEM_RANK = {
    ITEM_UNASSIGNED: 0, ITEM_QUEUED: 1, ITEM_FAILED: 2,
    ITEM_PARTIAL: 3, ITEM_PRINTING: 4, ITEM_PRINTED: 5, ITEM_DONE: 5,
}


def order_status(manual: str | None, items: list[ItemStatus]) -> str:
    """Estado agregado del pedido a partir del de sus piezas."""
    from ..models import ORDER_MANUAL

    if manual in ORDER_MANUAL:
        return manual
    if not items:
        return ORDER_DRAFT
    st = {i.status for i in items}
    if ITEM_PRINTING in st:
        return ORDER_PRINTING
    if any(i.status == ITEM_UNASSIGNED for i in items):
        # Aún se está armando el pedido: falta asignar alguna pieza.
        return ORDER_DRAFT
    done = {ITEM_PRINTED, ITEM_DONE}
    if all(i.status in done for i in items):
        return ORDER_PRINTED
    if any(i.status in done or i.status == ITEM_PARTIAL for i in items):
        return ORDER_PARTIAL
    return ORDER_QUEUED


@dataclass
class OrderPriority:
    due_date: datetime | None
    status: str

    @property
    def is_open(self) -> bool:
        """Sigue pendiente de trabajo (no entregado ni cancelado)."""
        return self.status not in (ORDER_DELIVERED, ORDER_CANCELLED)


def due_bucket(due: datetime | None, now: datetime) -> tuple[int, str]:
    """Clasifica por urgencia: (rango para ordenar, etiqueta)."""
    if due is None:
        return (5, "sin fecha")
    days = (due.date() - now.date()).days
    if days < 0:
        return (0, "vencido")
    if days == 0:
        return (1, "vence hoy")
    if days == 1:
        return (2, "mañana")
    if days <= 3:
        return (3, f"en {days} días")
    return (4, f"en {days} días")


def order_margin(cost: float | None, price: float | None) -> dict | None:
    """Coste, precio y margen del pedido. None si falta el precio."""
    if price is None:
        return None
    c = cost or 0.0
    profit = price - c
    pct = (profit / price * 100.0) if price else 0.0
    return {"cost": round(c, 2), "price": round(price, 2),
            "profit": round(profit, 2), "margin_pct": round(pct, 1)}
