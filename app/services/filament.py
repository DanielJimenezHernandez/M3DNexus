"""Parseo de filamentos desde los metadatos de Moonraker y auto-creación.

El campo ``filament_name`` del gcode viene en formato del laminador, p.ej.:
    "Elegoo PLA+ White @ Hi 0.4 Nozzle"
A veces multi-material, repetido con ';' y comillas:
    'Elegoo PLA+ White @ ... ";" ERYONE PLA @ ...'

De ahí extraemos: nombre completo (clave única), marca, tipo y color.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..cost import DEFAULT_FILAMENT_DENSITY
from ..models import Material

# Densidad (g/cm³) por tipo de material, para estimar peso si falta y para
# inicializar filamentos auto-creados.
DENSITY_BY_TYPE = {
    "PLA": 1.24, "PETG": 1.27, "PET": 1.27, "ABS": 1.04, "ASA": 1.07,
    "TPU": 1.21, "TPE": 1.21, "NYLON": 1.14, "PA": 1.14, "PC": 1.20,
    "PVA": 1.23, "HIPS": 1.04, "PP": 0.90,
}


def density_for(material_type: str | None) -> float:
    if not material_type:
        return DEFAULT_FILAMENT_DENSITY
    key = material_type.upper().rstrip("+").strip()
    return DENSITY_BY_TYPE.get(key, DEFAULT_FILAMENT_DENSITY)


def _first_value(raw) -> str:
    """Primer valor de un campo que puede venir como lista ';' con comillas."""
    if not raw:
        return ""
    return str(raw).split(";")[0].strip().strip('"').strip()


@dataclass
class ParsedFilament:
    full_name: str
    brand: str | None
    material_type: str | None
    color: str | None


def parse_filament(filament_name, filament_type=None) -> ParsedFilament | None:
    """Descompone el nombre de filamento en (nombre, marca, tipo, color).

    Soporta dos formatos:

    1. **Convención recomendada (con '|'):** ``Marca | Tipo | Color``
       Determinista, sin ambigüedad con marcas/colores de varias palabras.
       El tipo canónico se toma de ``filament_type`` (metadatos) si está; el del
       nombre es respaldo. P.ej. ``Elegoo | PLA+ | White``.

    2. **Heurístico (nombre libre):** ``Elegoo PLA+ White`` — se localiza el
       token del tipo y se parte: antes = marca, después = color. Compatibilidad
       con los nombres antiguos.
    """
    name = _first_value(filament_name)
    if not name:
        return None
    # El laminador añade "@impresora" a los perfiles específicos de una máquina.
    # Los espacios alrededor de la arroba son opcionales: en los perfiles reales
    # conviven "White @ Hi 0.4" y "Marble @Ender 0.4", y cortar solo por " @ "
    # dejaba el nombre de la impresora dentro del material.
    full = re.split(r"\s*@\s*", name, maxsplit=1)[0].strip().strip('"').strip()
    if not full:
        return None

    meta_type = _first_value(filament_type) or None

    # --- Formato estructurado con '|' (recomendado) ------------------------
    if "|" in full:
        parts = [p.strip() for p in full.split("|")]
        brand = parts[0] or None
        rest = parts[1:]
        name_type = color = None
        if len(rest) >= 2:
            name_type = rest[0] or None
            color = rest[1] or None
        elif len(rest) == 1:
            token = rest[0]
            if token and token.upper().rstrip("+") in DENSITY_BY_TYPE:
                name_type = token
            else:
                color = token or None
        mtype = meta_type or name_type
        type_disp = name_type or mtype
        display = " ".join(p for p in [brand, type_disp, color] if p) or full
        return ParsedFilament(
            full_name=display, brand=brand, material_type=mtype, color=color
        )

    # --- Heurístico (nombre libre, compatibilidad) -------------------------
    mtype = meta_type
    tokens = full.split()

    # Localiza el token del tipo (p.ej. "PLA" o "PLA+") para separar
    # marca (antes) de color (después).
    idx = None
    if mtype:
        t = mtype.upper().rstrip("+")
        for i, tok in enumerate(tokens):
            if tok.upper().rstrip("+") == t or tok.upper().startswith(t):
                idx = i
                break
    if idx is None:  # no había tipo: busca cualquier tipo conocido en el nombre
        for i, tok in enumerate(tokens):
            if tok.upper().rstrip("+") in DENSITY_BY_TYPE:
                idx = i
                mtype = mtype or tok.upper().rstrip("+")
                break

    if idx is not None:
        brand = " ".join(tokens[:idx]) or None
        color = " ".join(tokens[idx + 1:]) or None
    else:
        brand = tokens[0] if tokens else None
        color = " ".join(tokens[1:]) or None

    return ParsedFilament(full_name=full, brand=brand, material_type=mtype, color=color)


def parse_all_filaments(metadata: dict | None, filament_type=None) -> list[ParsedFilament]:
    """Parsea TODOS los filamentos de un gcode (multi-material incluido).

    El laminador une los filamentos con ';' (con comillas): cada nombre se parea
    con su tipo por posición. Devuelve la lista sin duplicados, en orden. Se usa
    para dejar elegir a mano con cuál se imprimió cuando el proyecto tenía varios.
    """
    raw = (metadata or {}).get("filament_name")
    if not raw:
        return []
    names = [n.strip().strip('"').strip() for n in str(raw).split(";")]
    types_raw = filament_type or (metadata or {}).get("filament_type") or ""
    types = [t.strip().strip('"').strip() for t in str(types_raw).split(";")]

    out: list[ParsedFilament] = []
    seen: set[str] = set()
    for i, nm in enumerate(names):
        if not nm:
            continue
        ftype = types[i] if i < len(types) and types[i] else (types[0] if types else None)
        parsed = parse_filament(nm, ftype)
        if parsed and parsed.full_name and parsed.full_name.lower() not in seen:
            seen.add(parsed.full_name.lower())
            out.append(parsed)
    return out


def get_or_create_by_parsed(
    session: Session, parsed: ParsedFilament, filament_type=None
) -> Material:
    """Devuelve el Material con ese nombre; si no existe, lo crea (precio 0).

    Igual que la auto-creación de la ingesta, pero disparado a mano al elegir un
    filamento concreto del gcode. Precio 0 → queda 'a revisar' hasta ponerlo.
    """
    existing = session.scalar(
        select(Material).where(func.lower(Material.name) == parsed.full_name.lower())
    )
    if existing:
        return existing
    mtype = (parsed.material_type or _first_value(filament_type) or "PLA").rstrip("+")
    material = Material(
        name=parsed.full_name,
        material_type=mtype or "PLA",
        brand=parsed.brand,
        color=parsed.color,
        price_per_kg=0.0,
        density_g_cm3=density_for(parsed.material_type or filament_type),
        active=True,
        auto_created=True,
    )
    session.add(material)
    session.flush()
    return material


def resolve_by_type(session: Session, filament_type) -> Material | None:
    """Material activo cuyo tipo case (comportamiento de respaldo / genérico)."""
    ft = _first_value(filament_type).strip().lower()
    if not ft:
        return None
    for m in session.scalars(select(Material).where(Material.active.is_(True))).all():
        if m.material_type and m.material_type.strip().lower() == ft:
            return m
    return None


def resolve_or_create_material(
    session: Session,
    metadata: dict | None,
    filament_type,
    allow_create: bool,
) -> Material | None:
    """Resuelve el material por nombre exacto; si no existe y se permite, lo crea.

    1. Parsea el nombre del filamento de los metadatos.
    2. Busca un Material con ese nombre (case-insensitive) → lo reutiliza.
    3. Si no existe y ``allow_create`` → lo crea (precio 0, marca/tipo/color
       parseados, densidad por tipo) marcándolo ``auto_created``.
    4. Si no hay nombre o no se permite crear → cae al material genérico por tipo.
    """
    parsed = parse_filament((metadata or {}).get("filament_name"), filament_type)

    if parsed and parsed.full_name:
        existing = session.scalar(
            select(Material).where(func.lower(Material.name) == parsed.full_name.lower())
        )
        if existing:
            return existing
        if allow_create:
            mtype = (parsed.material_type or _first_value(filament_type) or "PLA").rstrip("+")
            material = Material(
                name=parsed.full_name,
                material_type=mtype or "PLA",
                brand=parsed.brand,
                color=parsed.color,
                price_per_kg=0.0,
                density_g_cm3=density_for(parsed.material_type or filament_type),
                active=True,
                auto_created=True,
            )
            session.add(material)
            session.flush()
            return material

    return resolve_by_type(session, filament_type)
