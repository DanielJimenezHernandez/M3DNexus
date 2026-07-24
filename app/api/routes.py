"""Rutas de la API REST."""

from __future__ import annotations

import base64
import binascii
import csv
import io
import re

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..config import get_config
from ..db import get_session
from ..integrations.homeassistant import HomeAssistantClient
from ..integrations.moonraker import MoonrakerClient
from ..models import (
    FilamentCalibration,
    MaterialPhoto,
    PHOTO_COLOR,
    PHOTO_LIMITS,
    PHOTO_MAX_BYTES,
    PHOTO_SPOOL,
    SETTING_COMPANY_INFO,
    SETTING_COMPANY_LOGO,
    SETTING_COMPANY_NAME,
    SETTING_CURRENCY,
    SETTING_ELECTRICITY_PRICE,
    SETTING_PAYMENT_INFO,
    SETTING_QUOTE_TERMS,
    Material,
    PrintJob,
    Printer,
    Setting,
)
from ..schemas import (
    AssignMaterialIn,
    JobOut,
    MaterialIn,
    MaterialOut,
    PrinterIn,
    PrinterOut,
    SettingsIn,
    SettingsOut,
    StatsOut,
)
from ..services.estimate import avg_power_w, estimate_cost, estimate_from_history
from ..services.calibration import rescan_from_history
from ..services.ingest import apply_costs, get_settings
from ..services.loaded import resolve_slots, set_slots
from ..services.orca_import import apply_import, plan_import
from ..services.sync import sync_all, sync_printer

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- #
# Impresoras
# --------------------------------------------------------------------------- #
@router.get("/printers", response_model=list[PrinterOut])
def list_printers(db: Session = Depends(get_session)):
    return db.scalars(select(Printer).order_by(Printer.name)).all()


@router.post("/printers", response_model=PrinterOut, status_code=201)
def create_printer(data: PrinterIn, db: Session = Depends(get_session)):
    printer = Printer(**data.model_dump())
    db.add(printer)
    db.commit()
    db.refresh(printer)
    return printer


@router.put("/printers/{printer_id}", response_model=PrinterOut)
def update_printer(printer_id: int, data: PrinterIn, db: Session = Depends(get_session)):
    printer = db.get(Printer, printer_id)
    if not printer:
        raise HTTPException(404, "Impresora no encontrada")
    for k, v in data.model_dump().items():
        setattr(printer, k, v)
    db.commit()
    db.refresh(printer)
    return printer


@router.delete("/printers/{printer_id}", status_code=204)
def delete_printer(printer_id: int, db: Session = Depends(get_session)):
    printer = db.get(Printer, printer_id)
    if not printer:
        raise HTTPException(404, "Impresora no encontrada")
    db.delete(printer)
    db.commit()


@router.get("/printers/loaded")
def printers_loaded(db: Session = Depends(get_session)):
    """Filamento cargado ahora en cada impresora (manual o del último impreso)."""
    return {
        p.id: resolve_slots(db, p)
        for p in db.scalars(select(Printer).order_by(Printer.name)).all()
    }


@router.put("/printers/{printer_id}/loaded")
def set_printer_loaded(
    printer_id: int, payload: dict = Body(...), db: Session = Depends(get_session)
):
    """Fija a mano el material de cada hueco. ``materials``: lista de id o null."""
    printer = db.get(Printer, printer_id)
    if not printer:
        raise HTTPException(404, "Impresora no encontrada")
    mats = payload.get("materials")
    if not isinstance(mats, list):
        raise HTTPException(422, "Se esperaba una lista en 'materials'")
    for mid in mats:
        if mid is not None and not db.get(Material, mid):
            raise HTTPException(404, f"Material {mid} no encontrado")
    set_slots(printer, mats)
    db.commit()
    return resolve_slots(db, printer)


@router.get("/printers/{printer_id}/files")
def list_printer_files(printer_id: int, db: Session = Depends(get_session)):
    """Gcodes disponibles en la impresora (para estimar antes de imprimir)."""
    printer = db.get(Printer, printer_id)
    if not printer:
        raise HTTPException(404, "Impresora no encontrada")
    try:
        files = MoonrakerClient(printer.moonraker_url).list_files()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"No se pudo leer la impresora: {exc}")
    return [
        {"path": f.get("path"), "size": f.get("size"), "modified": f.get("modified")}
        for f in files
    ]


