"""Tests de la resolución del peso de filamento en la ingesta."""

import math

from app.services.ingest import _filament_weight


def _job(status="completed", used_mm=0.0, weight_g=None, planned_mm=None):
    """MoonrakerJob mínimo: solo los campos que mira _filament_weight."""
    class _J:
        pass

    j = _J()
    j.status = status
    j.filament_used_mm = used_mm
    j.filament_weight_g = weight_g
    j.metadata = {"filament_total": planned_mm} if planned_mm is not None else {}
    return j


def test_completed_usa_el_peso_del_slicer():
    j = _job("completed", used_mm=10_000, weight_g=30.0, planned_mm=10_000)
    assert _filament_weight(j, None) == 30.0


def test_completed_no_se_escala_aunque_extruya_de_mas():
    # Gcode re-laminado tras imprimirlo: el ratio supera 1 y escalar inflaría.
    j = _job("completed", used_mm=33_000, weight_g=30.0, planned_mm=10_000)
    assert _filament_weight(j, None) == 30.0


def test_cancelado_a_un_tercio_cobra_un_tercio():
    j = _job("cancelled", used_mm=3_300, weight_g=30.0, planned_mm=10_000)
    assert math.isclose(_filament_weight(j, None), 9.9, abs_tol=1e-6)


def test_shutdown_sin_extruir_no_cobra_filamento():
    j = _job("klippy_shutdown", used_mm=0.0, weight_g=49.81, planned_mm=10_000)
    assert _filament_weight(j, None) == 0.0


def test_mm_negativos_no_dan_peso_negativo():
    # La retracción inicial puede dejar filament_used ligeramente en negativo.
    j = _job("cancelled", used_mm=-20.0, weight_g=30.0, planned_mm=10_000)
    assert _filament_weight(j, None) == 0.0


def test_sin_peso_del_slicer_se_estima_desde_la_longitud():
    j = _job("cancelled", used_mm=1_000, weight_g=None, planned_mm=10_000)
    # 1 m de PLA 1.75 (densidad por defecto 1.24) ≈ 2.98 g.
    assert math.isclose(_filament_weight(j, None), 2.982, abs_tol=0.01)


def test_sin_filament_total_cae_a_la_estimacion():
    j = _job("cancelled", used_mm=1_000, weight_g=30.0, planned_mm=None)
    assert math.isclose(_filament_weight(j, None), 2.982, abs_tol=0.01)
