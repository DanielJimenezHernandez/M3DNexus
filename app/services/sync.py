"""Sincronización: sondea Moonraker y vuelca jobs nuevos a la BD.

Diseño deliberadamente simple: un sondeo periódico del historial de cada
impresora. Para uso doméstico, un poll cada ~60 s es más que suficiente y es
mucho más robusto que mantener WebSockets abiertos. La ingesta es idempotente
(upsert por job_id), así que reintentar no duplica nada y rellena lo que se
hubiera perdido si la app estuvo caída.

Mejora futura: suscribirse a `notify_history_changed` por WebSocket para
reaccionar al instante; este módulo quedaría como reconciliación de respaldo.
"""

from __future__ import annotations

import logging
import threading
import time

from ..config import get_config
from ..db import session_scope
from ..integrations.homeassistant import HomeAssistantClient
from ..integrations.moonraker import MoonrakerClient, thumbnail_path
from ..models import PrintJob, Printer
from .ingest import get_autocreate_since, get_settings, ingest_job

log = logging.getLogger(__name__)

# Tope de tamaño de miniatura a guardar (las de gcode rondan 10 KB; 200 KB es
# un cinturón de seguridad por si un slicer mete una enorme).
THUMBNAIL_MAX_BYTES = 200 * 1024


def store_thumbnail(client: MoonrakerClient, rec: PrintJob) -> bool:
    """Descarga y guarda la miniatura del gcode del job (una sola vez).

    Devuelve True si la guardó. Marca ``thumbnail_tried`` siempre, para no
    reintentar en cada sondeo cuando el gcode ya no está en la impresora.
    """
    if rec.has_thumbnail or rec.thumbnail_tried:
        return False
    path = thumbnail_path(rec.filename, rec.raw_metadata)
    if not path:
        rec.thumbnail_tried = True   # el metadata no trae miniatura: permanente
        return False
    try:
        data, _ctype = client.thumbnail(path)
    except Exception:  # noqa: BLE001
        # Impresora caída/sin red (transitorio): NO se marca, se reintenta cuando
        # vuelva. Solo un no-200 (miniatura ya no está) cuenta como definitivo.
        return False
    rec.thumbnail_tried = True
    if not data or len(data) > THUMBNAIL_MAX_BYTES:
        return False
    rec.thumbnail = data
    rec.has_thumbnail = True
    return True


def _ha_client() -> HomeAssistantClient | None:
    """Cliente de HA, o None si no se puede usar ahora mismo.

    Se comprueba la conexión una vez por ronda: si HA no responde, es mejor
    saberlo aquí que descubrirlo job a job. Devolver None hace que la ingesta
    deje la energía pendiente en vez de darla por perdida.
    """
    cfg = get_config()
    if not cfg.ha_token:
        log.warning("Sin HA_TOKEN: no se calculará energía.")
        return None
    client = HomeAssistantClient(cfg.ha_url, cfg.ha_token)
    if not client.ping():
        log.warning(
            "HA no responde en %s: esta ronda no calculará energía "
            "(token caducado, URL o red). Diagnóstico: python -m app.debug_ha",
            cfg.ha_url,
        )
        return None
    return client


def sync_printer(printer_id: int, limit: int = 100) -> dict:
    """Sincroniza una impresora. Devuelve un resumen del resultado."""
    cfg = get_config()
    ha = _ha_client()
    result = {"printer_id": printer_id, "processed": 0, "errors": 0}

    with session_scope() as session:
        printer = session.get(Printer, printer_id)
        if printer is None or not printer.enabled:
            return result
        price, currency = get_settings(session)
        autocreate_since = get_autocreate_since(session)
        client = MoonrakerClient(printer.moonraker_url)
        try:
            jobs = client.history(limit=limit)
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo leer historial de %s: %s", printer.name, exc)
            result["errors"] += 1
            return result

        for job in jobs:
            try:
                rec = ingest_job(
                    session, printer, job, ha, price, currency,
                    padding_s=cfg.energy_window_padding_seconds,
                    autocreate_since=autocreate_since,
                    retention_days=cfg.ha_history_retention_days,
                )
                if rec is not None:
                    result["processed"] += 1
                    # Miniatura para el listado (offline). Un intento por job.
                    try:
                        store_thumbnail(client, rec)
                    except Exception:  # noqa: BLE001
                        pass  # la miniatura es cosmética: nunca rompe la ingesta
            except Exception as exc:  # noqa: BLE001
                log.exception("Fallo al ingerir job %s: %s", job.job_id, exc)
                result["errors"] += 1
    return result


def sync_all(limit: int = 100) -> list[dict]:
    """Sincroniza todas las impresoras habilitadas."""
    with session_scope() as session:
        ids = [p.id for p in session.query(Printer).filter_by(enabled=True).all()]
    return [sync_printer(pid, limit=limit) for pid in ids]


class SyncLoop:
    """Hilo en segundo plano que sincroniza en bucle."""

    def __init__(self, interval: int):
        self.interval = max(15, interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        log.info("SyncLoop arrancado (cada %ss)", self.interval)
        # Primer sondeo inmediato; luego espera el intervalo.
        while not self._stop.is_set():
            try:
                summary = sync_all()
                total = sum(s["processed"] for s in summary)
                if total:
                    log.info("Sincronizados %d jobs", total)
            except Exception as exc:  # noqa: BLE001
                log.exception("Error en el bucle de sincronización: %s", exc)
            self._stop.wait(self.interval)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
