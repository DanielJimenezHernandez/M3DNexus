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
    ENTITY_PHOTO_MAX,
    ENTITY_PHOTO_TYPES,
    EntityPhoto,
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
    SETTING_ORDERS_FOLDER_BASE,
    SETTING_PAYMENT_INFO,
    SETTING_QUOTE_TERMS,
    Material,
    Order,
    OrderItem,
    PrintJob,
    Printer,
    Project,
    ProjectPart,
    Setting,
)
from ..schemas import (
    AssignMaterialIn,
    JobOut,
    MaterialIn,
    MaterialOut,
    OrderIn,
    OrderItemIn,
    PrinterIn,
    PrinterOut,
    ProjectIn,
    ProjectEstimateIn,
    SettingsIn,
    SettingsOut,
    StatsOut,
)
from ..services.estimate import (
    avg_power_w,
    estimate_cost,
    estimate_from_history,
    estimate_project,
)
from ..services.calibration import rescan_from_history
from ..services.filament import get_or_create_by_parsed, parse_all_filaments
from ..services.ingest import _borrow_energy, apply_costs, get_settings
from ..services.loaded import resolve_slots, set_slots
from ..services.orca_import import apply_import, plan_import
from ..services.projects import avg_price_per_type, resolve_project
from ..services.orders import (
    ITEM_FAILED,
    ITEM_PARTIAL,
    ITEM_PRINTING,
    ITEM_QUEUED,
    LiveMatch,
    item_status,
    order_margin,
    order_status,
)
from ..services.sync import sync_all, sync_printer

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- #
# Impresoras
# --------------------------------------------------------------------------- #
@router.get("/printers", response_model=list[PrinterOut])
def list_printers(db: Session = Depends(get_session)):
    return db.scalars(select(Printer).order_by(Printer.name)).all()


def _validate_power_ref(db: Session, ref_id: int | None, self_id: int | None) -> None:
    """La impresora de referencia de energía debe existir y no ser ella misma."""
    if ref_id is None:
        return
    if self_id is not None and ref_id == self_id:
        raise HTTPException(422, "Una impresora no puede referenciarse a sí misma")
    if not db.get(Printer, ref_id):
        raise HTTPException(404, f"Impresora de referencia {ref_id} no encontrada")


@router.post("/printers", response_model=PrinterOut, status_code=201)
def create_printer(data: PrinterIn, db: Session = Depends(get_session)):
    _validate_power_ref(db, data.power_ref_printer_id, None)
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
    _validate_power_ref(db, data.power_ref_printer_id, printer_id)
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


@router.post("/estimate/project")
def estimate_project_endpoint(
    payload: ProjectEstimateIn, db: Session = Depends(get_session)
):
    """Estima un proyecto multi-gcode con la flota (promedio/máx/mín, sin fijar máquina)."""
    files = [f.model_dump() for f in payload.files]
    extras = [e.model_dump() for e in payload.extras]
    return estimate_project(db, files, payload.weighting, payload.basis, extras)


# --------------------------------------------------------------------------- #
# Materiales
# --------------------------------------------------------------------------- #
@router.get("/materials", response_model=list[MaterialOut])
def list_materials(db: Session = Depends(get_session)):
    mats = db.scalars(select(Material).order_by(Material.name)).all()
    # URL de la primera foto de bobina de cada material (para el selector con
    # fotos), en UNA consulta batch en vez de una por material.
    first_spool = dict(db.execute(
        select(MaterialPhoto.material_id, func.min(MaterialPhoto.id))
        .where(MaterialPhoto.kind == "spool")
        .group_by(MaterialPhoto.material_id)
    ).all())
    for m in mats:
        pid = first_spool.get(m.id)
        m.photo_url = f"/api/materials/{m.id}/photos/{pid}" if pid else None
    return mats


