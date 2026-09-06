"""Parseo del patrón estándar de nombres de gcode.

Convención documentada en el README ("Convención de nombres de archivo"):
TODOS los campos van entre paréntesis, cada uno con el sufijo/marcador que
le da significado por dentro:

    Gcode ({base}) ({plate}) ({N}obj) ({M}x) ({V}mm3) ({tipo}) ({tiempo}) (~{proceso}) (@{preset}).gcode

Notas de diseño:
- Paréntesis y no corchetes: "[...]" es sintaxis legacy de variables en Orca
  y rompe la plantilla.
- El proceso (perfil de laminado, {print_preset} en Orca) es opcional en el
  parser; su grupo es codicioso para tolerar procesos que contengan "@"
  (p. ej. "0.20mm Standard @Voron").
- Se acepta también el estilo anterior (campos numéricos/tipo/tiempo sin
  paréntesis y ~/@ a pelo) por los archivos ya rebanados con él.

Los gcodes con otros nombres simplemente no matchean y se tratan como siempre
(sin metadatos derivados del nombre).
"""
from __future__ import annotations

import re

GCODE_NAME_RX = re.compile(
    r"^Gcode \((?P<base>.+?)\) "
    r"\((?P<plate>.*?)\) "
    r"\((?P<objects>\d+)obj\) \((?P<instances>\d+)x\) "
    r"\((?P<volume_mm3>\d+)mm3\) "
    r"\((?P<filament_type>[A-Za-z0-9+\-]+)\) "
    r"\((?P<print_time>(?:\d+[dhms])+)\) "
    r"(?:\(~(?P<process>.+)\) )?"
    r"\(@(?P<preset>.+)\)\.gcode$"
)

# Estilo anterior de la misma convención (2026-09, previo a uniformar paréntesis).
GCODE_NAME_RX_LEGACY = re.compile(
    r"^Gcode (?P<base>.+?) "
    r"\((?P<plate>.*?)\) "
    r"(?P<objects>\d+)obj (?P<instances>\d+)x "
    r"(?P<volume_mm3>\d+)mm3 "
    r"(?P<filament_type>[A-Za-z0-9+\-]+) "
    r"(?P<print_time>(?:\d+[dhms])+) "
    r"(?:~(?P<process>.+) )?"
    r"@(?P<preset>.+)\.gcode$"
)


def parse_gcode_name(filename: str | None) -> dict | None:
    """Campos del nombre según la convención, o None si no la sigue.

    Acepta rutas con subcarpetas (usa solo el nombre base).
    """
    name = (filename or "").rsplit("/", 1)[-1]
    m = GCODE_NAME_RX.match(name) or GCODE_NAME_RX_LEGACY.match(name)
    if not m:
        return None
    d = m.groupdict()
    for key in ("objects", "instances", "volume_mm3"):
        d[key] = int(d[key])
    return d
