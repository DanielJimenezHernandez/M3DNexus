"""Qué filamento está cargado ahora en cada impresora.

Dos fuentes, en este orden:

1. **Manual**: lo que se haya fijado a mano por hueco (``loaded_materials``).
2. **Deducido**: si un hueco está a null, se rellena con el último filamento
   que imprimió esa máquina.

Para una impresora de un solo color el caso normal es no tocar nada y que se
deduzca sola. Para una multicolor (un CFS), lo habitual es fijar los huecos a
mano, porque de una sola impresión no se sabe qué bobina va en cada ranura.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Material, PrintJob, Printer


def _last_material_id(session: Session, printer_id: int) -> int | None:
    """Material del último job terminado de esa impresora (o None)."""
    return session.scalar(
        select(PrintJob.material_id)
        .where(
            PrintJob.printer_id == printer_id,
            PrintJob.material_id.is_not(None),
        )
        .order_by(PrintJob.end_time.desc().nulls_last())
        .limit(1)
    )


def resolve_slots(session: Session, printer: Printer) -> list[dict]:
    """Huecos resueltos de una impresora, uno por bobina que puede cargar.

    Cada hueco: ``{slot, material_id, material, color_hex, source}``, donde
    ``source`` es ``manual``, ``last-job`` o ``empty``.
    """
    n = max(1, printer.slot_count or 1)
    manual = list(printer.loaded_materials or [])
    # El fallback al último impreso solo tiene sentido para un color: en una
    # multicolor no se puede saber de la última impresión qué había en cada
    # ranura, así que allí solo cuenta lo fijado a mano.
    fallback = _last_material_id(session, printer.id) if n == 1 else None

    # Cache de materiales referenciados, para no consultar en bucle.
    ids = {mid for mid in manual if mid} | ({fallback} if fallback else set())
    mats = {
        m.id: m
        for m in session.scalars(select(Material).where(Material.id.in_(ids))).all()
    } if ids else {}

    slots = []
    for i in range(n):
        mid = manual[i] if i < len(manual) else None
        source = "manual" if mid else None
        if not mid and i == 0 and fallback:
            mid, source = fallback, "last-job"
        m = mats.get(mid) if mid else None
        # Un id manual que ya no existe (material borrado) se trata como vacío.
        if mid and m is None and source == "manual":
            mid = None
        slots.append({
            "slot": i,
            "material_id": m.id if m else None,
            "material": m.name if m else None,
            "color_hex": m.color_hex if m else None,
            "source": source if m else "empty",
        })
    return slots


def set_slots(printer: Printer, material_ids: list[int | None]) -> None:
    """Fija los materiales cargados a mano, ajustando a la longitud de huecos."""
    n = max(1, printer.slot_count or 1)
    limpia = [(mid if mid else None) for mid in (material_ids or [])][:n]
    limpia += [None] * (n - len(limpia))
    # Si no hay nada asignado, se guarda null: así el hueco 0 vuelve a deducirse.
    printer.loaded_materials = limpia if any(limpia) else None
