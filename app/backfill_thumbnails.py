"""Descarga y guarda las miniaturas de los gcodes ya registrados (una vez).

Las impresiones se guardaban con solo la REFERENCIA a la miniatura; este script
baja la imagen de cada impresora (si está encendida y el gcode sigue ahí) y la
guarda en la BD para verla offline en el listado. Los gcodes ya borrados de la
impresora no tendrán miniatura; se marcan como intentados para no reintentar.

Uso:
  python -m app.backfill_thumbnails
  docker exec printcost python -m app.backfill_thumbnails
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from .db import session_scope
from .integrations.moonraker import MoonrakerClient
from .models import PrintJob, Printer
from .services.sync import store_thumbnail


def main() -> int:
    with session_scope() as session:
        printers = {p.id: p for p in session.scalars(select(Printer)).all()}
        jobs = session.scalars(
            select(PrintJob).where(
                PrintJob.has_thumbnail == False,     # noqa: E712
                PrintJob.thumbnail_tried == False,   # noqa: E712
                PrintJob.raw_metadata.is_not(None),
            )
        ).all()

        clients: dict[int, MoonrakerClient] = {}
        reachable: dict[int, bool] = {}
        got = tried = 0
        for rec in jobs:
            p = printers.get(rec.printer_id)
            if p is None:
                continue
            # Ping una sola vez por impresora: no martillar las apagadas (y así
            # sus jobs quedan sin marcar para reintentar cuando enciendan).
            if p.id not in reachable:
                clients[p.id] = MoonrakerClient(p.moonraker_url)
                reachable[p.id] = clients[p.id].ping()
            if not reachable[p.id]:
                continue
            tried += 1
            if store_thumbnail(clients[p.id], rec):
                got += 1

        print(
            f"Miniaturas: {got} guardadas de {tried} intentadas "
            f"({len(jobs)} candidatas). Impresoras alcanzables: "
            f"{sum(1 for v in reachable.values() if v)}/{len(reachable)}."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
