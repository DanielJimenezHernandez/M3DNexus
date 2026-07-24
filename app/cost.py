"""Motor de costes — funciones puras, sin dependencias de I/O.

Calcula el coste de una impresión a partir de magnitudes ya medidas
(energía, peso de filamento, duración). No habla con Moonraker ni con HA;
eso lo hacen las capas de integración. Mantenerlo puro lo hace testeable.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

# Diámetro de filamento por defecto (mm). 1.75 es el estándar de facto.
DEFAULT_FILAMENT_DIAMETER_MM = 1.75
# Densidad por defecto (g/cm³) si no se conoce el material. PLA ≈ 1.24.
DEFAULT_FILAMENT_DENSITY = 1.24


def mm_to_grams(
    length_mm: float,
    diameter_mm: float = DEFAULT_FILAMENT_DIAMETER_MM,
    density_g_cm3: float = DEFAULT_FILAMENT_DENSITY,
) -> float:
    """Convierte longitud de filamento (mm) a peso (g).

    Se usa como *fallback* cuando el slicer no incluye el peso en los
    metadatos del gcode. Volumen de un cilindro × densidad.
    """
    if length_mm <= 0:
        return 0.0
    radius_cm = (diameter_mm / 2.0) / 10.0
    length_cm = length_mm / 10.0
    volume_cm3 = math.pi * radius_cm**2 * length_cm
    return volume_cm3 * density_g_cm3


@dataclass(frozen=True)
class CostInputs:
    """Todo lo necesario para calcular el coste de una impresión."""

    energy_kwh: float = 0.0
    electricity_price_per_kwh: float = 0.0

    filament_weight_g: float = 0.0
    filament_price_per_kg: float = 0.0

    print_duration_hours: float = 0.0
    # Amortización: precio de la máquina repartido entre su vida útil (horas).
    machine_purchase_price: float = 0.0
    machine_lifetime_hours: float = 0.0


@dataclass(frozen=True)
class CostBreakdown:
    """Desglose del coste, redondeado a céntimos de fracción."""

    energy: float = 0.0
    filament: float = 0.0
    depreciation: float = 0.0
    total: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


def compute_cost(inp: CostInputs) -> CostBreakdown:
    """Calcula el desglose de coste de una impresión.

    Funciona igual para impresiones completadas y falladas: si el job se
    canceló a medias, ``energy_kwh`` y ``filament_weight_g`` reflejan lo
    consumido hasta ese punto, así que el coste sigue siendo correcto.
    """
    energy = inp.energy_kwh * inp.electricity_price_per_kwh
    filament = (inp.filament_weight_g / 1000.0) * inp.filament_price_per_kg

    depreciation = 0.0
    if inp.machine_lifetime_hours > 0:
        rate = inp.machine_purchase_price / inp.machine_lifetime_hours
        depreciation = inp.print_duration_hours * rate

    total = energy + filament + depreciation
    return CostBreakdown(
        energy=round(energy, 4),
        filament=round(filament, 4),
        depreciation=round(depreciation, 4),
        total=round(total, 4),
    )
