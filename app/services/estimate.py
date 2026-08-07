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

# --- Factor térmico por material -------------------------------------------- #
# Cuando NO hay energía medida para un material, su consumo se estima escalando
# la media disponible por un factor que depende de sus temperaturas: mantener la
# cama (y en menor medida la boquilla) calientes es el grueso del gasto y crece
# con el salto sobre la temperatura ambiente. Así PETG/ABS no salen iguales que
# PLA cuando aún no se han medido.
AMBIENT_C = 25.0
# Temperaturas típicas (cama °C, boquilla °C) por tipo de material.
MATERIAL_TEMPS = {
    "PLA": (60, 210),
    "PLA+": (60, 210),
    "PETG": (80, 240),
    "ABS": (100, 250),
    "ASA": (100, 250),
    "TPU": (45, 220),
    "PC": (110, 260),
    "NYLON": (90, 260),
}
# Reparto aproximado del consumo durante la impresión. Cama y boquilla escalan
# con su temperatura; el resto (motores, electrónica, ventiladores) es ~constante
# entre materiales. Suman 1.0.
BED_SHARE = 0.45
NOZZLE_SHARE = 0.10
CONSTANT_SHARE = 0.45


def material_power_factor(material_type: str | None) -> float:
    """Factor de consumo relativo a PLA según las temperaturas del material.

    PLA = 1.0 (base); PETG ≈ 1.27; ABS/ASA ≈ 1.54; TPU < 1 (cama más fría). Se
    usa SOLO como estimación cuando no hay energía medida de ese material.
    """
    if not material_type:
        return 1.0
    temps = MATERIAL_TEMPS.get(material_type.upper())
    if not temps:
        return 1.0
    bed, noz = temps
    bed_pla, noz_pla = MATERIAL_TEMPS["PLA"]
    bed_ratio = (bed - AMBIENT_C) / (bed_pla - AMBIENT_C)
    noz_ratio = (noz - AMBIENT_C) / (noz_pla - AMBIENT_C)
    return round(CONSTANT_SHARE + BED_SHARE * bed_ratio + NOZZLE_SHARE * noz_ratio, 3)


def _avg(session: Session, printer_id=None, material_type=None) -> float | None:
    """Potencia media (W) sobre jobs con energía medida, filtrando opcional."""
    q = select(
        func.sum(PrintJob.energy_kwh), func.sum(PrintJob.print_duration_s)
    ).where(
        PrintJob.energy_kwh.is_not(None),
        # Solo energía MEDIDA de verdad: si se promediara la energía prestada
        # (estimada), el promedio se derivaría en parte de sí mismo.
        PrintJob.energy_estimated == False,  # noqa: E712
        PrintJob.print_duration_s > 0,
    )
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
    """Potencia media con fallback. Devuelve (vatios, fuente).

    Prioridad, pensada para reflejar CADA máquina:
      1. Medido para (esta impresora, este material) → dato real.
      2. Esta impresora tiene base propia medida → se escala por el factor
         térmico del material. Va ANTES que la medida de otra máquina: es más
         fiel imprimir ABS en la Hi estimándolo desde SU consumo que tomar el
         ABS medido en una Voron cerrada.
      3. La impresora no tiene nada medido → media del tipo en otras máquinas.
      4. Sin ningún dato → valor por defecto, también escalado por temperatura.
    """
    factor = material_power_factor(material_type)

    if material_type:
        val = _avg(session, printer_id, material_type)
        if val is not None:
            return val, "impresora+tipo"

    base = _avg(session, printer_id, None)
    if base is not None:
        if factor != 1.0:
            return round(base * factor, 1), "impresora×térmico"
        return base, "impresora"

    if material_type:
        val = _avg(session, None, material_type)
        if val is not None:
            return val, "tipo"

    if factor != 1.0:
        return round(FALLBACK_AVG_POWER_W * factor, 1), "defecto×térmico"
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

    # Si la impresora no mide energía pero referencia a otra, se promedia sobre
    # esa (misma lógica que la ingesta), para que la estimación no salga a 0.
    power_printer_id = printer.id
    if not printer.ha_energy_entity and printer.power_ref_printer_id:
        power_printer_id = printer.power_ref_printer_id
    power_w, power_source = avg_power_w(session, power_printer_id, mtype)
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
            machine_resale_value=printer.resale_value or 0.0,
            machine_lifetime_hours=printer.lifetime_hours,
            maintenance_per_hour=printer.maintenance_per_hour or 0.0,
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
        # Hay dato histórico salvo cuando se parte del valor por defecto (con o
        # sin ajuste térmico encima).
        "avg_power_from_history": not power_source.startswith("defecto"),
        "energy_kwh": energy_kwh,
        "currency": currency,
        "source": source,
        "has_data": bool(meta),
        "cost": breakdown.as_dict(),
    }
