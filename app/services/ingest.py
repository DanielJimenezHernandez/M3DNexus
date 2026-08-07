"""Ingesta: convierte un job de Moonraker en un registro con coste.

Flujo por job terminado:
  1. Normaliza los campos del job.
  2. Resuelve el peso de filamento (metadatos del slicer o, si faltan,
     se calcula desde la longitud y la densidad del material).
  3. Resuelve el material (por tipo) para conocer su precio.
  4. Consulta a HA la energía consumida en la ventana [start, end].
  5. Calcula el desglose de coste y persiste (upsert por job_id).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cost import (
    DEFAULT_FILAMENT_DENSITY,
    CostInputs,
    compute_cost,
    mm_to_grams,
)
from ..integrations.homeassistant import HomeAssistantClient
from ..integrations.moonraker import MoonrakerJob
from ..models import (
    SETTING_AUTOCREATE_SINCE,
    SETTING_CURRENCY,
    SETTING_ELECTRICITY_PRICE,
    Material,
    PrintJob,
    Printer,
    Setting,
)
from .filament import resolve_or_create_material

log = logging.getLogger(__name__)

# Estados de Moonraker que consideramos "terminados" (computables).
FINISHED_STATES = {"completed", "cancelled", "error", "klippy_shutdown", "interrupted"}


def get_settings(session: Session) -> tuple[float, str]:
    """Devuelve (precio €/kWh, moneda) desde la tabla de ajustes."""
    rows = {s.key: s.value for s in session.scalars(select(Setting)).all()}
    price = float(rows.get(SETTING_ELECTRICITY_PRICE, "0") or 0)
    currency = rows.get(SETTING_CURRENCY, "€") or "€"
    return price, currency


def get_autocreate_since(session: Session) -> datetime | None:
    """Momento desde el que se auto-crean filamentos (None si no está fijado)."""
    row = session.get(Setting, SETTING_AUTOCREATE_SINCE)
    if not row or not row.value:
        return None
    try:
        return datetime.fromisoformat(row.value)
    except ValueError:
        return None


def _filament_weight(job: MoonrakerJob, material: Material | None) -> float:
    """Peso (g) del filamento realmente extruido en la impresión.

    El slicer da el peso de la pieza **completa** (``filament_weight_total``),
    que solo vale tal cual si el job llegó al final. Si se canceló a medias hay
    que escalarlo por la fracción que llegó a extruirse (mm reales / mm
    planificados); si no, un fallo a los cinco minutos cobraría la pieza entera.

    Solo se escalan los jobs no completados: en los completados el ratio ya es
    ~1 y algunos lo superan (gcode re-laminado tras imprimirlo), lo que
    inflaría el peso en vez de corregirlo.
    """
    density = material.density_g_cm3 if material else DEFAULT_FILAMENT_DENSITY
    weight_total = job.filament_weight_g or 0.0

    # Sin peso del slicer: se estima desde la longitud realmente extruida.
    if weight_total <= 0:
        return round(mm_to_grams(job.filament_used_mm, density_g_cm3=density), 3)

    if job.status == "completed":
        return weight_total

    planned_mm = float((job.metadata or {}).get("filament_total") or 0.0)
    if planned_mm <= 0:  # sin referencia para escalar: mejor la estimación
        return round(mm_to_grams(job.filament_used_mm, density_g_cm3=density), 3)

    # Acotado a [0, 1]: la retracción inicial puede dar mm negativos.
    fraction = min(max(job.filament_used_mm / planned_mm, 0.0), 1.0)
    return round(weight_total * fraction, 3)


def _is_settled(record: PrintJob) -> bool:
    """True si ya no queda nada que aprender de este registro.

    Un job se reprocesa mientras pueda mejorar: falta la energía y todavía es
    recuperable, o falta el material (que puede aparecer si lo creas a mano).
    Sin este corte, cada sondeo relanza una consulta a HA por cada job del
    historial —cientos por minuto— sin converger nunca.
    """
    energy_done = record.energy_kwh is not None or record.energy_unavailable
    return energy_done and record.material_id is not None


def _resolve_energy(
    record: PrintJob,
    printer: Printer,
    job: MoonrakerJob,
    ha: HomeAssistantClient | None,
    padding_s: int,
    retention_cutoff: datetime,
) -> None:
    """Rellena la energía del registro, o la marca como irrecuperable.

    La distinción importa: sin ella no se puede saber si merece la pena volver a
    preguntar. Solo se reintenta lo que de verdad puede aparecer más tarde.
    """
    if record.energy_kwh is not None or record.energy_unavailable:
        return  # ya resuelto, en un sentido o en otro
    if not printer.ha_energy_entity or not job.start_time or not job.end_time:
        return  # sin sensor configurado: no es irrecuperable, es que falta config

    if job.end_time < retention_cutoff:
        # Fuera de la retención del recorder: HA ya purgó esos estados y no los
        # va a recuperar. Preguntar sería una llamada perdida en cada sondeo.
        record.energy_unavailable = True
        return
    if ha is None:
        return  # HA no responde ahora mismo: se reintenta en el próximo sondeo

    start = job.start_time - timedelta(seconds=padding_s)
    end = job.end_time + timedelta(seconds=padding_s)
    energy = ha.energy_between(printer.ha_energy_entity, start, end)
    if energy is None:
        # HA contestó, pero su ventana no tiene datos usables (enchufe caído
        # durante la impresión, contador reiniciado). Eso ya no cambia.
        record.energy_unavailable = True
        log.info(
            "Job %s de %s sin energía en HA: marcado como irrecuperable.",
            job.job_id,
            printer.name,
        )
    else:
        record.energy_kwh = energy


def _borrow_energy(
    session: Session,
    record: PrintJob,
    printer: Printer,
    material: Material | None,
) -> None:
    """Estima la energía tomando la potencia media de otra impresora.

    Para máquinas sin sensor propio (``ha_energy_entity`` vacío) pero con una
    ``power_ref_printer_id``: se estima energía = potencia_media(referencia) ×
    duración, y se marca el job como ``energy_estimated`` para no confundirlo con
    una medida real. Solo actúa si aún no hay energía (medida o estimada).
    """
    # No se toca la energía MEDIDA; una estimación previa sí puede refrescarse
    # (p.ej. si luego se resuelve el material y cambia la potencia por tipo).
    if record.energy_kwh is not None and not record.energy_estimated:
        return
    if printer.ha_energy_entity or not printer.power_ref_printer_id:
        return  # tiene sensor propio, o no referencia a nadie
    duration_h = (record.print_duration_s or 0.0) / 3600.0
    if duration_h <= 0:
        # Job de 0 s (cancelado al instante): no hay nada que estimar. Se marca
        # resuelto para que no se reprocese en cada sondeo.
        if record.energy_kwh is None:
            record.energy_unavailable = True
        return

    from .estimate import avg_power_w  # perezoso: estimate importa de este módulo

    mtype = material.material_type if material else None
    power_w, _ = avg_power_w(session, printer.power_ref_printer_id, mtype)
    record.energy_kwh = round(power_w / 1000.0 * duration_h, 4)
    record.energy_estimated = True
    # Estimada, pero ya no es irrecuperable: hay un valor con el que costear.
    record.energy_unavailable = False


def apply_costs(
    record: PrintJob,
    printer: Printer,
    material: Material | None,
    price_per_kwh: float,
    currency: str,
) -> None:
    """Rellena los campos de coste del registro a partir de su estado actual.

    Reutilizable: lo usa tanto la ingesta como el recálculo manual desde la UI.
    """
    duration_h = (record.print_duration_s or 0.0) / 3600.0
    filament_price = material.price_per_kg if material else 0.0

    breakdown = compute_cost(
        CostInputs(
            energy_kwh=record.energy_kwh or 0.0,
            electricity_price_per_kwh=price_per_kwh,
            filament_weight_g=record.filament_weight_g or 0.0,
            filament_price_per_kg=filament_price,
            print_duration_hours=duration_h,
            machine_purchase_price=printer.purchase_price or 0.0,
            machine_resale_value=printer.resale_value or 0.0,
            machine_lifetime_hours=printer.lifetime_hours,
            maintenance_per_hour=printer.maintenance_per_hour or 0.0,
        )
    )

    record.tariff_per_kwh = price_per_kwh
    record.currency = currency
    record.cost_energy = breakdown.energy
    record.cost_filament = breakdown.filament
    record.cost_depreciation = breakdown.depreciation
    record.cost_maintenance = breakdown.maintenance
    record.cost_total = breakdown.total
    record.material_id = material.id if material else record.material_id
    # A revisar si falta energía, falta material, o el material no tiene precio
    # (típico de filamentos recién auto-creados, hasta que pongas su precio/kg).
    no_price = material is not None and (material.price_per_kg or 0) <= 0
    record.needs_review = (record.energy_kwh is None) or (material is None) or no_price
    record.computed_at = datetime.now(timezone.utc)


def ingest_job(
    session: Session,
    printer: Printer,
    job: MoonrakerJob,
    ha: HomeAssistantClient | None,
    price_per_kwh: float,
    currency: str,
    padding_s: int = 0,
    autocreate_since: datetime | None = None,
    retention_days: int = 10,
) -> PrintJob | None:
    """Inserta o actualiza un job y calcula su coste. None si se ignora."""
    if job.status not in FINISHED_STATES or job.end_time is None:
        return None  # en curso: aún no computable

    # La misma impresión se identifica por (impresora, job_id, inicio). El inicio
    # distingue un job_id reutilizado tras reiniciar el historial de Moonraker.
    record = session.scalar(
        select(PrintJob).where(
            PrintJob.printer_id == printer.id,
            PrintJob.moonraker_job_id == job.job_id,
            PrintJob.start_time == job.start_time,
        )
    )

    # Auto-crear el filamento de impresiones terminadas a partir de la marca
    # (así el back-catálogo, anterior a la marca, no llena la BD de materiales).
    # NO se exige que el registro sea nuevo: si el historial de Moonraker se
    # reinició, una impresión reciente puede caer sobre un registro viejo con el
    # mismo job_id, y aun así su filamento debe resolverse/crearse bien, no caer
    # al genérico por tipo.
    allow_create = (
        autocreate_since is not None
        and job.end_time is not None
        and job.end_time >= autocreate_since
    )
    material = resolve_or_create_material(
        session, job.metadata, job.filament_type, allow_create
    )

    # Ya resuelto y con energía: no rehacemos trabajo (ni, sobre todo, llamadas a
    # HA). Excepción: si ahora se resuelve un material CONCRETO distinto del
    # guardado —típico de un registro que se quedó en genérico— se reprocesa para
    # corregirlo; la energía ya resuelta no se vuelve a pedir. Cuando no hay
    # material que resolver (metadatos sin nombre) no se fuerza nada, para no
    # pisar una asignación hecha a mano.
    material_ok = material is None or record and record.material_id == material.id
    if record is not None and _is_settled(record) and material_ok:
        return record

    if record is None:
        record = PrintJob(
            printer_id=printer.id, moonraker_job_id=job.job_id
        )
        session.add(record)

    record.filename = job.filename
    record.status = job.status
    record.start_time = job.start_time
    record.end_time = job.end_time
    record.print_duration_s = job.print_duration_s
    record.total_duration_s = job.total_duration_s
    record.filament_used_mm = job.filament_used_mm
    record.filament_weight_g = _filament_weight(job, material)
    record.raw_metadata = job.metadata

    # Energía desde HA en la ventana de la impresión (con margen opcional).
    retention_cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    _resolve_energy(record, printer, job, ha, padding_s, retention_cutoff)
    # Si esta impresora no mide energía pero referencia a otra, se estima con la
    # potencia media de esa (misma clase de máquina). Mejor que cobrar $0.
    _borrow_energy(session, record, printer, material)

    apply_costs(record, printer, material, price_per_kwh, currency)
    session.flush()
    return record
