"""Cliente de Moonraker (API HTTP).

Moonraker ya mantiene el historial de impresiones en su componente
``[history]``. Esta clase lo lee; no genera datos, solo los consume.

Doc del endpoint: GET /server/history/list
  → {"result": {"count": N, "jobs": [ {job}, ... ]}}

Cada job trae, entre otros:
  job_id, filename, status, start_time, end_time (epoch s),
  print_duration, total_duration, filament_used (mm),
  metadata { filament_total, filament_weight_total, filament_type,
             filament_name, estimated_time, ... }
"""

from __future__ import annotations

import logging
import posixpath
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)


def thumbnail_path(filename: str | None, meta: dict | None) -> str | None:
    """Ruta de la miniatura más grande del gcode, relativa al root ``gcodes``.

    En los metadatos viene relativa al directorio del propio gcode, así que hay
    que recomponerla con la carpeta del archivo para que sirva al descargarla.
    """
    thumbs = (meta or {}).get("thumbnails") or []
    if not thumbs:
        return None
    best = max(thumbs, key=lambda t: t.get("width", 0) or t.get("size", 0))
    rel = best.get("relative_path")
    if not rel:
        return None
    return posixpath.join(posixpath.dirname(filename or ""), rel)


def _epoch_to_dt(value) -> datetime | None:
    try:
        if value in (None, 0, "0"):
            return None
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


@dataclass
class MoonrakerJob:
    """Job normalizado a partir de la respuesta de Moonraker."""

    job_id: str
    filename: str | None
    status: str
    start_time: datetime | None
    end_time: datetime | None
    print_duration_s: float
    total_duration_s: float
    filament_used_mm: float
    # Peso de filamento según el slicer (g), si está en los metadatos.
    filament_weight_g: float | None
    filament_type: str | None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_api(cls, job: dict) -> "MoonrakerJob":
        meta = job.get("metadata") or {}
        weight = meta.get("filament_weight_total")
        return cls(
            job_id=str(job.get("job_id")),
            filename=job.get("filename"),
            status=job.get("status", "completed"),
            start_time=_epoch_to_dt(job.get("start_time")),
            end_time=_epoch_to_dt(job.get("end_time")),
            print_duration_s=float(job.get("print_duration") or 0.0),
            total_duration_s=float(job.get("total_duration") or 0.0),
            filament_used_mm=float(job.get("filament_used") or 0.0),
            filament_weight_g=float(weight) if weight else None,
            filament_type=meta.get("filament_type"),
            metadata=meta,
        )


class MoonrakerClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def history(self, limit: int = 100, since: datetime | None = None) -> list[MoonrakerJob]:
        """Devuelve los jobs del historial, del más reciente al más antiguo.

        ``since`` filtra por start_time (epoch) en el lado servidor cuando se
        indica, para no traer todo el historial en cada sondeo.
        """
        params: dict = {"limit": limit, "order": "desc"}
        if since is not None:
            params["start"] = since.timestamp()
        url = f"{self.base_url}/server/history/list"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        jobs = data.get("result", {}).get("jobs", []) or []
        return [MoonrakerJob.from_api(j) for j in jobs]

    def status(self) -> dict:
        """Estado en vivo: print_stats, display_status y virtual_sdcard.

        Devuelve el bloque ``status`` de /printer/objects/query.
        """
        url = f"{self.base_url}/printer/objects/query"
        params = {"print_stats": "", "display_status": "", "virtual_sdcard": ""}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
        return resp.json().get("result", {}).get("status", {})

    def list_files(self, root: str = "gcodes") -> list[dict]:
        """Lista los gcode disponibles en la impresora (más recientes primero)."""
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(f"{self.base_url}/server/files/list", params={"root": root})
            resp.raise_for_status()
        files = resp.json().get("result", []) or []
        files.sort(key=lambda f: f.get("modified", 0), reverse=True)
        return files

    def file_metadata(self, filename: str) -> dict | None:
        """Metadatos del laminador para un gcode (peso, tiempo, material...)."""
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(
                    f"{self.base_url}/server/files/metadata",
                    params={"filename": filename},
                )
                if resp.status_code != 200:
                    return None
                return resp.json().get("result")
        except httpx.HTTPError:
            return None

    def thumbnail(self, gcode_path: str) -> tuple[bytes, str] | tuple[None, None]:
        """Descarga una miniatura por su ruta relativa al root 'gcodes'.

        Distingue dos fallos para quien decide si reintentar: un no-200 (la
        miniatura ya no está en la impresora) devuelve (None, None) —permanente—;
        un error de transporte (impresora apagada/sin red) se PROPAGA como
        excepción —transitorio—, para no darlo por perdido.
        """
        from urllib.parse import quote

        url = f"{self.base_url}/server/files/gcodes/{quote(gcode_path)}"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return None, None
            return resp.content, resp.headers.get("content-type", "image/png")

    def ping(self) -> bool:
        """Comprueba conectividad con la instancia."""
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(f"{self.base_url}/server/info")
                return resp.status_code == 200
        except httpx.HTTPError as exc:
            log.warning("Moonraker %s no responde: %s", self.base_url, exc)
            return False
