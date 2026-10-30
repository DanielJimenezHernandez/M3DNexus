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
        # Reparto multimaterial (si lo hay): el precio/kg efectivo es el
        # promedio ponderado por porcentaje de cada tipo. Para la máquina
        # (energía por tipo) se usa el tipo DOMINANTE: el que más aporta.
        raw_mix = [
            e for e in (p.material_mix or [])
            if isinstance(e, dict) and str(e.get("type") or "").strip()
            and (e.get("pct") or 0) > 0
        ]
        if raw_mix:
            total_pct = sum(e["pct"] for e in raw_mix)
            shares = [(str(e["type"]).strip(), e["pct"] / total_pct) for e in raw_mix]
        else:
            shares = [(p.material_type or "", 1.0)]
        ppk = sum(prices.get(t.upper(), 0.0) * s for t, s in shares)
        dom_type = max(shares, key=lambda ts: ts[1])[0]
        key = dom_type.upper()
        if key not in fleet_cache:
            fleet_cache[key] = fleet_cost_per_hour(session, dom_type, price, weighting)
        fleet = fleet_cache[key]
        per_h = fleet["avg_per_h"] if fleet else 0.0

        qty = p.quantity or 1
        # Varias piezas iguales en una cama: el gcode entero se divide entre
        # ellas para ponderar POR PIEZA (coste y peso).
        bed = max(1, p.pieces_per_bed or 1)
        material = (p.weight_g or 0.0) / 1000.0 * ppk / bed
        machine = (p.print_time_s or 0.0) / 3600.0 * per_h / bed
        unit = material + machine
        # Una parte excluida se lista con su coste, pero NO suma a la cuenta
        # (ni al peso estimado que reconcilia contra la báscula).
        included = p.included is not False
        if included:
            mat_total += material * qty
            mach_total += machine * qty
            weight_total += (p.weight_g or 0.0) / bed * qty
        parts_out.append({
            "id": p.id,
            "name": p.name,
            "printer_id": p.printer_id,
            "printer_name": p.printer.name if p.printer else None,
            "gcode_filename": p.gcode_filename,
            "material_type": p.material_type,
            # Mezcla normalizada a 100% (None si es de un solo material) y
            # etiqueta legible ("70% PETG + 30% TPU") para la fila colapsada.
            "material_mix": ([{"type": t, "pct": round(s * 100, 1)}
                              for t, s in shares] if raw_mix else None),
            "material_label": (" + ".join(
                f"{round(s * 100)}% {t}" for t, s in
                sorted(shares, key=lambda ts: -ts[1]))
                if raw_mix else (p.material_type or "")),
            "dominant_type": dom_type,
            "weight_g": round(p.weight_g or 0.0, 1),
            "quantity": qty,
            "print_time_s": p.print_time_s or 0.0,
            "price_per_kg": round(ppk, 2),
            # Falta precio si CUALQUIER tipo de la mezcla no lo tiene.
            "no_price": any(prices.get(t.upper(), 0.0) <= 0 for t, _ in shares),
            "included": included,
            "pieces_per_bed": bed,
            "material": round(material, 2),
            "machine": round(machine, 2),
            "unit_cost": round(unit, 2),
            "line_cost": round(unit * qty, 2),
        })

    # Gastos extra (tornillería, pegamento, piezas compradas…): coste directo.
    # Igual que las partes: los no incluidos se ven pero no suman.
    expenses = list(project.extra_expenses or [])
    extras_cost = sum(
        (e.get("amount") or 0) * (e.get("quantity") or 1)
        for e in expenses if e.get("included", True)
    )

    measured = project.total_weight_g
    have = measured and weight_total > 0

    # Si hay peso de báscula, el MATERIAL se cobra por lo que el producto pesa
    # de verdad, no por la suma estimada: se escala el coste calculado por el
    # factor (báscula ÷ estimado), que reparte la diferencia (soportes, purga,
    # merma) proporcionalmente respetando el precio de cada tipo de material.
    material_estimated = mat_total
    if have:
        mat_total = mat_total * (measured / weight_total)
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
        "extra_expenses": expenses,
        "extras_cost": round(extras_cost, 2),
        # material_cost es el EFECTIVO (por báscula si la hay); el estimado por
        # partes queda como referencia.
        "material_cost": round(mat_total, 2),
        "material_cost_estimated": round(material_estimated, 2),
        "material_by_scale": bool(have),
        "machine_cost": round(mach_total, 2),
        "cost_total": round(mat_total + mach_total + extras_cost, 2),
        "reconciliation": reconciliation,
    }
