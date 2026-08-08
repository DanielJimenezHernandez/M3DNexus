"""Coste real de un producto en desarrollo, a partir de pesos de báscula.

A diferencia de la estimación por gcode (peso del slicer, promedio de flota),
aquí el peso de cada parte se mide con báscula, así que el coste de material es
exacto. Se combina con el coste de máquina por hora (energía + amortización +
mantenimiento) de la flota, según el tiempo de impresión que se indique.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import EntityPhoto, Material, Project
from .estimate import fleet_cost_per_hour
from .ingest import get_settings


def avg_price_per_type(session: Session) -> dict[str, float]:
    """Precio medio /kg por tipo de material (solo los que tienen precio > 0)."""
    rows = session.execute(
        select(Material.material_type, func.avg(Material.price_per_kg))
        .where(Material.price_per_kg > 0)
        .group_by(Material.material_type)
    ).all()
    return {(t or "").upper(): round(p, 2) for t, p in rows if t}


def resolve_project(session: Session, project: Project, weighting: str = "usage") -> dict:
    """Serializa un proyecto con el coste real de cada parte y del total.

    Coste de parte = material (peso × precio medio del tipo) + máquina (horas ×
    coste/hora de flota del tipo). Todo × cantidad. Reconcilia la suma de pesos
    de las partes contra el peso medido del producto completo.
    """
    price, _currency = get_settings(session)
    prices = avg_price_per_type(session)
    fleet_cache: dict[str, dict | None] = {}

    parts_out = []
    mat_total = mach_total = weight_total = 0.0
    for p in project.parts:
        key = (p.material_type or "").upper()
        ppk = prices.get(key, 0.0)
        if key not in fleet_cache:
            fleet_cache[key] = fleet_cost_per_hour(session, p.material_type, price, weighting)
        fleet = fleet_cache[key]
        per_h = fleet["avg_per_h"] if fleet else 0.0

        qty = p.quantity or 1
        material = (p.weight_g or 0.0) / 1000.0 * ppk
        machine = (p.print_time_s or 0.0) / 3600.0 * per_h
        unit = material + machine
        mat_total += material * qty
        mach_total += machine * qty
        weight_total += (p.weight_g or 0.0) * qty
        parts_out.append({
            "id": p.id,
            "name": p.name,
            "printer_id": p.printer_id,
            "printer_name": p.printer.name if p.printer else None,
            "gcode_filename": p.gcode_filename,
            "material_type": p.material_type,
            "weight_g": round(p.weight_g or 0.0, 1),
            "quantity": qty,
            "print_time_s": p.print_time_s or 0.0,
            "price_per_kg": ppk,
            "no_price": ppk <= 0,
            "material": round(material, 2),
            "machine": round(machine, 2),
            "unit_cost": round(unit, 2),
            "line_cost": round(unit * qty, 2),
        })

    measured = project.total_weight_g
    have = measured and weight_total > 0
    reconciliation = {
        "sum_parts_g": round(weight_total, 1),
        "measured_g": measured,
        # Positivo = el producto pesa más que la suma estimada (soportes/merma o
        # partes no listadas); negativo = se estimó de más.
        "diff_g": round((measured or 0.0) - weight_total, 1) if measured else None,
        # Error de la estimación y factor de calibración (báscula / estimado):
        # multiplica futuras estimaciones por 'factor' para acercarlas a la
        # realidad medida.
        "error_pct": round((measured - weight_total) / weight_total * 100, 1) if have else None,
        "factor": round(measured / weight_total, 3) if have else None,
    }
    first_photo = session.scalar(
        select(EntityPhoto.id)
        .where(EntityPhoto.entity_type == "project", EntityPhoto.entity_id == project.id)
        .order_by(EntityPhoto.id)
    )
    return {
        "id": project.id,
        "name": project.name,
        "notes": project.notes,
        "total_weight_g": project.total_weight_g,
        "created_at": project.created_at,
        "photo_url": f"/api/entity-photos/{first_photo}" if first_photo else None,
        "parts": parts_out,
        "material_cost": round(mat_total, 2),
        "machine_cost": round(mach_total, 2),
        "cost_total": round(mat_total + mach_total, 2),
        "reconciliation": reconciliation,
    }
