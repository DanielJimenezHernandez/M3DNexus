"""Esquemas Pydantic para la API."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class PrinterIn(BaseModel):
    name: str
    host: str
    moonraker_port: int = 7125
    ui_port: int = 80
    ha_energy_entity: str | None = None
    ha_power_entity: str | None = None
    power_ref_printer_id: int | None = None
    purchase_price: float = 0.0
    resale_value: float = 0.0
    amortization_years: float = 2.0
    active_days_per_year: float = 250.0
    active_hours_per_day: float = 8.0
    maintenance_per_hour: float = 0.0
    multicolor: bool = False
    slot_count: int = 1
    enabled: bool = True

    @field_validator("slot_count")
    @classmethod
    def _check_slots(cls, v: int) -> int:
        # Rango del hardware: de 1 (una bobina) a 16 (CFS grande).
        if not 1 <= v <= 16:
            raise ValueError("El número de colores debe estar entre 1 y 16")
        return v


class PrinterOut(PrinterIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    moonraker_url: str = ""   # compuesto (host:moonraker_port), solo lectura


class MaterialIn(BaseModel):
    name: str
    material_type: str = "PLA"
    brand: str | None = None
    color: str | None = None
    color_hex: str | None = None
    price_per_kg: float = 0.0
    density_g_cm3: float = 1.24
    stock_level: str = "unknown"
    purchase_url: str | None = None
    active: bool = True

    @field_validator("color_hex")
    @classmethod
    def _check_hex(cls, v: str | None) -> str | None:
        if v and not re.match(r"^#[0-9a-fA-F]{6}$", v):
            raise ValueError("El color debe ser un hex tipo #RRGGBB")
        return v or None

    @field_validator("stock_level")
    @classmethod
    def _check_stock(cls, v: str) -> str:
        from .models import STOCK_LEVELS

        if v not in STOCK_LEVELS:
            raise ValueError(f"Nivel de stock inválido: {v}")
        return v

    @field_validator("purchase_url")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("El enlace de compra debe empezar por http:// o https://")
        return v or None


class MaterialOut(MaterialIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    auto_created: bool = False


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    printer_id: int
    moonraker_job_id: str
    filename: str | None
    status: str
    start_time: datetime | None
    end_time: datetime | None
    print_duration_s: float
    filament_used_mm: float
    filament_weight_g: float
    material_id: int | None
    energy_kwh: float | None
    energy_estimated: bool
    tariff_per_kwh: float
    currency: str
    cost_energy: float
    cost_filament: float
    cost_depreciation: float
    cost_maintenance: float
    cost_total: float
    needs_review: bool


# Logo: data URI de imagen. Se valida la forma para que no pueda romper el
# atributo src="..." al incrustarlo en el HTML del PDF.
LOGO_DATA_URI = re.compile(
    r"^data:image/(png|jpeg|webp|gif|svg\+xml);base64,[A-Za-z0-9+/=\s]+$"
)


class SettingsOut(BaseModel):
    electricity_price_per_kwh: float
    currency: str
    company_name: str = ""
    company_logo: str = ""
    company_info: str = ""
    payment_info: str = ""
    quote_terms: str = ""
    orders_folder_base: str = ""


class SettingsIn(BaseModel):
    electricity_price_per_kwh: float | None = None
    currency: str | None = None
    company_name: str | None = None
    company_logo: str | None = None
    company_info: str | None = None
    payment_info: str | None = None
    quote_terms: str | None = None
    orders_folder_base: str | None = None

    @field_validator("company_logo")
    @classmethod
    def _check_logo(cls, v: str | None) -> str | None:
        if v and not LOGO_DATA_URI.match(v):
            raise ValueError("El logo debe ser una imagen en data URI base64")
        return v


class AssignMaterialIn(BaseModel):
    material_id: int | None


class ExpenseIn(BaseModel):
    label: str
    amount: float = 0.0      # precio por unidad
    quantity: int = 1

    @field_validator("amount")
    @classmethod
    def _amount(cls, v: float) -> float:
        return max(0.0, v)

    @field_validator("quantity")
    @classmethod
    def _qty(cls, v: int) -> int:
        return max(1, v)


class OrderItemIn(BaseModel):
    label: str | None = None
    printer_id: int | None = None
    gcode_filename: str | None = None
    quantity: int = 1
    manual_status: str | None = None
    copy_status: list[str] = []
    extra_expenses: list[ExpenseIn] = []

    @field_validator("copy_status")
    @classmethod
    def _copy(cls, v: list[str]) -> list[str]:
        from .models import COPY_STATES

        if any(s not in COPY_STATES for s in v):
            raise ValueError("Estado de copia inválido")
        return v

    @field_validator("quantity")
    @classmethod
    def _qty(cls, v: int) -> int:
        return max(1, v)

    @field_validator("manual_status")
    @classmethod
    def _item_manual(cls, v: str | None) -> str | None:
        from .models import ITEM_MANUAL

        if v and v not in ITEM_MANUAL:
            raise ValueError(f"Estado de pieza inválido: {v}")
        return v or None


class OrderIn(BaseModel):
    client: str
    description: str | None = None
    payment_status: str = "pending"
    agreed_price: float | None = None
    currency: str | None = None
    due_date: datetime | None = None
    manual_status: str | None = None
    notes: str | None = None
    folder: str | None = None
    deposit_amount: float | None = None
    postproc_rate: float | None = None
    postproc_minutes: int = 0
    extra_expenses: list[ExpenseIn] = []
    items: list[OrderItemIn] = []

    @field_validator("postproc_minutes")
    @classmethod
    def _ppm(cls, v: int) -> int:
        return max(0, v)

    @field_validator("payment_status")
    @classmethod
    def _pay(cls, v: str) -> str:
        from .models import PAYMENT_STATES

        if v not in PAYMENT_STATES:
            raise ValueError(f"Estado de pago inválido: {v}")
        return v

    @field_validator("manual_status")
    @classmethod
    def _order_manual(cls, v: str | None) -> str | None:
        from .models import ORDER_MANUAL

        if v and v not in ORDER_MANUAL:
            raise ValueError(f"Estado de pedido inválido: {v}")
        return v or None


class StatsOut(BaseModel):
    currency: str
    total_jobs: int
    total_cost: float
    total_energy_kwh: float
    total_filament_g: float
    cost_by_component: dict[str, float]
    by_printer: list[dict]
    by_month: list[dict]
    cost_per_hour: list[dict]
