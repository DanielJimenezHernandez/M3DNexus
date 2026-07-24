"""Lectura de perfiles de filamento de OrcaSlicer.

Un perfil de usuario de Orca es un JSON que guarda **solo lo que has
sobrescrito**: el resto de claves valen ``"nil"`` o no aparecen, y se heredan
del perfil de ``inherits``. Eso hace que el propio fichero diga cuánto has
calibrado, sin depender de cómo lo hayas bautizado.

Dos cosas que conviene no confundir:

* ``compatible_printers`` lista **dónde se puede usar** el perfil, no para qué
  está calibrado: viene del perfil base y suele traer todas las boquillas del
  modelo. Sirve para identificar la impresora con fiabilidad (sale del catálogo
  de Orca, no de lo que teclee el usuario), pero no la boquilla.
* La boquilla concreta está en el sufijo que el usuario pone en el nombre
  (``"... @ Ender3v3SE 0.4 Nozzle"``), que es convención suya.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .calibration import normalize_alias, split_profile
from .filament import DENSITY_BY_TYPE

# Claves que identifican el perfil o su procedencia: no son calibración.
_META_KEYS = {
    "name", "type", "from", "base_id", "version", "inherits", "instantiation",
    "filament_id", "setting_id", "filament_settings_id", "compatible_printers",
    "compatible_printers_condition", "filament_vendor", "default_filament_colour",
    "filament_colour", "is_custom_defined",
}

# Valores que significan "heredado, sin tocar".
_VACIOS = {"nil", "", None}

# Grados de calibración. La distinción no es cosmética: ajustar temperaturas es
# otra cosa que haber sacado el pressure advance y el flow ratio de la máquina,
# que es lo que de verdad cambia el resultado de la pieza.
CAL_FULL = "FULL"     # pressure advance y/o flow ratio calibrados
CAL_BASIC = "BASIC"   # solo temperaturas, velocidades y demás
CAL_NONE = "NONE"     # hereda el perfil genérico sin tocar nada


def _lista(valor) -> list[str]:
    """Normaliza un campo que puede venir como lista o como cadena con ';'.

    Orca escribe ``'"A 0.4 nozzle";"B 0.4 nozzle"'`` en unas versiones y una
    lista JSON en otras.
    """
    if valor is None:
        return []
    if isinstance(valor, list):
        return [str(v).strip().strip('"').strip() for v in valor if str(v).strip()]
    partes = re.split(r'"\s*;\s*"|;', str(valor))
    return [p.strip().strip('"').strip() for p in partes if p.strip().strip('"').strip()]


def _escalar(valor):
    """Primer valor útil de un campo (Orca guarda muchos como lista de 1)."""
    if isinstance(valor, list):
        valor = valor[0] if valor else None
    if valor in _VACIOS:
        return None
    return str(valor).strip().strip('"').strip() or None


def _num(valor) -> float | None:
    v = _escalar(valor)
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


# La boquilla aparece con y sin paréntesis, y detrás puede venir una variante
# del perfil de máquina ("- Mod", "- Timelapse"). Todo eso sobra para el modelo.
_BOQUILLA_Y_RESTO = re.compile(r"\s*\(?\s*[\d.]+\s*nozzle\s*\)?.*$", re.IGNORECASE)


def printer_model(compatible: str) -> str:
    """Modelo de impresora, sin boquilla ni variante del perfil.

    ``"Creality Ender-3 V3 SE 0.4 nozzle"``      → ``"Creality Ender-3 V3 SE"``
    ``"Creality K1 (0.4 nozzle) - Timelapse"``   → ``"Creality K1"``
    """
    return _BOQUILLA_Y_RESTO.sub("", compatible.strip()).strip()


def nozzle_from_name(nombre: str) -> float | None:
    """Boquilla declarada en el sufijo del nombre del perfil.

    ``"Elegoo PLA Purple @ Ender3v3SE 0.4 Nozzle"`` → ``0.4``. Es convención del
    usuario, así que puede faltar.
    """
    _, sufijo = split_profile(nombre)
    if not sufijo:
        return None
    m = re.search(r"(\d\.\d+)", sufijo)
    return float(m.group(1)) if m else None


def material_type(perfil: dict) -> str | None:
    """Tipo de material (PLA, PETG...) buscándolo donde Orca lo deje.

    El campo ``filament_type`` solo está si se sobrescribió; lo normal es que se
    herede, así que se recurre al nombre del perfil y al del padre.
    """
    explicito = _escalar(perfil.get("filament_type"))
    if explicito:
        return explicito.upper().rstrip("+")
    for campo in ("name", "inherits", "filament_settings_id"):
        texto = _escalar(perfil.get(campo)) or ""
        for token in re.split(r"[\s_\-@|]+", texto):
            clave = token.upper().rstrip("+")
            if clave in DENSITY_BY_TYPE:
                return clave
    return None


@dataclass
class OrcaProfile:
    """Perfil de filamento de Orca, ya normalizado."""

    name: str
    vendor: str | None = None
    material_type: str | None = None
    colour: str | None = None
    nozzle_mm: float | None = None
    printer_models: list[str] = field(default_factory=list)
    # Claves realmente sobrescritas: la medida objetiva de cuánto se ha tocado.
    tuned_keys: list[str] = field(default_factory=list)
    nozzle_temp: float | None = None
    bed_temp: float | None = None
    flow_ratio: float | None = None
    pressure_advance: float | None = None
    max_volumetric_speed: float | None = None
    pressure_advance_enabled: bool = False
    inherits: str | None = None

    @property
    def is_tuned(self) -> bool:
        """True si el perfil sobrescribe algo; si no, es el genérico tal cual."""
        return bool(self.tuned_keys)

    @property
    def calibration_level(self) -> str:
        """Cuán calibrado está, que no es lo mismo que si está tocado.

        * ``FULL``  — tiene pressure advance y/o flow ratio: se ha calibrado de
          verdad para esa máquina, que es lo que cambia la calidad de la pieza.
        * ``BASIC`` — solo se han ajustado temperaturas, velocidades o similares.
        * ``NONE``  — hereda el genérico entero.
        """
        if self.pressure_advance is not None or self.flow_ratio is not None:
            return CAL_FULL
        return CAL_BASIC if self.tuned_keys else CAL_NONE

    @property
    def filament_name(self) -> str:
        """Nombre del filamento sin el sufijo '@impresora'."""
        base, _ = split_profile(self.name)
        return base


def parse_orca_profile(perfil: dict) -> OrcaProfile | None:
    """Convierte el JSON de un perfil de filamento de Orca en OrcaProfile."""
    if not isinstance(perfil, dict):
        return None
    # Los perfiles de impresora y de proceso comparten carpeta padre.
    if _escalar(perfil.get("type")) not in (None, "filament"):
        return None
    nombre = _escalar(perfil.get("name")) or _escalar(perfil.get("filament_settings_id"))
    if not nombre:
        return None

    modelos: list[str] = []
    for c in _lista(perfil.get("compatible_printers")):
        m = printer_model(c)
        if m and m not in modelos:
            modelos.append(m)

    tuned = sorted(
        k for k, v in perfil.items()
        if k not in _META_KEYS and _escalar(v) is not None
    )

    return OrcaProfile(
        name=nombre,
        vendor=_escalar(perfil.get("filament_vendor")),
        material_type=material_type(perfil),
        colour=_escalar(perfil.get("default_filament_colour"))
               or _escalar(perfil.get("filament_colour")),
        nozzle_mm=nozzle_from_name(nombre),
        printer_models=modelos,
        tuned_keys=tuned,
        nozzle_temp=_num(perfil.get("nozzle_temperature")),
        bed_temp=_num(perfil.get("hot_plate_temp")),
        flow_ratio=_num(perfil.get("filament_flow_ratio")),
        pressure_advance=_num(perfil.get("pressure_advance")),
        max_volumetric_speed=_num(perfil.get("filament_max_volumetric_speed")),
        # Un perfil puede traer el valor pero tenerlo desactivado; conviene
        # saberlo antes de dar la calibración por buena.
        pressure_advance_enabled=_escalar(perfil.get("enable_pressure_advance")) == "1",
        inherits=_escalar(perfil.get("inherits")),
    )


def _squash(texto: str) -> str:
    """Solo letras y dígitos: hace que "K1 SE" y "K1SE" sean comparables."""
    return re.sub(r"[^a-z0-9]", "", texto.lower())


def _tokens(texto: str) -> set[str]:
    return {t for t in re.split(r"[\s\-_.]+", texto.lower()) if t}


# Longitud mínima para fiarse de una coincidencia por contención: evita que un
# "K1" suelto se enganche a cualquier cosa que empiece igual.
_MIN_CONTENIDO = 6


def _score(modelo: str, nombre_impresora: str) -> int:
    """Cuánto se parecen un modelo de Orca y una impresora de la app."""
    a, b = _squash(modelo), _squash(nombre_impresora)
    if a and a == b:
        return 100 + len(a)                       # "Creality K1 SE" = "Creality K1SE"
    if a and b and (a in b or b in a):
        corto = min(len(a), len(b))
        if corto >= _MIN_CONTENIDO:
            return 50 + corto                     # "Creality Ender-3 V3 SE" ⊃ "Ender 3v3 SE"
    comunes = len(_tokens(modelo) & _tokens(nombre_impresora))
    return comunes if comunes >= 2 else 0


def match_printer(profile: OrcaProfile, printers) -> object | None:
    """Empareja el perfil con una impresora de la app.

    Se puntúan **todas** las combinaciones modelo × impresora y gana la mejor.
    Hace falta porque un mismo perfil puede declarar varios modelos: el de la
    Mint lista la K1 y la K1 SE, y quedarse con el primero daría la máquina
    equivocada.

    Si no hay ``compatible_printers`` (perfiles escritos a mano), se recurre a
    los alias deducidos del historial.
    """
    mejor, puntos = None, 0
    for modelo in profile.printer_models:
        for p in printers:
            s = _score(modelo, p.name)
            if s > puntos:
                mejor, puntos = p, s
    if mejor is not None:
        return mejor

    _, sufijo = split_profile(profile.name)
    if sufijo:
        alias = normalize_alias(sufijo)
        for p in printers:
            if alias in set(p.orca_aliases or []):
                return p
    return None
