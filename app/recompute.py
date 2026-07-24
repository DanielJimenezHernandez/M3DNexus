"""Recalcula el peso de filamento y el coste de los registros ya guardados.

Necesario cuando cambia la forma de derivar una magnitud: `/api/jobs/{id}/recompute`
solo rehace el coste a partir del peso almacenado, no vuelve a calcular el peso.
Aquí se re-deriva desde `raw_metadata`, que conserva los datos originales del
slicer, aplicando la misma regla que la ingesta (`_filament_weight`).

Uso:
  python -m app.recompute --dry-run     # enseña qué cambiaría, sin tocar nada
  python -m app.recompute               # aplica

Dentro de Docker:
  docker exec printcost python -m app.recompute --dry-run
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from .db import session_scope
from .integrations.moonraker import MoonrakerJob
from .models import Material, PrintJob, Printer
from .services.ingest import _filament_weight, apply_costs, get_settings


def main() -> int:
    ap = argparse.ArgumentParser(description="Recalcula pesos y costes guardados")
    ap.add_argument("--dry-run", action="store_true", help="no escribe nada")
    args = ap.parse_args()

    with session_scope() as session:
        price, currency = get_settings(session)
        printers = {p.id: p for p in session.scalars(select(Printer)).all()}
        jobs = session.scalars(select(PrintJob)).all()

        changed = 0
        weight_before = weight_after = 0.0
        cost_before = cost_after = 0.0

        for rec in jobs:
            printer = printers.get(rec.printer_id)
            if printer is None:  # job huérfano: nada que recalcular
                continue
            material = (
                session.get(Material, rec.material_id) if rec.material_id else None
            )

            # Reconstruye el job tal como lo vio la ingesta, desde los metadatos
            # originales — así la regla del peso vive en un solo sitio.
            mj = MoonrakerJob.from_api({
                "job_id": rec.moonraker_job_id,
                "status": rec.status,
                "filament_used": rec.filament_used_mm,
                "metadata": rec.raw_metadata or {},
            })
            new_weight = _filament_weight(mj, material)

            old_weight = rec.filament_weight_g or 0.0
            old_cost = rec.cost_total or 0.0
            weight_before += old_weight
            cost_before += old_cost

            rec.filament_weight_g = new_weight
            apply_costs(rec, printer, material, price, currency)

            weight_after += new_weight
            cost_after += rec.cost_total or 0.0
            if abs(new_weight - old_weight) > 0.01:
                changed += 1

        print(f"Jobs revisados     : {len(jobs)}")
        print(f"Con peso corregido : {changed}")
        print(f"Filamento : {weight_before / 1000:.2f} kg → {weight_after / 1000:.2f} kg "
              f"({(weight_after - weight_before) / 1000:+.2f} kg)")
        print(f"Coste     : {cost_before:.2f} → {cost_after:.2f} {currency} "
              f"({cost_after - cost_before:+.2f})")

        if args.dry_run:
            session.rollback()
            print("\n(--dry-run: no se ha escrito nada)")
        else:
            print("\nAplicado ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
