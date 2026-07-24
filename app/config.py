"""Carga de configuración: archivo YAML + overrides por variable de entorno.

La configuración se divide en dos planos:

* **Infraestructura** (conexión a Home Assistant, ruta de la BD, intervalo de
  sondeo): vive aquí y se lee en cada arranque. Las variables de entorno tienen
  prioridad sobre el YAML — práctico para secretos en Docker.
* **Bootstrap** (precio de la luz, moneda, impresoras y materiales iniciales):
  se vuelca a la base de datos en el primer arranque. A partir de ahí la fuente
  de verdad es la BD y se edita desde la UI.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class PrinterSeed(BaseModel):
    name: str
    host: str = ""
    moonraker_port: int = 7125
    ui_port: int = 80
    ha_energy_entity: str | None = None
    ha_power_entity: str | None = None
    purchase_price: float = 0.0
    amortization_years: float = 2.0
    active_days_per_year: float = 250.0
    active_hours_per_day: float = 8.0


class MaterialSeed(BaseModel):
    name: str
    material_type: str = "PLA"
    brand: str | None = None
    price_per_kg: float = 0.0
    density_g_cm3: float = 1.24


class Bootstrap(BaseModel):
    electricity_price_per_kwh: float = 0.0
    currency: str = "€"
    printers: list[PrinterSeed] = Field(default_factory=list)
    materials: list[MaterialSeed] = Field(default_factory=list)


class AppConfig(BaseModel):
    ha_url: str = "http://homeassistant.local:8123"
    ha_token: str = ""
    db_path: str = "/data/printcost.db"
    poll_interval_seconds: int = 60
    # Margen (s) que se añade antes/después del job al consultar energía,
    # para no perder el pico de calentamiento ni el enfriado.
    energy_window_padding_seconds: int = 0
    # Días de historial detallado que guarda el recorder de HA (su
    # `purge_keep_days`, 10 por defecto). No se pide energía de jobs más
    # antiguos: HA ya no la tiene y sería una llamada perdida en cada sondeo.
    ha_history_retention_days: int = 10
    bootstrap: Bootstrap = Field(default_factory=Bootstrap)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache
def get_config() -> AppConfig:
    config_path = Path(os.environ.get("CONFIG_PATH", "config.yaml"))
    data = _load_yaml(config_path)
    cfg = AppConfig(**data)

    # Overrides por entorno (prioridad sobre el YAML).
    if v := os.environ.get("HA_URL"):
        cfg.ha_url = v
    if v := os.environ.get("HA_TOKEN"):
        cfg.ha_token = v
    if v := os.environ.get("DB_PATH"):
        cfg.db_path = v
    if v := os.environ.get("POLL_INTERVAL_SECONDS"):
        cfg.poll_interval_seconds = int(v)

    # Asegura que el directorio de la BD existe.
    db_dir = Path(cfg.db_path).parent
    if str(db_dir) and not db_dir.exists():
        db_dir.mkdir(parents=True, exist_ok=True)

    return cfg
