"""Deduce del historial qué filamento está calibrado en qué impresora.

OrcaSlicer nombra los perfiles específicos de una máquina con un sufijo
``@impresora`` (p.ej. ``Elegoo PLA White @Voron 0.6``). Ese sufijo es, de hecho,
la declaración de "esto está calibrado para esta máquina": si un perfil no fuera
específico, no lo llevaría.

Como el sufijo se teclea a mano, el vocabulario se fragmenta ("@Hi 0.4 Nozzle",
"@HI 0.4 noozle", "@Creality Hi 0.4"...). Por eso cada impresora guarda una
lista de alias, que se puede derivar del propio historial: los sufijos que
aparecen mayoritariamente en jobs de esa máquina son suyos.

Con eso se distinguen tres situaciones:
  * el perfil es de la impresora que imprimió  → calibrado
  * el perfil es de OTRA impresora             → se imprimió descalibrado
  * el perfil no tiene sufijo                  → genérico, estado desconocido
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

# Un sufijo se considera alias de una impresora si al menos esta fracción de las
# veces que aparece fue en esa máquina. Por debajo, es un perfil prestado.
ALIAS_MIN_SHARE = 0.6
# Y hace falta un mínimo de apariciones para no crear alias con un solo job.
ALIAS_MIN_JOBS = 2

_NOT_TUNED = re.compile(r"not\s*tuned", re.IGNORECASE)


def profile_name(filament_name) -> str:
    """Nombre del perfil tal cual lo escribió el laminador, sin multi-material."""
    if not filament_name:
        return ""
    return str(filament_name).split(";")[0].strip().strip('"').strip()


def split_profile(filament_name) -> tuple[str, str | None]:
    """Parte un perfil en (filamento, sufijo de impresora).

    ``"Elegoo PLA White @Voron 0.6"`` → ``("Elegoo PLA White", "Voron 0.6")``.
    Se acepta con y sin espacio tras la arroba: ambas formas aparecen en los
    perfiles reales.
    """
    full = profile_name(filament_name)
    if not full:
        return "", None
    m = re.search(r"\s*@\s*(.+)$", full)
    if not m:
        return full, None
    return full[: m.start()].strip(), m.group(1).strip()


def is_not_tuned(filament_name) -> bool:
    """True si el propio nombre declara que el perfil no está calibrado."""
    return bool(_NOT_TUNED.search(profile_name(filament_name)))


def normalize_alias(suffix: str | None) -> str:
    """Forma canónica de un sufijo, para que las variantes de tecleo casen.

    Unifica mayúsculas, espacios y las erratas habituales de "nozzle", que en
    los perfiles reales aparece como noozle, Noozle, Nozzlle...
    """
    if not suffix:
        return ""
    s = suffix.lower().strip()
    s = re.sub(r"n[o]+z+l+e", "nozzle", s)   # noozle, nozzlle, noozzle...
    s = re.sub(r"[\s_-]+", " ", s)
    return s.strip()


@dataclass
class AliasProposal:
    """Sufijos candidatos a ser alias de una impresora, con su evidencia."""

    printer_id: int
    printer_name: str
    aliases: list[str] = field(default_factory=list)
    # Sufijos vistos aquí que pertenecen claramente a otra máquina.
    foreign: Counter = field(default_factory=Counter)
    # Sufijos sin evidencia suficiente para decidir. No son un error: es que
    # aparecen demasiado poco. Van aparte para no acusar en falso.
    unclear: Counter = field(default_factory=Counter)


def propose_aliases(observations) -> dict[int, AliasProposal]:
    """Reparte los sufijos observados entre las impresoras que los usaron.

    ``observations`` es un iterable de ``(printer_id, printer_name, sufijo)``.
    Un sufijo con una sola impresora candidata es suyo; si varias se lo
    disputan, gana la que predomina y en las demás queda marcado como prestado.
    Lo que no llega a evidencia suficiente se aparta en ``unclear``.
    """
    por_sufijo: dict[str, Counter] = defaultdict(Counter)
    nombres: dict[int, str] = {}
    for printer_id, printer_name, suffix in observations:
        nombres[printer_id] = printer_name
        # Un perfil que se declara sin calibrar no define el vocabulario de la
        # máquina: describe justo lo contrario.
        if is_not_tuned(suffix):
            continue
        alias = normalize_alias(suffix)
        if alias:
            por_sufijo[alias][printer_id] += 1

    props = {
        pid: AliasProposal(printer_id=pid, printer_name=name)
        for pid, name in nombres.items()
    }
    for alias, cuenta in por_sufijo.items():
        total = sum(cuenta.values())
        duenyo, n = cuenta.most_common(1)[0]

        if len(cuenta) == 1:
            # Nadie más lo usa: no hay conflicto que resolver, es suyo.
            props[duenyo].aliases.append(alias)
            continue

        if n >= ALIAS_MIN_JOBS and n / total >= ALIAS_MIN_SHARE:
            props[duenyo].aliases.append(alias)
            for pid, veces in cuenta.items():
                if pid != duenyo:
                    props[pid].foreign[alias] += veces
        else:
            # Disputado sin ganador claro: no se decide por nadie.
            for pid, veces in cuenta.items():
                props[pid].unclear[alias] += veces

    for p in props.values():
        p.aliases.sort()
    return props


def classify(filament_name, printer_aliases) -> str:
    """Estado de calibración de un job según su perfil y los alias de la máquina.

    Devuelve una de las constantes ``CAL_*`` de ``models``.
    """
    from ..models import CAL_CALIBRATED, CAL_NOT_TUNED, CAL_UNKNOWN

    if is_not_tuned(filament_name):
        return CAL_NOT_TUNED
    _, suffix = split_profile(filament_name)
    if not suffix:
        return CAL_UNKNOWN
    alias = normalize_alias(suffix)
    return CAL_CALIBRATED if alias in set(printer_aliases or []) else CAL_UNKNOWN


def rescan_from_history(session, apply: bool = True) -> dict:
    """Reconstruye alias y matriz de calibración a partir del historial.

    Todo lo de arriba en este módulo es puro y se testea sin base de datos;
    esta es la única función que toca el ORM, y solo orquesta.
    """
    from sqlalchemy import select

    from ..models import (
        CAL_CALIBRATED,
        CAL_NOT_TUNED,
        FilamentCalibration,
        PrintJob,
        Printer,
    )

    printers = {p.id: p for p in session.scalars(select(Printer)).all()}
    filas = []
    for job in session.scalars(select(PrintJob)).all():
        meta = job.raw_metadata or {}
        nombre = profile_name(meta.get("filament_name"))
        if not nombre or job.printer_id not in printers:
            continue
        try:
            nozzle = round(float(meta.get("nozzle_diameter") or 0.4), 2)
        except (TypeError, ValueError):
            nozzle = 0.4
        filas.append((job, nombre, nozzle))

    obs = []
    for job, nombre, _ in filas:
        _, sufijo = split_profile(nombre)
        if sufijo:
            obs.append((job.printer_id, printers[job.printer_id].name, sufijo))
    props = propose_aliases(obs)

    if apply:
        for pid, pr in props.items():
            printers[pid].orca_aliases = pr.aliases

    ajenos = 0
    agg: dict[tuple, dict] = defaultdict(
        lambda: {"jobs": 0, "last": None, "estados": Counter(), "perfil": None}
    )
    for job, nombre, nozzle in filas:
        alias = props[job.printer_id].aliases if job.printer_id in props else []
        if is_foreign_profile(nombre, alias):
            ajenos += 1
        if job.material_id is None:
            continue
        e = agg[(job.material_id, job.printer_id, nozzle)]
        e["jobs"] += 1
        e["estados"][classify(nombre, alias)] += 1
        if job.end_time and (e["last"] is None or job.end_time > e["last"]):
            e["last"] = job.end_time
            e["perfil"] = nombre

    existentes = {
        (c.material_id, c.printer_id, c.nozzle_mm): c
        for c in session.scalars(select(FilamentCalibration)).all()
    }
    nuevas = actualizadas = 0
    for clave, e in agg.items():
        # El mejor estado visto manda: si alguna vez se imprimió con el perfil
        # propio, la combinación está calibrada aunque otras veces no lo fuera.
        if e["estados"][CAL_CALIBRATED]:
            estado = CAL_CALIBRATED
        elif e["estados"][CAL_NOT_TUNED]:
            estado = CAL_NOT_TUNED
        else:
            estado = "UNKNOWN"

        cal = existentes.get(clave)
        if cal is None:
            cal = FilamentCalibration(
                material_id=clave[0], printer_id=clave[1], nozzle_mm=clave[2]
            )
            if apply:
                session.add(cal)
            nuevas += 1
        else:
            actualizadas += 1
        # Lo importado de Orca es más fiable: no se pisa con deducciones.
        if cal.source != "orca":
            cal.status = estado
            cal.orca_profile = e["perfil"]
            cal.source = "history"
        cal.jobs_seen = e["jobs"]
        cal.last_job_at = e["last"]

    if apply:
        session.flush()
    return {
        "aliases": {p.printer_name: p.aliases for p in props.values()},
        "foreign": {p.printer_name: dict(p.foreign) for p in props.values()},
        "unclear": {p.printer_name: dict(p.unclear) for p in props.values()},
        "combinaciones": len(agg),
        "nuevas": nuevas,
        "actualizadas": actualizadas,
        "jobs_con_perfil_ajeno": ajenos,
    }


def is_foreign_profile(filament_name, printer_aliases) -> bool:
    """True si el perfil está calibrado, pero para OTRA impresora.

    Es el caso que interesa avisar: el perfil declara una máquina concreta y no
    es esta, así que se imprimiría con parámetros de otra.
    """
    _, suffix = split_profile(filament_name)
    if not suffix or is_not_tuned(filament_name):
        return False
    return normalize_alias(suffix) not in set(printer_aliases or [])
