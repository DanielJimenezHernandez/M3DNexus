"""Seguimiento en vivo de impresiones en curso y su coste acumulado.

Un loop en segundo plano consulta cada impresora habilitada; para las que
estén imprimiendo calcula, en tiempo casi real:
  * energía consumida desde el inicio (delta del contador del POW3 en HA),
  * potencia instantánea,
  * filamento usado hasta ahora,
  * coste acumulado (mismo motor que el registro definitivo),
  * progreso y tiempo restante estimado.

El estado vive en memoria (no en BD): cuando la impresión termina, el job pasa
a ser un registro permanente vía la sincronización normal.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from ..cost import DEFAULT_FILAMENT_DENSITY, CostInputs, compute_cost, mm_to_grams
from ..db import session_scope
from ..integrations.moonraker import MoonrakerClient, thumbnail_path
from ..models import Printer
from .filament import resolve_or_create_material
from .ingest import get_settings
from .sync import _ha_client

log = logging.getLogger(__name__)


class LiveTracker:
    """Cache en memoria del estado en vivo por impresora."""

    def __init__(self) -> None:
        self._state: dict[int, dict] = {}
        self._lock = threading.Lock()

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self._state.values())

    def update(self) -> None:
        ha = _ha_client()
        now = datetime.now(timezone.utc)
        new_state: dict[int, dict] = {}

        with session_scope() as session:
            price, currency = get_settings(session)
            printers = session.query(Printer).filter_by(enabled=True).all()

            for p in printers:
                client = MoonrakerClient(p.moonraker_url)
                try:
                    status = client.status()
                except Exception:  # noqa: BLE001  (impresora apagada/inaccesible)
                    continue

                ps = status.get("print_stats", {})
                if ps.get("state") != "printing":
                    continue

                filename = ps.get("filename")
                print_duration = float(ps.get("print_duration") or 0.0)
                filament_mm = float(ps.get("filament_used") or 0.0)
                progress = (
                    status.get("display_status", {}).get("progress")
                    or status.get("virtual_sdcard", {}).get("progress")
                    or 0.0
                )

                # Inicio: se conserva entre ciclos si es la misma impresión;
                # si arrancamos a media impresión, se aproxima.
                prev = self._state.get(p.id)
                if prev and prev.get("filename") == filename and prev.get("_start"):
                    start = prev["_start"]
                else:
                    start = now - timedelta(seconds=print_duration)

                # Material y ETA desde los metadatos del laminador (sin crear:
                # el filamento se crea cuando la impresión termina y se ingiere).
                meta = client.file_metadata(filename) or {}
                material = resolve_or_create_material(
                    session, meta, meta.get("filament_type"), allow_create=False
                )
                density = material.density_g_cm3 if material else DEFAULT_FILAMENT_DENSITY
                weight_g = round(mm_to_grams(filament_mm, density_g_cm3=density), 2)

                # Energía y potencia en vivo desde HA.
                energy = None
                power = None
                if ha and p.ha_energy_entity:
                    energy = ha.energy_between(p.ha_energy_entity, start, now)
                if ha and p.ha_power_entity:
                    st = ha.state(p.ha_power_entity)
                    if st and st["state"] not in (None, "unknown", "unavailable"):
                        try:
                            power = float(st["state"])
                        except (TypeError, ValueError):
                            power = None

                breakdown = compute_cost(
                    CostInputs(
                        energy_kwh=energy or 0.0,
                        electricity_price_per_kwh=price,
                        filament_weight_g=weight_g,
                        filament_price_per_kg=material.price_per_kg if material else 0.0,
                        print_duration_hours=print_duration / 3600.0,
                        machine_purchase_price=p.purchase_price or 0.0,
                        machine_lifetime_hours=p.lifetime_hours,
                    )
                )

                est = float(meta.get("estimated_time") or 0.0)
                eta_s = max(0.0, est - print_duration) if est else None

                thumb_path = thumbnail_path(filename, meta)

                new_state[p.id] = {
                    "_start": start,  # privado (no serializar)
                    "printer_id": p.id,
                    "printer_name": p.name,
                    "filename": filename,
                    "progress": round(progress, 4),
                    "elapsed_s": print_duration,
                    "eta_s": eta_s,
                    "power_w": power,
                    "energy_kwh": energy,
                    "filament_g": weight_g,
                    "material": material.name if material else None,
                    "thumbnail": thumb_path,
                    "currency": currency,
                    "cost": breakdown.as_dict(),
                }

        with self._lock:
            self._state = new_state


# Singleton compartido por el loop y la API.
tracker = LiveTracker()


class LiveLoop:
    """Hilo que refresca el estado en vivo periódicamente."""

    def __init__(self, interval: int = 15):
        self.interval = max(5, interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        log.info("LiveLoop arrancado (cada %ss)", self.interval)
        while not self._stop.is_set():
            try:
                tracker.update()
            except Exception as exc:  # noqa: BLE001
                log.exception("Error en LiveLoop: %s", exc)
            self._stop.wait(self.interval)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
