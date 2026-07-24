"""Modelos ORM: Impresora, Material, Job de impresión y Ajustes."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Printer(Base):
    __tablename__ = "printers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    # Conexión: host/IP + puertos. La URL de Moonraker se compone de host:moonraker_port;
    # la interfaz de Klipper (Mainsail/Fluidd) de host:ui_port.
    host: Mapped[str] = mapped_column(String, default="")
    moonraker_port: Mapped[int] = mapped_column(Integer, default=7125)
    ui_port: Mapped[int] = mapped_column(Integer, default=80)
    # Entidad de HA con la energía acumulada (kWh) del POW3 de esta impresora.
    ha_energy_entity: Mapped[str | None] = mapped_column(String, nullable=True)
    ha_power_entity: Mapped[str | None] = mapped_column(String, nullable=True)

    # Sufijos "@..." con los que OrcaSlicer nombra los perfiles de ESTA máquina.
    # Hacen falta varios porque el nombre se teclea a mano y acaba habiendo
    # variantes ("@Hi 0.4 Nozzle", "@HI 0.4 noozle", "@Creality Hi 0.4").
    orca_aliases: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Multicolor: número de huecos (bobinas) que puede cargar a la vez. 1 = una
    # sola. Para un sistema tipo CFS se sube (1..16). ``multicolor`` es solo la
    # casilla que en la UI habilita elegir más de uno.
    multicolor: Mapped[bool] = mapped_column(default=False)
    slot_count: Mapped[int] = mapped_column(Integer, default=1)
    # Material cargado por hueco (lista de material_id o null, de longitud
    # slot_count). Es la asignación MANUAL; si un hueco queda a null se deduce
    # del último filamento impreso.
    loaded_materials: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Amortización al estilo hoja de cálculo: precio repartido entre los años
    # de amortización × días activos al año × horas por día.
    purchase_price: Mapped[float] = mapped_column(Float, default=0.0)
    amortization_years: Mapped[float] = mapped_column(Float, default=2.0)
    active_days_per_year: Mapped[float] = mapped_column(Float, default=250.0)
    active_hours_per_day: Mapped[float] = mapped_column(Float, default=8.0)

    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    jobs: Mapped[list["PrintJob"]] = relationship(
        back_populates="printer", cascade="all, delete-orphan"
    )

    @property
    def moonraker_url(self) -> str:
        return f"http://{self.host}:{self.moonraker_port}"

    @property
    def lifetime_hours(self) -> float:
        return (
            (self.amortization_years or 0.0)
            * (self.active_days_per_year or 0.0)
            * (self.active_hours_per_day or 0.0)
        )

    @property
    def amortization_per_hour(self) -> float:
        lh = self.lifetime_hours
        return (self.purchase_price / lh) if lh else 0.0


# Nivel de bobina. Cualitativo a propósito: se rellena a ojo, no se pesa.
STOCK_FULL = "full"       # bobina completa
STOCK_HALF = "half"       # a medias
STOCK_LOW = "low"         # menos de 100 g
STOCK_EMPTY = "empty"     # agotada
STOCK_UNKNOWN = "unknown"
STOCK_LEVELS = {STOCK_FULL, STOCK_HALF, STOCK_LOW, STOCK_EMPTY, STOCK_UNKNOWN}


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    material_type: Mapped[str] = mapped_column(String, default="PLA")
    brand: Mapped[str | None] = mapped_column(String, nullable=True)
    color: Mapped[str | None] = mapped_column(String, nullable=True)
    # Color en hexadecimal tal como lo guarda OrcaSlicer (#AD15DF): sirve para
    # pintar la muestra en la matriz y distinguir tonos con el mismo nombre.
    color_hex: Mapped[str | None] = mapped_column(String, nullable=True)
    price_per_kg: Mapped[float] = mapped_column(Float, default=0.0)
    density_g_cm3: Mapped[float] = mapped_column(Float, default=1.24)
    # Nivel de bobina (de momento a mano): full / half / low / empty.
    stock_level: Mapped[str] = mapped_column(String, default=STOCK_UNKNOWN)
    # URL de la tienda para recomprar rápido.
    purchase_url: Mapped[str | None] = mapped_column(String, nullable=True)
    active: Mapped[bool] = mapped_column(default=True)
    # True si lo creó la app automáticamente desde los metadatos del gcode.
    auto_created: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    photos: Mapped[list["MaterialPhoto"]] = relationship(
        back_populates="material", cascade="all, delete-orphan"
    )


class PrintJob(Base):
    __tablename__ = "print_jobs"
    __table_args__ = (
        UniqueConstraint("printer_id", "moonraker_job_id", name="uq_printer_job"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id"))
    moonraker_job_id: Mapped[str] = mapped_column(String, index=True)

    filename: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="completed")
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    print_duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    total_duration_s: Mapped[float] = mapped_column(Float, default=0.0)

    filament_used_mm: Mapped[float] = mapped_column(Float, default=0.0)
    filament_weight_g: Mapped[float] = mapped_column(Float, default=0.0)
    material_id: Mapped[int | None] = mapped_column(
        ForeignKey("materials.id"), nullable=True
    )

    # Energía medida en HA durante la ventana de la impresión.
    energy_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    # True cuando HA ya no puede darnos esa energía (job fuera de la retención
    # del recorder, o sin datos del sensor en su ventana). Evita reintentarlo en
    # cada sondeo: sin esta marca, un job sin energía se consulta para siempre.
    energy_unavailable: Mapped[bool] = mapped_column(default=False)

    # Snapshots de los parámetros usados al calcular (para reproducibilidad).
    tariff_per_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String, default="€")

    cost_energy: Mapped[float] = mapped_column(Float, default=0.0)
    cost_filament: Mapped[float] = mapped_column(Float, default=0.0)
    cost_depreciation: Mapped[float] = mapped_column(Float, default=0.0)
    cost_total: Mapped[float] = mapped_column(Float, default=0.0)

    # True si faltan datos para un coste fiable (sin material, sin energía...).
    needs_review: Mapped[bool] = mapped_column(default=False)

    raw_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    printer: Mapped["Printer"] = relationship(back_populates="jobs")
    material: Mapped["Material | None"] = relationship()


class FilamentCalibration(Base):
    """Qué filamento está calibrado en qué impresora y con qué boquilla.

    La calibración no es de un filamento a secas: el mismo rollo necesita flow
    ratio y pressure advance distintos en cada máquina, y cambia otra vez si
    cambias de boquilla. De ahí la clave triple.

    La fuente de verdad son los perfiles de OrcaSlicer; aquí solo se espeja para
    poder consultarlo y para avisar cuando se va a imprimir con un perfil que no
    corresponde a esa impresora.
    """

    __tablename__ = "filament_calibrations"
    __table_args__ = (
        UniqueConstraint(
            "material_id", "printer_id", "nozzle_mm", name="uq_calibracion"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id"))
    nozzle_mm: Mapped[float] = mapped_column(Float, default=0.4)

    # CALIBRATED / NOT_TUNED / UNKNOWN (ver estados abajo).
    status: Mapped[str] = mapped_column(String, default="UNKNOWN")
    # Nombre del preset en Orca, tal cual, con su sufijo "@impresora".
    orca_profile: Mapped[str | None] = mapped_column(String, nullable=True)

    # Parámetros de la calibración (los rellena el import de Orca).
    flow_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    pressure_advance: Mapped[float | None] = mapped_column(Float, nullable=True)
    nozzle_temp: Mapped[float | None] = mapped_column(Float, nullable=True)
    bed_temp: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Evidencia procedente del historial de impresiones.
    jobs_seen: Mapped[int] = mapped_column(Integer, default=0)
    last_job_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # "history" (deducido de los jobs) u "orca" (importado del perfil).
    source: Mapped[str] = mapped_column(String, default="history")
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    material: Mapped["Material"] = relationship()
    printer: Mapped["Printer"] = relationship()


# Estados de calibración.
CAL_CALIBRATED = "CALIBRATED"   # perfil específico de esa impresora
CAL_NOT_TUNED = "NOT_TUNED"     # el propio nombre lo declara sin calibrar
CAL_UNKNOWN = "UNKNOWN"         # perfil genérico, sin sufijo de impresora


class MaterialPhoto(Base):
    """Fotos de referencia de un filamento, para reconocer la bobina al recomprar.

    Van en tabla aparte a propósito: la lista de materiales se recarga a menudo
    y llevar los binarios dentro la haría enorme. Aquí se guardan los bytes y se
    sirven de una en una bajo demanda.

    Dos tipos: ``spool`` (hasta 2, la bobina) y ``color`` (1, de la que se saca
    con cuentagotas el color aproximado que va a ``Material.color_hex``).
    """

    __tablename__ = "material_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material_id: Mapped[int] = mapped_column(
        ForeignKey("materials.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String, default="spool")  # spool | color
    data: Mapped[bytes] = mapped_column(LargeBinary)
    content_type: Mapped[str] = mapped_column(String, default="image/jpeg")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    material: Mapped["Material"] = relationship(back_populates="photos")


# Tipos de foto y cuántas se admiten de cada una.
PHOTO_SPOOL = "spool"
PHOTO_COLOR = "color"
PHOTO_LIMITS = {PHOTO_SPOOL: 2, PHOTO_COLOR: 1}
# Tope de tamaño ya decodificado (el navegador reescala antes de subir).
PHOTO_MAX_BYTES = 3 * 1024 * 1024


class Setting(Base):
    """Ajustes globales editables desde la UI (clave/valor)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String)


# Claves de Setting conocidas.
SETTING_ELECTRICITY_PRICE = "electricity_price_per_kwh"
SETTING_CURRENCY = "currency"
# Momento (ISO) a partir del cual se auto-crean filamentos nuevos. Las
# impresiones terminadas antes de esta marca no auto-crean (evita llenar la BD
# con el back-catálogo).
SETTING_AUTOCREATE_SINCE = "filament_autocreate_since"
# Datos de empresa para el PDF de cotización.
SETTING_COMPANY_NAME = "company_name"
# Logo en data URI (se incrusta en el PDF: así no depende de que cargue a
# tiempo una imagen externa antes de que se dispare la impresión).
SETTING_COMPANY_LOGO = "company_logo"
SETTING_COMPANY_INFO = "company_info"
SETTING_PAYMENT_INFO = "payment_info"
SETTING_QUOTE_TERMS = "quote_terms"