@router.get("/materials/usage")
def materials_usage(db: Session = Depends(get_session)):
    """Por material: nº de impresiones y fecha de la última. En una consulta."""
    rows = db.execute(
        select(
            PrintJob.material_id,
            func.count(PrintJob.id),
            func.max(PrintJob.end_time),
        )
        .where(PrintJob.material_id.is_not(None))
        .group_by(PrintJob.material_id)
    ).all()
    return {
        mid: {"count": cnt, "last_used": last}
        for mid, cnt, last in rows
    }


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
    jobs = db.scalars(stmt).all()

    # Relación por gcode: pedidos y proyectos que usan cada archivo (deduplicado
    # por pedido/proyecto). Se adjunta a cada job como 'in_use' para el badge.
    refs: dict[str, dict] = {}
    for fn, oid, client in db.execute(
        select(OrderItem.gcode_filename, Order.id, Order.client)
        .join(Order, OrderItem.order_id == Order.id)
        .where(OrderItem.gcode_filename.is_not(None))
    ).all():
        refs.setdefault(fn, {})[("order", oid)] = client or f"Pedido {oid}"
    for fn, pid, name in db.execute(
        select(ProjectPart.gcode_filename, Project.id, Project.name)
        .join(Project, ProjectPart.project_id == Project.id)
        .where(ProjectPart.gcode_filename.is_not(None))
    ).all():
        refs.setdefault(fn, {})[("project", pid)] = name or f"Proyecto {pid}"
    for j in jobs:
        m = refs.get(j.filename, {})
        j.in_use = [{"kind": k, "id": i, "label": lbl} for (k, i), lbl in m.items()]
    return jobs


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
        "material", "energia_kwh", "energia_estimada", "coste_luz",
        "coste_filamento", "coste_amortizacion", "coste_mantenimiento",
        "coste_total", "moneda",
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
            "sí" if j.energy_estimated else "",
            j.cost_energy, j.cost_filament, j.cost_depreciation,
            j.cost_maintenance, j.cost_total, j.currency,
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=m3dnexus_impresiones.csv"},
    )


def _recompute(db: Session, job: PrintJob) -> None:
    price, currency = get_settings(db)
    printer = db.get(Printer, job.printer_id)
    material = db.get(Material, job.material_id) if job.material_id else None
    if printer is not None:
        _borrow_energy(db, job, printer, material)
    apply_costs(job, printer, material, price, currency)


