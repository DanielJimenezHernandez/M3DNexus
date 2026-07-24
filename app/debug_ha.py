"""Diagnóstico de la conexión entre la app y Home Assistant.

Comprueba, en orden, todo el camino de la energía:
  1. Que hay token y la URL responde (ping con auth).
  2. Que cada entidad de energía configurada existe y su valor actual.
  3. Que el historial devuelve datos y se puede calcular el delta (kWh).

Uso:
  python -m app.debug_ha                  # todas las impresoras, últimas 48 h
  python -m app.debug_ha --hours 6
  python -m app.debug_ha --entity sensor.energy_creality_hi_total

Dentro de Docker:
  docker exec printcost python -m app.debug_ha
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from .config import get_config
from .db import session_scope
from .integrations.homeassistant import HomeAssistantClient
from .models import Printer


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnóstico de conexión con HA")
    ap.add_argument("--hours", type=float, default=48, help="ventana a consultar")
    ap.add_argument("--entity", help="probar una entidad concreta (omite la BD)")
    args = ap.parse_args()

    cfg = get_config()
    print(f"HA URL : {cfg.ha_url}")
    print(f"Token  : {'(definido)' if cfg.ha_token else 'VACÍO ❌'}")
    if not cfg.ha_token:
        print("→ No hay token. Define HA_TOKEN en el entorno o en config.yaml.")
        return 1

    ha = HomeAssistantClient(cfg.ha_url, cfg.ha_token)
    if not ha.ping():
        print("Ping   : FALLO ❌")
        print("  Causas típicas: URL incorrecta, nombre .local no resoluble desde")
        print("  Docker (usa la IP), red/cortafuegos, o token inválido.")
        return 1
    print("Ping   : OK ✅")

    # Entidades a revisar.
    if args.entity:
        entities = [("(--entity)", args.entity)]
    else:
        with session_scope() as session:
            entities = [
                (p.name, p.ha_energy_entity)
                for p in session.query(Printer).all()
                if p.ha_energy_entity
            ]
    if not entities:
        print("\nNo hay entidades de energía configuradas en ninguna impresora.")
        return 0

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=args.hours)
    problems = 0
    for name, entity in entities:
        print(f"\n[{name}] {entity}")
        st = ha.state(entity)
        if st is None:
            print("  estado actual : NO EXISTE ❌ (revisa el nombre de la entidad)")
            problems += 1
            continue
        print(f"  estado actual : {st['state']} {st['unit'] or ''} "
              f"(state_class={st['state_class']}, device_class={st['device_class']})")
        if st["state_class"] not in ("total", "total_increasing"):
            print("  ⚠ state_class no es total/total_increasing: puede no ser un "
                  "contador acumulado de energía.")
        kwh = ha.energy_between(entity, start, end)
        if kwh is None:
            print(f"  energía {args.hours:.0f}h : sin datos suficientes ⚠ "
                  "(¿fuera de la retención del recorder? ¿impresora apagada?)")
        else:
            print(f"  energía {args.hours:.0f}h : {kwh} kWh ✅")

    print()
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