@router.get("/printers/{printer_id}/quote-defaults")
def quote_defaults(
    printer_id: int,
    material_type: str | None = None,
    db: Session = Depends(get_session),
):
    """Valores de la impresora para rellenar una cotización manual.

    Si se indica ``material_type``, el consumo medio es el de esa combinación
    (impresora × tipo) con fallback; si no, la media global de la impresora.
    """
    p = db.get(Printer, printer_id)
    if not p:
        raise HTTPException(404, "Impresora no encontrada")
    price, currency = get_settings(db)
    power_w, source = avg_power_w(db, printer_id, material_type)
    return {
        "currency": currency,
        "electricity_price_per_kwh": price,
        "avg_power_kw": round(power_w / 1000.0, 3),
        "avg_power_source": source,
        "depreciation_per_h": round(p.amortization_per_hour, 4),
        "failure_rate_pct": 10.0,
    }


@router.get("/printers/{printer_id}/history-files")
def list_history_files(printer_id: int, db: Session = Depends(get_session)):
    """Gcodes ya impresos en esta impresora (desde la BD, funciona offline)."""
    if not db.get(Printer, printer_id):
        raise HTTPException(404, "Impresora no encontrada")
    rows = db.execute(
        select(PrintJob.filename, func.max(PrintJob.end_time))
        .where(PrintJob.printer_id == printer_id, PrintJob.filename.is_not(None))
        .group_by(PrintJob.filename)
        .order_by(func.max(PrintJob.end_time).desc().nulls_last())
    ).all()
    return [{"path": r[0]} for r in rows]


@router.get("/printers/{printer_id}/estimate")
def estimate_print(
    printer_id: int,
    filename: str,
    source: str = "history",
    db: Session = Depends(get_session),
):
    """Estima el coste de un gcode. ``source``: 'history' (BD) o 'live' (Moonraker)."""
    printer = db.get(Printer, printer_id)
    if not printer:
        raise HTTPException(404, "Impresora no encontrada")
    try:
        if source == "live":
            return estimate_cost(db, printer, filename)
        result = estimate_from_history(db, printer, filename)
        if not result.get("has_data"):
            raise HTTPException(404, "Sin metadatos en el historial para ese archivo")
        return result
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"No se pudo estimar: {exc}")


# --------------------------------------------------------------------------- #
# Materiales
# --------------------------------------------------------------------------- #
@router.get("/materials", response_model=list[MaterialOut])
def list_materials(db: Session = Depends(get_session)):
    return db.scalars(select(Material).order_by(Material.name)).all()


@router.post("/materials", response_model=MaterialOut, status_code=201)
def create_material(data: MaterialIn, db: Session = Depends(get_session)):
    material = Material(**data.model_dump())
    db.add(material)
    db.commit()
    db.refresh(material)
    return material


def _recompute_material_jobs(db: Session, material: Material) -> int:
    """Rehace el coste de las impresiones que usan este material."""
    price, currency = get_settings(db)
    printers = {p.id: p for p in db.scalars(select(Printer)).all()}
    jobs = db.scalars(
        select(PrintJob).where(PrintJob.material_id == material.id)
    ).all()
    for job in jobs:
        printer = printers.get(job.printer_id)
        if printer:
            apply_costs(job, printer, material, price, currency)
    return len(jobs)


@router.put("/materials/{material_id}", response_model=MaterialOut)
def update_material(material_id: int, data: MaterialIn, db: Session = Depends(get_session)):
    material = db.get(Material, material_id)
    if not material:
        raise HTTPException(404, "Material no encontrado")
    old_price = material.price_per_kg
    for k, v in data.model_dump().items():
        setattr(material, k, v)
    # El precio/kg entra en el coste ya calculado de cada impresión, así que al
    # cambiarlo hay que rehacer las suyas. Antes ocurría de rebote, porque el
    # sondeo reprocesaba en bucle todo lo que estuviera "a revisar".
    if material.price_per_kg != old_price:
        _recompute_material_jobs(db, material)
    db.commit()
    db.refresh(material)
    return material


