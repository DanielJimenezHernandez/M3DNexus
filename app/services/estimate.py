"""Estimación de coste ANTES de imprimir, a partir de los metadatos del gcode.

El peso de filamento y el tiempo estimado salen del laminador. La energía no se
ha medido aún, así que se estima con una **potencia media** que se deriva del
historial real. Como distintos materiales consumen distinto (ABS/ASA calientan
más que PLA), el promedio se calcula por (impresora × tipo de material) con una
cadena de respaldo: impresora+tipo → tipo → impresora → valor por defecto.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..cost import DEFAULT_FILAMENT_DENSITY, CostInputs, compute_cost, mm_to_grams
from ..integrations.moonraker import MoonrakerClient, thumbnail_path
from ..models import Material, PrintJob, Printer
from .filament import resolve_or_create_material
from .ingest import get_settings

# Potencia media por defecto (W) cuando no hay historial del que derivarla.
FALLBACK_AVG_POWER_W = 100.0


def _avg(session: Session, printer_id=None, material_type=None) -> float | None:
    """Potencia media (W) sobre jobs con energía medida, filtrando opcional."""
    q = select(
        func.sum(PrintJob.energy_kwh), func.sum(PrintJob.print_duration_s)
    ).where(PrintJob.energy_kwh.is_not(None), PrintJob.print_duration_s > 0)
    if printer_id is not None:
        q = q.where(PrintJob.printer_id == printer_id)
    if material_type is not None:
        q = q.join(Material, PrintJob.material_id == Material.id).where(
            func.lower(Material.material_type) == material_type.lower()
        )
    energy, dur_s = session.execute(q).one()
    if energy and dur_s and dur_s > 0:
        return round(energy / (dur_s / 3600.0) * 1000.0, 1)
    return None


def avg_power_w(
    session: Session, printer_id: int, material_type: str | None = None
) -> tuple[float, str]:
    """Potencia media con fallback. Devuelve (vatios, fuente)."""
    attempts: list[tuple[int | None, str | None, str]] = []
    if material_type:
        attempts.append((printer_id, material_type, "impresora+tipo"))
        attempts.append((None, material_type, "tipo"))
    attempts.append((printer_id, None, "impresora"))
    for pf, tf, label in attempts:
        val = _avg(session, pf, tf)
        if val is not None:
            return val, label
    return FALLBACK_AVG_POWER_W, "defecto"


def estimate_cost(session: Session, printer: Printer, filename: str) -> dict:
    """Estima leyendo los metadatos EN VIVO de Moonraker (impresora encendida)."""
    meta = MoonrakerClient(printer.moonraker_url).file_metadata(filename) or {}
    return _estimate_from_meta(session, printer, filename, meta, source="live")


def estimate_from_history(session: Session, printer: Printer, filename: str) -> dict:
    """Estima usando los metadatos GUARDADOS en la BD (funciona sin conexión)."""
    job = session.scalar(
        select(PrintJob)
        .where(
            PrintJob.printer_id == printer.id,
            PrintJob.filename == filename,
            PrintJob.raw_metadata.is_not(None),
        )
        .order_by(PrintJob.end_time.desc().nulls_last())
    )
    meta = (job.raw_metadata if job else None) or {}
    return _estimate_from_meta(session, printer, filename, meta, source="history")


def _estimate_from_meta(
    session: Session, printer: Printer, filename: str, meta: dict, source: str
) -> dict:
    """Núcleo del cálculo de estimación a partir de un dict de metadatos."""
    # Resuelve el filamento por nombre (sin crear nada en una estimación).
    material = resolve_or_create_material(
        session, meta, meta.get("filament_type"), allow_create=False
    )
    mtype = material.material_type if material else meta.get("filament_type")
    density = material.density_g_cm3 if material else DEFAULT_FILAMENT_DENSITY

    weight_g = meta.get("filament_weight_total")
    if not weight_g:
        weight_g = mm_to_grams(meta.get("filament_total") or 0.0, density_g_cm3=density)
    weight_g = round(float(weight_g), 2)

    est_s = float(meta.get("estimated_time") or 0.0)
    hours = est_s / 3600.0

    power_w, power_source = avg_power_w(session, printer.id, mtype)
    energy_kwh = round(power_w / 1000.0 * hours, 4)

    price, currency = get_settings(session)
    breakdown = compute_cost(
        CostInputs(
            energy_kwh=energy_kwh,
            electricity_price_per_kwh=price,
            filament_weight_g=weight_g,
            filament_price_per_kg=material.price_per_kg if material else 0.0,
            print_duration_hours=hours,
            machine_purchase_price=printer.purchase_price or 0.0,
            machine_lifetime_hours=printer.lifetime_hours,
        )
    )

    return {
        "printer_id": printer.id,
        "printer_name": printer.name,
        "filename": filename,
        "estimated_time_s": est_s,
        "filament_g": weight_g,
        # Para rellenar una cotización: altura de capa y miniatura del gcode.
        "layer_height": meta.get("layer_height"),
        "thumbnail": thumbnail_path(filename, meta),
        "material": material.name if material else None,
        "filament_type": mtype,
        "avg_power_w": power_w,
        "avg_power_source": power_source,
        "avg_power_from_history": power_source != "defecto",
        "energy_kwh": energy_kwh,
        "currency": currency,
        "source": source,
        "has_data": bool(meta),
        "cost": breakdown.as_dict(),
    }
