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


# --- Estimación de proyecto multi-gcode / multi-máquina --------------------- #
# Para prototipar u ofrecer sin fijar impresora: se promedia el coste/hora de la
# FLOTA (luz + máquina) y se aplica al tiempo del gcode. La luz depende del
# material (factor térmico); la máquina/hora (amortización + mantenimiento) no.


def _machine_hours(session: Session) -> dict[int, float]:
    """Horas reales de impresión por impresora (peso del promedio ponderado)."""
    rows = session.execute(
        select(
            PrintJob.printer_id,
            func.coalesce(func.sum(PrintJob.print_duration_s), 0.0),
        ).group_by(PrintJob.printer_id)
    ).all()
    return {pid: (secs or 0.0) / 3600.0 for pid, secs in rows}


def fleet_cost_per_hour(
    session: Session,
    material_type: str | None,
    price_per_kwh: float,
    weighting: str = "usage",
) -> dict | None:
    """Coste $/h promedio de la flota para un material, con rango min–max.

    Por máquina: luz/h = potencia(material) × precio + máquina/h. El promedio
    pondera por horas reales de impresión (``usage``) o es simple (``simple``).
    Devuelve None si no hay impresoras habilitadas.
    """
    printers = session.scalars(
        select(Printer).where(Printer.enabled == True)  # noqa: E712
    ).all()
    if not printers:
        return None

    hours = _machine_hours(session) if weighting == "usage" else {}
    machines = []
    for p in printers:
        power, src = avg_power_w(session, p.id, material_type)
        elec = power / 1000.0 * price_per_kwh
        mach = p.machine_per_hour
        machines.append({
            "printer": p.name,
            "power_w": power,
            "power_source": src,
            "elec_per_h": round(elec, 4),
            "machine_per_h": round(mach, 4),
            "total_per_h": round(elec + mach, 4),
            "weight_h": round(hours.get(p.id, 0.0), 1) if weighting == "usage" else 1.0,
        })

    total_w = sum(m["weight_h"] for m in machines)
    # Se pondera solo si se pidió "usage" Y hay horas registradas; si no, media
    # plana (que la rama ponderada con pesos 1.0 también produce).
    weighted = weighting == "usage" and total_w > 0
    if total_w <= 0:  # sin uso registrado: media plana
        n = len(machines)
        avg = sum(m["total_per_h"] for m in machines) / n
        avg_elec = sum(m["elec_per_h"] for m in machines) / n
        avg_mach = sum(m["machine_per_h"] for m in machines) / n
    else:
        avg = sum(m["total_per_h"] * m["weight_h"] for m in machines) / total_w
        avg_elec = sum(m["elec_per_h"] * m["weight_h"] for m in machines) / total_w
        avg_mach = sum(m["machine_per_h"] * m["weight_h"] for m in machines) / total_w

    lo = min(machines, key=lambda m: m["total_per_h"])
    hi = max(machines, key=lambda m: m["total_per_h"])
    return {
        "avg_per_h": round(avg, 4),
        "avg_elec_per_h": round(avg_elec, 4),
        "avg_machine_per_h": round(avg_mach, 4),
        "min_per_h": lo["total_per_h"],
        "min_machine": lo["printer"],
        "max_per_h": hi["total_per_h"],
        "max_machine": hi["printer"],
        "weighted": weighted,
        "n_machines": len(machines),
        "machines": machines,
    }


def estimate_project(
    session: Session, files: list[dict], weighting: str = "usage"
) -> dict:
    """Estima el coste de un proyecto (varios gcodes, posibles materiales varios).

    Cada archivo: {filename, weight_g, time_s, quantity, material_id|material_type}.
    El coste de cada uno = filamento (peso × precio/kg) + coste/hora de flota ×
    horas. Devuelve por archivo y totales, con rango min–max de la flota.
    """
    price, currency = get_settings(session)
    fleet_cache: dict[str, dict | None] = {}
    out_files = []
    cost_total = cost_low = cost_high = 0.0

    for f in files:
        weight_g = float(f.get("weight_g") or 0.0)
        time_h = float(f.get("time_s") or 0.0) / 3600.0
        qty = max(1, int(f.get("quantity") or 1))
        material = (
            session.get(Material, f["material_id"]) if f.get("material_id") else None
        )
        mtype = material.material_type if material else (f.get("material_type") or None)
        ppk = material.price_per_kg if material else 0.0

        key = (mtype or "").upper()
        if key not in fleet_cache:
            fleet_cache[key] = fleet_cost_per_hour(session, mtype, price, weighting)
        fleet = fleet_cache[key]

        filament = weight_g / 1000.0 * ppk
        if fleet:
            unit = filament + fleet["avg_per_h"] * time_h
            low = filament + fleet["min_per_h"] * time_h
            high = filament + fleet["max_per_h"] * time_h
        else:
            unit = low = high = filament

        cost_total += unit * qty
        cost_low += low * qty
        cost_high += high * qty
        out_files.append({
            "filename": f.get("filename"),
            "weight_g": round(weight_g, 1),
            "time_s": float(f.get("time_s") or 0.0),
            "quantity": qty,
            "material": material.name if material else mtype,
            "material_type": mtype,
            "filament": round(filament, 2),
            "print_per_unit": round(unit - filament, 2),  # luz + máquina por ud.
            "unit_cost": round(unit, 2),
            "line_cost": round(unit * qty, 2),
            "unit_low": round(low, 2),
            "unit_high": round(high, 2),
            "no_price": material is not None and ppk <= 0,
        })

    return {
        "currency": currency,
        "weighting": weighting,
        "files": out_files,
        "cost_total": round(cost_total, 2),
        "cost_low": round(cost_low, 2),
        "cost_high": round(cost_high, 2),
        "fleet": fleet_cache,
    }
