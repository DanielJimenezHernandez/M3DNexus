"""Tests del cálculo de progreso por posición en el archivo (como Fluidd)."""

from app.services.live import file_progress


def test_progreso_relativo_al_gcode_real():
    # Caso real de la Creality Hi 2: pos 1.456.449 entre inicio 43.888 y
    # fin 5.926.471 → ~24% (lo que muestra Fluidd), no el 55% del M73.
    status = {"virtual_sdcard": {"file_position": 1456449, "progress": 0.2447},
              "display_status": {"progress": 0.55}}
    meta = {"gcode_start_byte": 43888, "gcode_end_byte": 5926471}
    assert abs(file_progress(status, meta) - 0.240) < 0.005


def test_excluye_la_cabecera_con_thumbnails():
    # Al principio del gcode (posición = inicio) el progreso es 0, aunque en
    # bytes absolutos ya se haya "leído" toda la cabecera.
    status = {"virtual_sdcard": {"file_position": 1000, "progress": 0.1}}
    meta = {"gcode_start_byte": 1000, "gcode_end_byte": 11000}
    assert file_progress(status, meta) == 0.0


def test_al_final_es_uno():
    status = {"virtual_sdcard": {"file_position": 11000}}
    meta = {"gcode_start_byte": 1000, "gcode_end_byte": 11000}
    assert file_progress(status, meta) == 1.0


def test_se_acota_entre_cero_y_uno():
    status = {"virtual_sdcard": {"file_position": 20000}}
    meta = {"gcode_start_byte": 1000, "gcode_end_byte": 11000}
    assert file_progress(status, meta) == 1.0


def test_fallback_a_virtual_sdcard_sin_bytes():
    # Sin gcode_start/end en los metadatos, se usa el progreso absoluto.
    status = {"virtual_sdcard": {"progress": 0.42}, "display_status": {"progress": 0.9}}
    assert file_progress(status, {}) == 0.42


def test_fallback_a_display_status():
    # Sin bytes ni virtual_sdcard.progress, se cae al M73 por tiempo.
    status = {"virtual_sdcard": {}, "display_status": {"progress": 0.7}}
    assert file_progress(status, {}) == 0.7


def test_sin_nada_es_cero():
    assert file_progress({}, {}) == 0.0
