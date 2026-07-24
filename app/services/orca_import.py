"""Importa perfiles de OrcaSlicer a la matriz de calibración.

Se hace en dos pasos a propósito. Primero ``plan_import`` propone qué haría con
cada perfil sin tocar nada; después ``apply_import`` ejecuta lo que se haya
confirmado. El motivo es que los materiales están enlazados a las impresiones ya
registradas: fusionar dos que no eran el mismo reasigna costes de forma callada
y no hay vuelta atrás fácil.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FilamentCalibration, Material, Printer
from .filament import density_for
from .orca import CAL_NONE, match_printer, parse_orca_profile

# A partir de este parecido se propone fusionar con un material existente.
# Por debajo se propone crear uno nuevo, que siempre se puede cambiar a mano.
SIMILAR_MIN = 0.86


def _norm(nombre: str) -> str:
    """Forma comparable de un nombre de filamento."""
    s = (nombre or "").lower()
    s = re.sub(r"[^a-z0-9+ ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def match_material(nombre: str, materiales) -> tuple[object | None, float]:
    """Material existente más parecido, con su grado de parecido (0..1).

    Exacto tras normalizar → 1.0. Si no, el más próximo por similitud, que es
    lo que rescata las erratas de tecleo ("Whithe" por "White").
    """
    objetivo = _norm(nombre)
    if not objetivo:
        return None, 0.0
    mejor, score = None, 0.0
    for m in materiales:
        cand = _norm(m.name)
        if cand == objetivo:
            return m, 1.0
        s = SequenceMatcher(None, objetivo, cand).ratio()
        if s > score:
            mejor, score = m, s
    return (mejor, score) if score >= SIMILAR_MIN else (None, score)


def plan_import(session: Session, perfiles: list[dict]) -> list[dict]:
    """Propone qué hacer con cada perfil, sin escribir nada.

    Cada entrada del plan lleva ya elegidos el material y la impresora más
    probables; la UI los muestra para que se confirmen o se cambien.
    """
    materiales = session.scalars(select(Material)).all()
    impresoras = session.scalars(select(Printer)).all()
    plan = []

    for crudo in perfiles:
        p = parse_orca_profile(crudo)
        if p is None:
            continue

        impresora = match_printer(p, impresoras)
        material, score = match_material(p.filament_name, materiales)

        plan.append({
            "profile": p.name,
            "filament_name": p.filament_name,
            "vendor": p.vendor,
            "material_type": p.material_type,
            "colour": p.colour,
            "nozzle_mm": p.nozzle_mm,
            "level": p.calibration_level,
            "tuned_count": len(p.tuned_keys),
            "pressure_advance": p.pressure_advance,
            "pressure_advance_enabled": p.pressure_advance_enabled,
            "flow_ratio": p.flow_ratio,
            "nozzle_temp": p.nozzle_temp,
            "bed_temp": p.bed_temp,
            "printer_id": impresora.id if impresora else None,
            "printer_name": impresora.name if impresora else None,
            "material_id": material.id if material else None,
            "material_name": material.name if material else None,
            "match": ("exact" if score >= 1.0 else "similar") if material else "new",
            "match_score": round(score, 3),
            # Motivos por los que la fila necesita atención antes de importar.
            "warnings": _warnings(p, impresora, p.nozzle_mm),
        })

    plan.sort(key=lambda r: (r["printer_name"] or "zz", r["filament_name"]))
    return plan


def _warnings(p, impresora, nozzle) -> list[str]:
    avisos = []
    if impresora is None:
        avisos.append("no se ha podido identificar la impresora")
    if nozzle is None:
        avisos.append("el nombre no dice la boquilla")
    if p.calibration_level == CAL_NONE:
        avisos.append("el perfil no sobrescribe nada: hereda el genérico")
    if p.pressure_advance is not None and not p.pressure_advance_enabled:
        avisos.append("tiene pressure advance pero está desactivado")
    return avisos


def apply_import(session: Session, decisiones: list[dict]) -> dict:
    """Aplica las filas confirmadas del plan.

    Cada decisión trae el perfil crudo y a qué material e impresora va. Las que
    no tengan impresora o boquilla se saltan: sin esas dos no hay dónde colgar
    la calibración.
    """
    resumen = {"materiales_nuevos": 0, "materiales_actualizados": 0,
               "calibraciones_nuevas": 0, "calibraciones_actualizadas": 0,
               "omitidas": 0}
    ahora = datetime.now(timezone.utc)

    # La sesión va con autoflush=False, así que un SELECT dentro del bucle no ve
    # las filas que este mismo lote acaba de crear (siguen pendientes). Por eso
    # se llevan índices en memoria: sin ellos, dos perfiles que caen en la misma
    # clave se insertan por duplicado y el flush final rompe la restricción
    # UNIQUE. Pasa de forma natural aquí: el mismo rollo calibrado en varias
    # máquinas son varios perfiles.
    por_nombre = {_norm(m.name): m for m in session.scalars(select(Material)).all()}
    por_clave = {
        (c.material_id, c.printer_id, c.nozzle_mm): c
        for c in session.scalars(select(FilamentCalibration)).all()
    }

    for d in decisiones:
        p = parse_orca_profile(d.get("profile_json") or {})
        printer_id = d.get("printer_id")
        nozzle = d.get("nozzle_mm")
        if p is None or not printer_id or not nozzle:
            resumen["omitidas"] += 1
            continue

        material = session.get(Material, d["material_id"]) if d.get("material_id") else None
        # Aunque la previsualización dijera "nuevo", puede existir ya: otra fila
        # de este lote lo creó, o el nombre coincide exacto con uno existente.
        if material is None:
            material = por_nombre.get(_norm(p.filament_name))
        if material is None:
            material = Material(
                name=p.filament_name,
                material_type=(p.material_type or "PLA"),
                brand=p.vendor,
                color_hex=p.colour,
                price_per_kg=0.0,
                density_g_cm3=density_for(p.material_type),
                active=True,
                auto_created=True,
            )
            session.add(material)
            session.flush()
            por_nombre[_norm(material.name)] = material
            resumen["materiales_nuevos"] += 1
        else:
            # Solo se rellenan huecos: lo que ya tenga puesto a mano se respeta,
            # sobre todo el precio, que Orca no conoce.
            if not material.brand and p.vendor:
                material.brand = p.vendor
            if not material.color_hex and p.colour:
                material.color_hex = p.colour
            resumen["materiales_actualizados"] += 1

        clave = (material.id, printer_id, nozzle)
        cal = por_clave.get(clave)
        if cal is None:
            cal = FilamentCalibration(
                material_id=material.id, printer_id=printer_id, nozzle_mm=nozzle
            )
            session.add(cal)
            session.flush()          # asigna id y fija la fila para esta terna
            por_clave[clave] = cal
            resumen["calibraciones_nuevas"] += 1
        else:
            resumen["calibraciones_actualizadas"] += 1

        cal.status = p.calibration_level
        cal.orca_profile = p.name
        cal.flow_ratio = p.flow_ratio
        cal.pressure_advance = p.pressure_advance
        cal.nozzle_temp = p.nozzle_temp
        cal.bed_temp = p.bed_temp
        cal.source = "orca"      # manda sobre lo deducido del historial
        cal.updated_at = ahora

    session.flush()
    return resumen