@router.post("/jobs/{job_id}/assign-material", response_model=JobOut)
def assign_material(job_id: int, data: AssignMaterialIn, db: Session = Depends(get_session)):
    job = db.get(PrintJob, job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    if data.material_id is not None and not db.get(Material, data.material_id):
        raise HTTPException(404, "Material no encontrado")
    job.material_id = data.material_id
    job.material_manual = True   # elección del usuario: el sync no la pisa
    _recompute(db, job)
    db.commit()
    db.refresh(job)
    return job


@router.post("/jobs/{job_id}/review", response_model=JobOut)
def review_job(job_id: int, payload: dict = Body(...), db: Session = Depends(get_session)):
    """Califica la impresión (1..5 estrellas, 0/null la quita) con nota libre."""
    job = db.get(PrintJob, job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    rating = payload.get("rating")
    if rating is not None:
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            raise HTTPException(422, "rating debe ser un entero 0..5")
        if not 0 <= rating <= 5:
            raise HTTPException(422, "rating debe estar entre 0 y 5")
    job.rating = rating or None          # 0 = quitar calificación
    notes = (payload.get("notes") or "").strip()
    job.review_notes = notes or None
    db.commit()
    db.refresh(job)
    return job


@router.get("/jobs/{job_id}/thumbnail")
def job_thumbnail(job_id: int, db: Session = Depends(get_session)):
    """Sirve la miniatura guardada del gcode (desde la BD; funciona offline)."""
    job = db.get(PrintJob, job_id)
    if not job or not job.thumbnail:
        raise HTTPException(404, "Sin miniatura")
    return Response(
        content=job.thumbnail,
        media_type="image/png",
        # Sin 'immutable' ni caché larga: el id de job puede reusarse tras un
        # borrado (PK entera sin AUTOINCREMENT) y mostraría la miniatura vieja.
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/gcode-thumbnail")
def gcode_thumbnail(filename: str, db: Session = Depends(get_session)):
    """Miniatura de la última impresión guardada de un gcode, por su nombre.

    Para previsualizar una pieza al editar un pedido, donde el gcode se elige de
    un desplegable y aún no hay un job concreto asociado.
    """
    job = db.scalar(
        select(PrintJob)
        .where(PrintJob.filename == filename, PrintJob.has_thumbnail == True)  # noqa: E712
        .order_by(PrintJob.end_time.desc().nulls_last())
    )
    if not job or not job.thumbnail:
        raise HTTPException(404, "Sin miniatura")
    return Response(
        content=job.thumbnail,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/gcode-info")
def gcode_info(filename: str, printer_id: int | None = None,
               db: Session = Depends(get_session)):
    """Datos de la última impresión real de un gcode (para autocompletar partes).

    Devuelve el tipo de material, el tiempo REAL de impresión y el peso medido
    por el slicer. Prefiere la impresora indicada; si no hay, cualquiera.
    """
    q = select(PrintJob).where(
        PrintJob.filename == filename, PrintJob.print_duration_s > 0
    )
    job = None
    if printer_id is not None:
        job = db.scalar(
            q.where(PrintJob.printer_id == printer_id)
            .order_by(PrintJob.end_time.desc().nulls_last())
        )
    if job is None:
        job = db.scalar(q.order_by(PrintJob.end_time.desc().nulls_last()))
    if job is None:
        return {"found": False}
    mat = db.get(Material, job.material_id) if job.material_id else None
    return {
        "found": True,
        "material_type": mat.material_type if mat else None,
        "print_time_s": job.print_duration_s,
        "filament_g": job.filament_weight_g,
    }


@router.get("/jobs/{job_id}/filaments")
def job_gcode_filaments(job_id: int, db: Session = Depends(get_session)):
    """Filamentos que lista el gcode del job (multi-material incluido).

    Para elegir a mano con cuál se imprimió cuando el proyecto tenía varios. Cada
    uno indica si ya existe como material (``material_id``) o habría que crearlo.
    """
    job = db.get(PrintJob, job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    out = []
    for p in parse_all_filaments(job.raw_metadata or {}):
        existing = db.scalar(
            select(Material).where(func.lower(Material.name) == p.full_name.lower())
        )
        out.append({
            "name": p.full_name,
            "brand": p.brand,
            "material_type": p.material_type,
            "color": p.color,
            "material_id": existing.id if existing else None,
            "assigned": existing is not None and existing.id == job.material_id,
        })
    return {"filaments": out, "current_material_id": job.material_id}


@router.post("/jobs/{job_id}/set-filament", response_model=JobOut)
def set_job_filament(
    job_id: int, payload: dict = Body(...), db: Session = Depends(get_session)
):
    """Asigna al job uno de los filamentos del gcode, creándolo si no existe."""
    job = db.get(PrintJob, job_id)
    if not job:
        raise HTTPException(404, "Job no encontrado")
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "Falta 'name'")
    parsed = next(
        (p for p in parse_all_filaments(job.raw_metadata or {})
         if p.full_name.lower() == name.lower()),
        None,
    )
    if parsed is None:
        raise HTTPException(404, "Ese filamento no está en el gcode")
    material = get_or_create_by_parsed(db, parsed)
    job.material_id = material.id
    job.material_manual = True   # elección del usuario: el sync no la pisa
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
# Pedidos
# --------------------------------------------------------------------------- #
def _compose_folder(base: str, folder: str | None) -> str | None:
    """Ruta local completa del pedido: <base>/<folder>, respetando el separador.

    Solo texto para mostrar y copiar; el servidor no toca esa carpeta.
    """
    if not folder:
        return None
    base = (base or "").rstrip("/\\")
    if not base:
        return folder
    sep = "\\" if ("\\" in base and "/" not in base) else "/"
    return f"{base}{sep}{folder}"


def _sum_expenses(items) -> float:
    """Suma los gastos [{label, amount, quantity}] como precio × cantidad."""
    total = 0.0
    for e in items or []:
        try:
            total += float(e.get("amount") or 0) * int(e.get("quantity") or 1)
        except (TypeError, ValueError, AttributeError):
            pass
    return total


def _gcode_thumbnail_url(db: Session, gcode_filename: str | None) -> str | None:
    """URL de la miniatura de la última impresión guardada de ese gcode (o None).

    La miniatura es del gcode, igual en cualquier máquina, así que sirve la de
    cualquier impresión de ese archivo que la tenga guardada (funciona offline).
    """
    if not gcode_filename:
        return None
    jid = db.scalar(
        select(PrintJob.id)
        .where(
            PrintJob.filename == gcode_filename,
            PrintJob.has_thumbnail == True,  # noqa: E712
        )
        .order_by(PrintJob.end_time.desc().nulls_last())
    )
    return f"/api/jobs/{jid}/thumbnail" if jid else None


def _resolve_order(order: Order, live_by_printer, db: Session, base: str = "") -> dict:
    """Serializa un pedido con el estado de cada pieza (marcado a mano)."""
    item_dicts, item_states = [], []
    total_cost = 0.0
    for it in order.items:
        live = live_by_printer.get(it.printer_id)
        lm = (
            LiveMatch(printer_id=live["printer_id"], filename=live.get("filename") or "",
                      progress=live.get("progress") or 0.0, eta_s=live.get("eta_s"))
            if live else None
        )
        st = item_status(
            printer_id=it.printer_id, gcode_filename=it.gcode_filename,
            quantity=it.quantity, manual=it.manual_status, live=lm,
            copy_status=it.copy_status,
        )
        item_states.append(st)
        # Coste estimado de la pieza (× cantidad), si hay datos en el historial.
        # Del mismo estimate salen tiempo, filamento y material para el detalle.
        unit_cost = est_time_s = filament_g = est_material = None
        est_material_id = None
        no_price = False
        if it.printer_id and it.gcode_filename:
            printer = db.get(Printer, it.printer_id)
            if printer:
                est = estimate_from_history(db, printer, it.gcode_filename)
                if est.get("has_data"):
                    unit_cost = est["cost"]["total"]
                    total_cost += unit_cost * it.quantity
                    est_time_s = est.get("estimated_time_s")
                    filament_g = est.get("filament_g")
                    est_material = est.get("material")
                    # Id del material, para poder saltar a editar su precio.
                    if est_material:
                        est_material_id = db.scalar(
                            select(Material.id).where(Material.name == est_material)
                        )
                    # El coste va incompleto si hay filamento pero no se cobra:
                    # material sin precio/kg o sin material resuelto.
                    no_price = (filament_g or 0) > 0 and (est["cost"]["filament"] or 0) <= 0
        item_expenses = list(it.extra_expenses or [])
        total_cost += _sum_expenses(item_expenses)
        item_dicts.append({
            "id": it.id, "label": it.label, "printer_id": it.printer_id,
            "printer_name": db.get(Printer, it.printer_id).name if it.printer_id else None,
            "gcode_filename": it.gcode_filename, "quantity": it.quantity,
            "manual_status": it.manual_status, "unit_cost": unit_cost,
            "thumbnail_url": _gcode_thumbnail_url(db, it.gcode_filename),
            "est_time_s": est_time_s, "filament_g": filament_g, "est_material": est_material,
            "est_material_id": est_material_id, "no_price": no_price,
            "extra_expenses": item_expenses,
            "copy_status": list(it.copy_status or []),
            **st.as_dict(),
        })

    # Gastos extra a nivel de pedido, también son coste.
    order_expenses = list(order.extra_expenses or [])
    expenses_total = _sum_expenses(order_expenses)
    total_cost += expenses_total
    # Post-procesado del pedido: minutos totales × tarifa/hora.
    postproc_cost = round((order.postproc_minutes or 0) / 60.0 * (order.postproc_rate or 0.0), 2)
    total_cost += postproc_cost

    ostatus = order_status(order.manual_status, item_states)
    return {
        "id": order.id, "client": order.client, "description": order.description,
        "payment_status": order.payment_status, "agreed_price": order.agreed_price,
        "currency": order.currency, "due_date": order.due_date,
        "manual_status": order.manual_status, "notes": order.notes,
        "created_at": order.created_at, "status": ostatus,
        "folder": order.folder,
        "folder_path": _compose_folder(base, order.folder),
        "deposit_amount": order.deposit_amount,
        "postproc_rate": order.postproc_rate,
        "postproc_minutes": order.postproc_minutes or 0,
        "postproc_cost": postproc_cost,
        "extra_expenses": order_expenses,
        "items": item_dicts,
        "margin": order_margin(total_cost, order.agreed_price),
        "photo_url": _entity_first_photo(db, "order", order.id),
    }


@router.get("/orders")
def list_orders(db: Session = Depends(get_session)):
    """Tablero de pedidos con el estado de impresión deducido de la realidad."""
    from ..services.live import tracker

    live_by_printer = {s["printer_id"]: s for s in tracker.snapshot()}
    base = _get_setting(db, SETTING_ORDERS_FOLDER_BASE)
    orders = db.scalars(
        select(Order).order_by(Order.created_at.desc())
    ).unique().all()
    return [_resolve_order(o, live_by_printer, db, base) for o in orders]


@router.get("/orders/queue")
def orders_queue(db: Session = Depends(get_session)):
    """Piezas pendientes agrupadas por impresora, para saber qué cargar."""
    from ..services.live import tracker

    live_by_printer = {s["printer_id"]: s for s in tracker.snapshot()}
    queue: dict = {}
    orders = db.scalars(select(Order)).unique().all()
    for o in orders:
        if o.manual_status in ("delivered", "cancelled"):
            continue
        for it in o.items:
            if not it.printer_id:
                continue
            live = live_by_printer.get(it.printer_id)
            lm = LiveMatch(it.printer_id, live.get("filename") or "",
                           live.get("progress") or 0.0, live.get("eta_s")) if live else None
            st = item_status(printer_id=it.printer_id, gcode_filename=it.gcode_filename,
                             quantity=it.quantity, manual=it.manual_status, live=lm,
                             copy_status=it.copy_status)
            if st.status in (ITEM_PRINTING, ITEM_QUEUED, ITEM_PARTIAL, ITEM_FAILED):
                p = db.get(Printer, it.printer_id)
                queue.setdefault(it.printer_id, {"printer": p.name if p else "—", "items": []})
                queue[it.printer_id]["items"].append({
                    "order_id": o.id, "client": o.client,
                    "label": it.label or it.gcode_filename, **st.as_dict(),
                })
    # Dentro de cada impresora, primero lo que está imprimiendo.
    rank = {ITEM_PRINTING: 0, ITEM_PARTIAL: 1, ITEM_FAILED: 2, ITEM_QUEUED: 3}
    for q in queue.values():
        q["items"].sort(key=lambda i: rank.get(i["status"], 9))
    return list(queue.values())


def _apply_order(order: Order, data: OrderIn, db: Session) -> None:
    """Vuelca los campos del pedido y reemplaza sus piezas."""
    order.client = data.client
    order.description = data.description
    order.payment_status = data.payment_status
    order.agreed_price = data.agreed_price
    order.currency = data.currency or get_settings(db)[1]
    order.due_date = data.due_date
    order.manual_status = data.manual_status
    order.notes = data.notes
    order.folder = data.folder
    # El anticipo solo tiene sentido si el pago es "anticipo"; si no, se limpia.
    order.deposit_amount = data.deposit_amount if data.payment_status == "deposit" else None
    order.postproc_rate = data.postproc_rate
    order.postproc_minutes = data.postproc_minutes
    order.extra_expenses = [e.model_dump() for e in data.extra_expenses] or None
    # Reemplazo completo de piezas: la UI manda la lista entera.
    order.items.clear()
    db.flush()
    for it in data.items:
        order.items.append(_make_order_item(it, db))


def _make_order_item(it: OrderItemIn, db: Session) -> OrderItem:
    """Construye una OrderItem validada desde su esquema (piezas y añadidos)."""
    if it.printer_id is not None and not db.get(Printer, it.printer_id):
        raise HTTPException(404, f"Impresora {it.printer_id} no encontrada")
    payload = it.model_dump()
    payload["extra_expenses"] = payload["extra_expenses"] or None
    # El estado por copia se recorta/rellena a la cantidad (por si cambió).
    cs = (payload.get("copy_status") or [])[: it.quantity]
    cs += ["pending"] * (it.quantity - len(cs))
    payload["copy_status"] = cs if any(s != "pending" for s in cs) else None
    return OrderItem(**payload)


@router.post("/orders", status_code=201)
def create_order(data: OrderIn, db: Session = Depends(get_session)):
    order = Order(client=data.client)
    db.add(order)
    _apply_order(order, data, db)
    db.commit()
    db.refresh(order)
    from ..services.live import tracker
    return _resolve_order(order, {s["printer_id"]: s for s in tracker.snapshot()},
                          db, _get_setting(db, SETTING_ORDERS_FOLDER_BASE))


@router.put("/orders/{order_id}")
def update_order(order_id: int, data: OrderIn, db: Session = Depends(get_session)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Pedido no encontrado")
    _apply_order(order, data, db)
    db.commit()
    db.refresh(order)
    from ..services.live import tracker
    return _resolve_order(order, {s["printer_id"]: s for s in tracker.snapshot()},
                          db, _get_setting(db, SETTING_ORDERS_FOLDER_BASE))


@router.post("/orders/{order_id}/items", status_code=201)
def add_order_item(order_id: int, data: OrderItemIn, db: Session = Depends(get_session)):
    """Añade una pieza a un pedido (p.ej. desde una impresión ya hecha)."""
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Pedido no encontrado")
    order.items.append(_make_order_item(data, db))
    db.commit()
    return {"ok": True, "order_id": order.id}


@router.delete("/order-items/{item_id}", status_code=204)
def delete_order_item(item_id: int, db: Session = Depends(get_session)):
    """Quita una pieza de su pedido (sin abrir el editor completo)."""
    item = db.get(OrderItem, item_id)
    if not item:
        raise HTTPException(404, "Pieza no encontrada")
    db.delete(item)
    db.commit()


@router.delete("/orders/{order_id}", status_code=204)
def delete_order(order_id: int, db: Session = Depends(get_session)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Pedido no encontrado")
    db.delete(order)   # cascade borra sus piezas
    db.commit()


# --------------------------------------------------------------------------- #
# Proyectos (desarrollo de producto: coste real por peso de báscula)
# --------------------------------------------------------------------------- #
def _apply_project(project: Project, data: ProjectIn) -> None:
    project.name = data.name
    project.notes = data.notes
    project.total_weight_g = data.total_weight_g
    project.extra_expenses = [e.model_dump() for e in data.extra_expenses] or None
    project.parts.clear()
    for pt in data.parts:
        project.parts.append(ProjectPart(**pt.model_dump()))


@router.get("/projects/material-prices")
def project_material_prices(db: Session = Depends(get_session)):
    """Precio medio /kg por tipo de material (para el editor de proyectos)."""
    price, currency = get_settings(db)
    return {"prices": avg_price_per_type(db), "currency": currency}


@router.get("/projects")
def list_projects(db: Session = Depends(get_session)):
    projects = db.scalars(select(Project).order_by(Project.created_at.desc())).all()
    return [resolve_project(db, p) for p in projects]


@router.post("/projects", status_code=201)
def create_project(data: ProjectIn, db: Session = Depends(get_session)):
    project = Project(name=data.name)
    db.add(project)
    _apply_project(project, data)
    db.commit()
    db.refresh(project)
    return resolve_project(db, project)


@router.post("/projects/{project_id}/add-job", status_code=201)
def add_job_to_project(
    project_id: int, payload: dict = Body(...), db: Session = Depends(get_session)
):
    """Añade una impresión como parte del proyecto (para calibrar contra báscula).

    La parte se rellena con el PESO ESTIMADO del slicer y el TIEMPO REAL del
    print; el usuario luego pesa el producto completo y compara.
    """
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Proyecto no encontrado")
    job = db.get(PrintJob, payload.get("job_id"))
    if not job:
        raise HTTPException(404, "Impresión no encontrada")
    mat = db.get(Material, job.material_id) if job.material_id else None
    project.parts.append(ProjectPart(
        printer_id=job.printer_id,
        gcode_filename=job.filename,
        material_type=(mat.material_type if mat else "PLA"),
        weight_g=job.filament_weight_g or 0.0,     # estimado del slicer
        print_time_s=job.print_duration_s or 0.0,  # tiempo real
        quantity=1,
    ))
    db.commit()
    return {"ok": True, "project_id": project.id}


@router.put("/projects/{project_id}")
def update_project(project_id: int, data: ProjectIn, db: Session = Depends(get_session)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Proyecto no encontrado")
    _apply_project(project, data)
    db.commit()
    db.refresh(project)
    return resolve_project(db, project)


@router.delete("/project-parts/{part_id}", status_code=204)
def delete_project_part(part_id: int, db: Session = Depends(get_session)):
    """Quita una parte de su proyecto (sin abrir el editor completo)."""
    part = db.get(ProjectPart, part_id)
    if not part:
        raise HTTPException(404, "Parte no encontrada")
    db.delete(part)
    db.commit()


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_session)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Proyecto no encontrado")
    db.delete(project)
    db.commit()


# --------------------------------------------------------------------------- #
# Fotos de pedido / proyecto (producto terminado)
# --------------------------------------------------------------------------- #
def _entity_first_photo(db: Session, entity_type: str, entity_id: int) -> str | None:
    """URL de la primera foto de un pedido/proyecto (para la tarjeta)."""
    pid = db.scalar(
        select(EntityPhoto.id)
        .where(EntityPhoto.entity_type == entity_type, EntityPhoto.entity_id == entity_id)
        .order_by(EntityPhoto.id)
    )
    return f"/api/entity-photos/{pid}" if pid else None


@router.get("/entity-photos")
def list_entity_photos(entity_type: str, entity_id: int, db: Session = Depends(get_session)):
    if entity_type not in ENTITY_PHOTO_TYPES:
        raise HTTPException(422, "entity_type inválido")
    rows = db.scalars(
        select(EntityPhoto)
        .where(EntityPhoto.entity_type == entity_type, EntityPhoto.entity_id == entity_id)
        .order_by(EntityPhoto.id)
    ).all()
    return [{"id": p.id, "url": f"/api/entity-photos/{p.id}"} for p in rows]


@router.post("/entity-photos", status_code=201)
def add_entity_photo(payload: dict = Body(...), db: Session = Depends(get_session)):
    et = payload.get("entity_type")
    eid = payload.get("entity_id")
    if et not in ENTITY_PHOTO_TYPES:
        raise HTTPException(422, "entity_type inválido")
    exists = db.get(Order, eid) if et == "order" else db.get(Project, eid)
    if not exists:
        raise HTTPException(404, "Pedido/proyecto no encontrado")
    count = db.scalar(
        select(func.count(EntityPhoto.id)).where(
            EntityPhoto.entity_type == et, EntityPhoto.entity_id == eid
        )
    )
    if count >= ENTITY_PHOTO_MAX:
        raise HTTPException(409, f"Máximo {ENTITY_PHOTO_MAX} fotos; borra alguna antes")
    raw, ctype = _decode_data_uri(payload.get("data_uri", ""))
    photo = EntityPhoto(entity_type=et, entity_id=eid, data=raw, content_type=ctype)
    db.add(photo)
    db.commit()
    db.refresh(photo)
    return {"id": photo.id, "url": f"/api/entity-photos/{photo.id}"}


@router.get("/entity-photos/{photo_id}")
def get_entity_photo(photo_id: int, db: Session = Depends(get_session)):
    photo = db.get(EntityPhoto, photo_id)
    if not photo:
        raise HTTPException(404, "Foto no encontrada")
    return Response(
        content=photo.data,
        media_type=photo.content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.delete("/entity-photos/{photo_id}", status_code=204)
def delete_entity_photo(photo_id: int, db: Session = Depends(get_session)):
    photo = db.get(EntityPhoto, photo_id)
    if photo:
        db.delete(photo)
        db.commit()


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
        orders_folder_base=_get_setting(db, SETTING_ORDERS_FOLDER_BASE),
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
    if data.orders_folder_base is not None:
        _set_setting(db, SETTING_ORDERS_FOLDER_BASE, data.orders_folder_base)
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
    try:
        data, ctype = MoonrakerClient(printer.moonraker_url).thumbnail(path)
    except Exception:  # noqa: BLE001
        raise HTTPException(502, "La impresora no responde")
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
            func.coalesce(func.sum(PrintJob.cost_maintenance), 0.0),
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
        machine_h = p.machine_per_hour
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
            "maintenance": round(totals[7], 4),
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
