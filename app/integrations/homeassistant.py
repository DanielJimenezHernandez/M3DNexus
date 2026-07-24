"""Cliente de Home Assistant (API REST).

Lee la energía consumida por un POW3 durante la ventana de una impresión.

Se usa el endpoint History:
  GET /api/history/period/{start_iso}?filter_entity_id={entity}
      &end_time={end_iso}&minimal_response&no_attributes

La energía del POW3 es un contador **acumulado** (kWh). El consumo de la
impresión = último valor − primer valor dentro de la ventana.

Nota: para tarifa plana esto basta y es robusto. Si en el futuro se añade
tarifa por tramos, conviene migrar a la API de estadísticas
(`recorder/statistics_during_period`) vía WebSocket, que trocea por hora y
maneja reinicios del contador.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

log = logging.getLogger(__name__)


def _to_float(state: str | None) -> float | None:
    if state in (None, "", "unknown", "unavailable"):
        return None
    try:
        return float(state)
    except (TypeError, ValueError):
        return None


class HomeAssistantClient:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._headers = {"Authorization": f"Bearer {token}"}

    def energy_between(
        self, entity_id: str, start: datetime, end: datetime
    ) -> float | None:
        """kWh consumidos por ``entity_id`` entre ``start`` y ``end``.

        Devuelve None si no hay datos suficientes o si la entidad no responde.
        """
        if not entity_id or start is None or end is None:
            return None

        url = f"{self.base_url}/api/history/period/{start.isoformat()}"
        params = {
            "filter_entity_id": entity_id,
            "end_time": end.isoformat(),
            "minimal_response": "",
            "no_attributes": "",
        }
        try:
            with httpx.Client(timeout=self.timeout, headers=self._headers) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            log.warning("HA history %s falló: %s", entity_id, exc)
            return None

        # La respuesta es una lista con una sublista por entidad.
        if not data or not data[0]:
            return None
        states = data[0]

        numeric = [
            (s.get("last_changed") or s.get("last_updated"), _to_float(s.get("state")))
            for s in states
        ]
        numeric = [(t, v) for t, v in numeric if v is not None]
        if len(numeric) < 2:
            return None

        numeric.sort(key=lambda x: x[0] or "")
        first = numeric[0][1]
        last = numeric[-1][1]
        delta = last - first
        # Un contador acumulado nunca debería decrecer; si lo hace, hubo un
        # reinicio dentro de la ventana → no fiable, mejor avisar.
        if delta < 0:
            log.warning(
                "Energía decreciente en %s (%.3f → %.3f): posible reinicio",
                entity_id,
                first,
                last,
            )
            return None
        return round(delta, 4)

    def state(self, entity_id: str) -> dict | None:
        """Estado actual de una entidad: {state, unit, state_class, ...}.

        Útil para diagnóstico (confirmar que la entidad existe y su valor).
        """
        try:
            with httpx.Client(timeout=self.timeout, headers=self._headers) as client:
                resp = client.get(f"{self.base_url}/api/states/{entity_id}")
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            log.warning("HA state %s falló: %s", entity_id, exc)
            return None
        attrs = data.get("attributes", {})
        return {
            "state": data.get("state"),
            "unit": attrs.get("unit_of_measurement"),
            "state_class": attrs.get("state_class"),
            "device_class": attrs.get("device_class"),
            "last_changed": data.get("last_changed"),
        }

    def ping(self) -> bool:
        try:
            with httpx.Client(timeout=self.timeout, headers=self._headers) as client:
                resp = client.get(f"{self.base_url}/api/")
                return resp.status_code == 200
        except httpx.HTTPError as exc:
            log.warning("HA %s no responde: %s", self.base_url, exc)
            return False