@router.delete("/materials/{material_id}", status_code=204)
def delete_material(material_id: int, db: Session = Depends(get_session)):
    material = db.get(Material, material_id)
    if not material:
        raise HTTPException(404, "Material no encontrado")

    # SQLite no fuerza claves foráneas: sin esto, las impresiones se quedan
    # apuntando a un material que ya no existe y conservan un coste calculado
    # con su precio, invisible en los informes porque los JOIN las descartan.
    price, currency = get_settings(db)
    printers = {p.id: p for p in db.scalars(select(Printer)).all()}
    for job in db.scalars(
        select(PrintJob).where(PrintJob.material_id == material_id)
    ).all():
        job.material_id = None
        printer = printers.get(job.printer_id)
        if printer:
            apply_costs(job, printer, None, price, currency)

    for cal in db.scalars(
        select(FilamentCalibration).where(
            FilamentCalibration.material_id == material_id
        )
    ).all():
        db.delete(cal)

    db.delete(material)   # cascade borra sus fotos (relationship)
    db.commit()


# --------------------------------------------------------------------------- #
# Fotos de referencia del filamento
# --------------------------------------------------------------------------- #
def _decode_data_uri(data_uri: str) -> tuple[bytes, str]:
    """Convierte un data URI de imagen en (bytes, content_type). Valida la forma."""
    m = re.match(r"^data:(image/(?:png|jpeg|webp));base64,(.+)$", data_uri or "", re.S)
    if not m:
        raise HTTPException(422, "Se esperaba una imagen en data URI (png/jpeg/webp)")
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(422, "Base64 inválido")
    if not raw:
        raise HTTPException(422, "Imagen vacía")
    if len(raw) > PHOTO_MAX_BYTES:
        raise HTTPException(413, "La imagen supera el tamaño máximo")
    return raw, m.group(1)


@router.get("/materials/{material_id}/photos")
def list_photos(material_id: int, db: Session = Depends(get_session)):
    """Fotos de un material: id, tipo y URL para pintarlas (sin el binario)."""
    if not db.get(Material, material_id):
        raise HTTPException(404, "Material no encontrado")
    fotos = db.scalars(
        select(MaterialPhoto)
        .where(MaterialPhoto.material_id == material_id)
        .order_by(MaterialPhoto.kind, MaterialPhoto.id)
    ).all()
    return [
        {"id": f.id, "kind": f.kind,
         "url": f"/api/materials/{material_id}/photos/{f.id}"}
        for f in fotos
    ]


