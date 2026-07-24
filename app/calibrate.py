"""Deduce la matriz de calibración a partir del historial de impresiones.

Cada job guarda el nombre del perfil de filamento que usó, y en OrcaSlicer ese
nombre lleva el sufijo de la impresora para la que se calibró. Con eso se puede
reconstruir qué está calibrado dónde sin teclear nada.

La lógica vive en ``services.calibration``; aquí solo se presenta. El mismo
análisis está detrás del botón «Deducir del historial» de la web.

Uso:
  python -m app.calibrate --dry-run     # propone, sin escribir
  python -m app.calibrate               # aplica

Dentro de Docker:
  docker exec printcost python -m app.calibrate --dry-run
"""

from __future__ import annotations

import argparse
import sys

from .db import session_scope
from .services.calibration import rescan_from_history


def main() -> int:
    ap = argparse.ArgumentParser(description="Matriz de calibración desde el historial")
    ap.add_argument("--dry-run", action="store_true", help="no escribe nada")
    args = ap.parse_args()

    with session_scope() as session:
        r = rescan_from_history(session, apply=not args.dry_run)

        print("=== Alias de OrcaSlicer por impresora ===")
        for nombre, alias in r["aliases"].items():
            print(f"\n{nombre}")
            for a in alias:
                print(f"    alias   @{a}")
            for a, n in sorted(r["foreign"][nombre].items(), key=lambda x: -x[1]):
                print(f"    AJENO   @{a}  ({n} jobs con perfil de otra máquina)")
            for a, n in sorted(r["unclear"][nombre].items(), key=lambda x: -x[1]):
                print(f"    revisar @{a}  ({n} jobs, sin evidencia para decidir)")

        print(f"\n=== Matriz: {r['combinaciones']} combinaciones "
              "(material × impresora × boquilla) ===")
        print(f"  nuevas {r['nuevas']} · actualizadas {r['actualizadas']}")
        print(f"  impresiones con perfil de otra máquina: {r['jobs_con_perfil_ajeno']}")

        if args.dry_run:
            session.rollback()
            print("\n(--dry-run: no se ha escrito nada)")
        else:
            print("\nAplicado ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