@router.get("/materials/{material_id}/photos/{photo_id}")
def get_photo(material_id: int, photo_id: int, db: Session = Depends(get_session)):
    """Sirve el binario de una foto."""
    foto = db.get(MaterialPhoto, photo_id)
    if not foto or foto.material_id != material_id:
        raise HTTPException(404, "Foto no encontrada")
    return Response(
        content=foto.data,
        media_type=foto.content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.post("/materials/{material_id}/photos", status_code=201)
def add_photo(material_id: int, payload: dict = Body(...), db: Session = Depends(get_session)):
    """Añade una foto (``kind``: spool o color) desde un data URI."""
    if not db.get(Material, material_id):
        raise HTTPException(404, "Material no encontrado")
    kind = payload.get("kind", PHOTO_SPOOL)
    if kind not in PHOTO_LIMITS:
        raise HTTPException(422, f"Tipo de foto inválido: {kind}")

    actuales = db.scalar(
        select(func.count(MaterialPhoto.id)).where(
            MaterialPhoto.material_id == material_id, MaterialPhoto.kind == kind
        )
    )
    if actuales >= PHOTO_LIMITS[kind]:
        raise HTTPException(
            409, f"Ya hay {PHOTO_LIMITS[kind]} foto(s) de tipo {kind}; borra una antes"
        )

    raw, ctype = _decode_data_uri(payload.get("data_uri", ""))
    foto = MaterialPhoto(
        material_id=material_id, kind=kind, data=raw, content_type=ctype
    )
    db.add(foto)
    db.commit()
    db.refresh(foto)
    return {"id": foto.id, "kind": foto.kind,
            "url": f"/api/materials/{material_id}/photos/{foto.id}"}


@router.delete("/materials/{material_id}/photos/{photo_id}", status_code=204)
def delete_photo(material_id: int, photo_id: int, db: Session = Depends(get_session)):
    foto = db.get(MaterialPhoto, photo_id)
    if not foto or foto.material_id != material_id:
        raise HTTPException(404, "Foto no encontrada")
    db.delete(foto)
    db.commit()


# --------------------------------------------------------------------------- #
# Jobs / impresiones
# --------------------------------------------------------------------------- #
@router.get("/jobs", response_model=list[JobOut])
def list_jobs(
    db: Session = Depends(get_session),
    printer_id: int | None = None,
    status: str | None = None,
    needs_review: bool | None = None,
    limit: int = Query(100, le=5000),
    offset: int = 0,
):
    stmt = select(PrintJob).order_by(PrintJob.end_time.desc().nulls_last())
    if printer_id is not None:
        stmt = stmt.where(PrintJob.printer_id == printer_id)
    if status is not None:
        stmt = stmt.where(PrintJob.status == status)
    if needs_review is not None:
        stmt = stmt.where(PrintJob.needs_review.is_(needs_review))
    stmt = stmt.limit(limit).offset(offset)
    return db.scalars(stmt).all()


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_session)):
    job = db.get(PrintJob, job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    return job


@router.get("/jobs.csv")
def export_jobs_csv(db: Session = Depends(get_session)):
    """Exporta el registro completo de impresiones a CSV."""
    rows = db.scalars(
        select(PrintJob).order_by(PrintJob.end_time.desc().nulls_last())
    ).all()
    pmap = {p.id: p.name for p in db.scalars(select(Printer)).all()}
    mmap = {m.id: m.name for m in db.scalars(select(Material)).all()}

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "fin", "impresora", "archivo", "estado", "duracion_h", "filamento_g",
        "material", "energia_kwh", "coste_luz", "coste_filamento",
        "coste_amortizacion", "coste_total", "moneda",
    ])
    for j in rows:
        w.writerow([
            j.end_time.isoformat() if j.end_time else "",
            pmap.get(j.printer_id, ""),
            j.filename or "",
            j.status,
            round((j.print_duration_s or 0) / 3600, 3),
            round(j.filament_weight_g or 0, 1),
            mmap.get(j.material_id, ""),
            j.energy_kwh if j.energy_kwh is not None else "",
            j.cost_energy, j.cost_filament, j.cost_depreciation,
            j.cost_total, j.currency,
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=printcost_jobs.csv"},
    )


def _recompute(db: Session, job: PrintJob) -> None:
    price, currency = get_settings(db)
    printer = db.get(Printer, job.printer_id)
    material = db.get(Material, job.material_id) if job.material_id else None
    apply_costs(job, printer, material, price, currency)


@router.post("/jobs/{job_id}/assign-material", response_model=JobOut)
def assign_material(job_id: int, data: AssignMaterialIn, db: Session = Depends(get_session)):
    job = db.get(PrintJob, job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    if data.material_id is not None and not db.get(Material, data.material_id):
        raise HTTPException(404, "Material no encontrado")
    job.material_id = data.material_id
    _recompute(db, job)
    db.commit()
    db.refresh(job)
    return job


@router.post("/jobs/{job_id}/recompute", response_model=JobOut)
def recompute_job(job_id: int, db: Session = Depends(get_session)):
    job = db.get(PrintJob, job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    _recompute(db, job)
    db.commit()
    db.refresh(job)
    return job


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: int, db: Session = Depends(get_session)):
    job = db.get(PrintJob, job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    db.delete(job)
    db.commit()


# --------------------------------------------------------------------------- #
# Calibración de filamentos
# --------------------------------------------------------------------------- #
@router.get("/calibrations")
def list_calibrations(db: Session = Depends(get_session)):
    """Matriz de qué filamento está calibrado en qué impresora y boquilla."""
    filas = db.execute(
        select(FilamentCalibration, Material, Printer)
        .join(Material, Material.id == FilamentCalibration.material_id)
        .join(Printer, Printer.id == FilamentCalibration.printer_id)
        .order_by(Material.name, Printer.name, FilamentCalibration.nozzle_mm)
    ).all()
    return [
        {
            "id": c.id,
            "material_id": m.id, "material": m.name,
            "material_type": m.material_type, "color_hex": m.color_hex,
            "printer_id": p.id, "printer": p.name,
            "nozzle_mm": c.nozzle_mm,
            "status": c.status,
            "orca_profile": c.orca_profile,
            "pressure_advance": c.pressure_advance,
            "flow_ratio": c.flow_ratio,
            "nozzle_temp": c.nozzle_temp,
            "bed_temp": c.bed_temp,
            "jobs_seen": c.jobs_seen,
            "last_job_at": c.last_job_at,
            "source": c.source,
        }
        for c, m, p in filas
    ]


@router.delete("/calibrations/{cal_id}", status_code=204)
def delete_calibration(cal_id: int, db: Session = Depends(get_session)):
    cal = db.get(FilamentCalibration, cal_id)
    if not cal:
        raise HTTPException(404, "Calibración no encontrada")
    db.delete(cal)
    db.commit()


@router.post("/calibrations/rescan")
def rescan_calibrations(db: Session = Depends(get_session)):
    """Rehace alias y matriz a partir del historial de impresiones."""
    resumen = rescan_from_history(db)
    db.commit()
    return resumen


@router.post("/orca/preview")
def orca_preview(payload: dict = Body(...), db: Session = Depends(get_session)):
    """Propone qué haría con unos perfiles de Orca, sin escribir nada."""
    perfiles = payload.get("profiles") or []
    if not isinstance(perfiles, list):
        raise HTTPException(422, "Se esperaba una lista en 'profiles'")
    return {"plan": plan_import(db, perfiles)}


@router.post("/orca/import")
def orca_import(payload: dict = Body(...), db: Session = Depends(get_session)):
    """Aplica las filas confirmadas del plan de importación."""
    decisiones = payload.get("decisions") or []
    if not isinstance(decisiones, list):
        raise HTTPException(422, "Se esperaba una lista en 'decisions'")
    resumen = apply_import(db, decisiones)
    db.commit()
    return resumen


# --------------------------------------------------------------------------- #
# Ajustes
# --------------------------------------------------------------------------- #
def _get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row and row.value is not None else default


def _settings_out(db: Session) -> SettingsOut:
    price, currency = get_settings(db)
    return SettingsOut(
        electricity_price_per_kwh=price,
        currency=currency,
        company_name=_get_setting(db, SETTING_COMPANY_NAME),
        company_logo=_get_setting(db, SETTING_COMPANY_LOGO),
        company_info=_get_setting(db, SETTING_COMPANY_INFO),
        payment_info=_get_setting(db, SETTING_PAYMENT_INFO),
        quote_terms=_get_setting(db, SETTING_QUOTE_TERMS),
    )


@router.get("/settings", response_model=SettingsOut)
def read_settings(db: Session = Depends(get_session)):
    return _settings_out(db)


def _set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


@router.put("/settings", response_model=SettingsOut)
def update_settings(data: SettingsIn, db: Session = Depends(get_session)):
    if data.electricity_price_per_kwh is not None:
        _set_setting(db, SETTING_ELECTRICITY_PRICE, str(data.electricity_price_per_kwh))
    if data.currency is not None:
        _set_setting(db, SETTING_CURRENCY, data.currency)
    if data.company_name is not None:
        _set_setting(db, SETTING_COMPANY_NAME, data.company_name)
    if data.company_logo is not None:
        _set_setting(db, SETTING_COMPANY_LOGO, data.company_logo)
    if data.company_info is not None:
        _set_setting(db, SETTING_COMPANY_INFO, data.company_info)
    if data.payment_info is not None:
        _set_setting(db, SETTING_PAYMENT_INFO, data.payment_info)
    if data.quote_terms is not None:
        _set_setting(db, SETTING_QUOTE_TERMS, data.quote_terms)
    db.commit()
    return _settings_out(db)


# --------------------------------------------------------------------------- #
# Sincronización y diagnóstico
# --------------------------------------------------------------------------- #
@router.get("/live")
def live():
    """Impresiones en curso con su coste acumulado en tiempo real."""
    from ..services.live import tracker

    return [
        {k: v for k, v in s.items() if not k.startswith("_")}
        for s in tracker.snapshot()
    ]


@router.get("/thumbnail/{printer_id}")
def thumbnail(printer_id: int, path: str, db: Session = Depends(get_session)):
    """Proxy de una miniatura del gcode desde Moonraker (evita CORS)."""
    printer = db.get(Printer, printer_id)
    if not printer:
        raise HTTPException(404, "Impresora no encontrada")
    data, ctype = MoonrakerClient(printer.moonraker_url).thumbnail(path)
    if not data:
        raise HTTPException(404, "Miniatura no disponible")
    return Response(
        content=data,
        media_type=ctype,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post("/sync")
def trigger_sync(printer_id: int | None = None):
    if printer_id is not None:
        return sync_printer(printer_id)
    return {"results": sync_all()}


@router.get("/health")
def health(db: Session = Depends(get_session)):
    cfg = get_config()
    ha_ok = None
    if cfg.ha_token:
        ha_ok = HomeAssistantClient(cfg.ha_url, cfg.ha_token).ping()
    printers = []
    for p in db.scalars(select(Printer)).all():
        printers.append(
            {"id": p.id, "name": p.name, "online": MoonrakerClient(p.moonraker_url).ping()}
        )
    return {"home_assistant": ha_ok, "printers": printers}


# --------------------------------------------------------------------------- #
# Estadísticas
# --------------------------------------------------------------------------- #
@router.get("/stats", response_model=StatsOut)
def stats(db: Session = Depends(get_session)):
    price, currency = get_settings(db)

    totals = db.execute(
        select(
            func.count(PrintJob.id),
            func.coalesce(func.sum(PrintJob.cost_total), 0.0),
            func.coalesce(func.sum(PrintJob.energy_kwh), 0.0),
            func.coalesce(func.sum(PrintJob.filament_weight_g), 0.0),
            func.coalesce(func.sum(PrintJob.cost_energy), 0.0),
            func.coalesce(func.sum(PrintJob.cost_filament), 0.0),
            func.coalesce(func.sum(PrintJob.cost_depreciation), 0.0),
        )
    ).one()

    by_printer_rows = db.execute(
        select(
            Printer.name,
            func.count(PrintJob.id),
            func.coalesce(func.sum(PrintJob.cost_total), 0.0),
        )
        .join(PrintJob, PrintJob.printer_id == Printer.id)
        .group_by(Printer.id)
        .order_by(func.sum(PrintJob.cost_total).desc())
    ).all()

    # Coste POR HORA por (impresora × tipo de material). La parte de luz usa la
    # potencia media aprendida del historial (se afina con más datos); la parte
    # de máquina es la amortización por hora de cada impresora.
    printers_by_id = {p.id: p for p in db.scalars(select(Printer)).all()}
    cph_rows = db.execute(
        select(
            PrintJob.printer_id,
            Material.material_type,
            func.count(PrintJob.id),
            func.coalesce(func.sum(PrintJob.print_duration_s), 0.0),
        )
        .select_from(PrintJob)
        .join(Material, PrintJob.material_id == Material.id, isouter=True)
        .group_by(PrintJob.printer_id, Material.material_type)
    ).all()

    cost_per_hour = []
    for printer_id, mtype, jobs, dur_s in cph_rows:
        p = printers_by_id.get(printer_id)
        if not p:
            continue
        power, source = avg_power_w(db, printer_id, mtype)
        elec_h = power / 1000.0 * price
        machine_h = p.amortization_per_hour
        cost_per_hour.append({
            "printer": p.name,
            "type": mtype or "Sin tipo",
            "jobs": jobs,
            "hours": round(dur_s / 3600.0, 1),
            "power_w": power,
            "power_source": source,
            "electricity_per_h": round(elec_h, 4),
            "machine_per_h": round(machine_h, 4),
            "total_per_h": round(elec_h + machine_h, 4),
        })
    cost_per_hour.sort(key=lambda r: r["total_per_h"], reverse=True)

    by_month_rows = db.execute(
        select(
            func.strftime("%Y-%m", PrintJob.end_time),
            func.count(PrintJob.id),
            func.coalesce(func.sum(PrintJob.cost_total), 0.0),
        )
        .where(PrintJob.end_time.is_not(None))
        .group_by(func.strftime("%Y-%m", PrintJob.end_time))
        .order_by(func.strftime("%Y-%m", PrintJob.end_time).desc())
    ).all()

    return StatsOut(
        currency=currency,
        total_jobs=totals[0],
        total_cost=round(totals[1], 4),
        total_energy_kwh=round(totals[2], 4),
        total_filament_g=round(totals[3], 2),
        cost_by_component={
            "energy": round(totals[4], 4),
            "filament": round(totals[5], 4),
            "depreciation": round(totals[6], 4),
        },
        by_printer=[
            {"printer": r[0], "jobs": r[1], "cost": round(r[2], 4)}
            for r in by_printer_rows
        ],
        by_month=[
            {"month": r[0], "jobs": r[1], "cost": round(r[2], 4)}
            for r in by_month_rows
        ],
        cost_per_hour=cost_per_hour,
    )
