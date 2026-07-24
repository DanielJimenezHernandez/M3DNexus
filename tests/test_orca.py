"""Tests del lector de perfiles de OrcaSlicer.

PERFIL_REAL es un perfil de usuario tal cual, sin retocar: es la referencia
frente a la que se valida el parseo.
"""

import pytest

from app.services.orca import (
    CAL_BASIC,
    CAL_FULL,
    CAL_NONE,
    OrcaProfile,
    match_printer,
    material_type,
    nozzle_from_name,
    parse_orca_profile,
    printer_model,
)

PERFIL_REAL = {
    "name": "Elegoo PLA Purple @ Ender3v3SE 0.4 Nozzle",
    "type": "filament",
    "base_id": "d9awwT21ZLo1iXoh",
    "version": "2.3.2.60",
    "inherits": "Creality Generic PLA @Ender-3V3-all",
    "filament_wipe": "nil",
    "filament_z_hop": "nil",
    "hot_plate_temp": "50",
    "filament_vendor": "Elegoo",
    "nozzle_temperature": "210",
    "compatible_printers": '"Creality Ender-3 V3 SE 0.2 nozzle";"Creality Ender-3 V3 SE 0.4 nozzle";"Creality Ender-3 V3 SE 0.6 nozzle";"Creality Ender-3 V3 SE 0.8 nozzle"',
    "filament_settings_id": '"Elegoo PLA Purple @ Ender3v3SE 0.4 Nozzle"',
    "filament_z_hop_types": "nil",
    "slow_down_layer_time": "0",
    "filament_wipe_distance": "nil",
    "default_filament_colour": "#AD15DF",
    "filament_retraction_speed": "nil",
    "filament_retraction_length": "nil",
    "close_fan_the_first_x_layers": "2",
    "hot_plate_temp_initial_layer": "50",
    "filament_max_volumetric_speed": "21",
    "nozzle_temperature_initial_layer": "210",
    "filament_retract_when_changing_layer": "nil",
}


# Segundo perfil real. A diferencia del anterior sí tiene pressure advance y
# flow ratio, y su compatible_printers mezcla dos modelos con otro formato de
# boquilla, entre paréntesis y con variantes del perfil de máquina.
PERFIL_CON_PA = {
    "name": "Elegoo PLA Mint @ K1SE 0.4 Nozzle",
    "type": "filament",
    "base_id": "RcBNzytWgwRrwXXz",
    "version": "2.3.2.60",
    "inherits": "Generic PLA @System",
    "filament_wipe": "0",
    "filament_z_hop": "nil",
    "hot_plate_temp": "50",
    "filament_vendor": "Elegoo",
    "pressure_advance": "0.045",
    "nozzle_temperature": "205",
    "compatible_printers": '"Creality K1 (0.4 nozzle)";"Creality K1 (0.4 nozzle) - Mod";"Creality K1 (0.4 nozzle) - Timelapse";"Creality K1 (0.6 nozzle)";"Creality K1 (0.8 nozzle)";"Creality K1 SE 0.4 nozzle"',
    "filament_flow_ratio": "1.01",
    "textured_plate_temp": "50",
    "filament_settings_id": '"Elegoo PLA Mint @ K1SE 0.4 Nozzle"',
    "slow_down_layer_time": "0",
    "default_filament_colour": "#D7FDB5",
    "enable_pressure_advance": "1",
    "filament_retraction_speed": "nil",
    "hot_plate_temp_initial_layer": "50",
    "filament_max_volumetric_speed": "20",
    "nozzle_temperature_initial_layer": "205",
    "textured_plate_temp_initial_layer": "50",
    "filament_retract_when_changing_layer": "0",
}


class ImpresoraFalsa:
    def __init__(self, name, aliases=None):
        self.name = name
        self.orca_aliases = aliases or []


IMPRESORAS = [
    ImpresoraFalsa("Creality Hi", ["hi 0.4 nozzle"]),
    ImpresoraFalsa("Creality K1SE", ["k1se 0.4 nozzle"]),
    ImpresoraFalsa("Voron 2.4", ["voron 0.6"]),
    ImpresoraFalsa("Ender 3v3 SE", ["ender3v3se 0.4 nozzle"]),
    ImpresoraFalsa("Ender 3V2", ["ender 0.4 nozzle"]),
]


class TestPerfilReal:
    @pytest.fixture
    def p(self):
        return parse_orca_profile(PERFIL_REAL)

    def test_identidad_del_filamento(self, p):
        assert p.vendor == "Elegoo"
        assert p.material_type == "PLA"        # heredado: sale del nombre
        assert p.colour == "#AD15DF"
        assert p.filament_name == "Elegoo PLA Purple"

    def test_la_boquilla_sale_del_nombre_no_de_compatible_printers(self, p):
        # compatible_printers trae 0.2/0.4/0.6/0.8: no distingue.
        assert p.nozzle_mm == 0.4

    def test_compatible_printers_colapsa_a_un_solo_modelo(self, p):
        # Cuatro entradas, un modelo: son las variantes de boquilla.
        assert p.printer_models == ["Creality Ender-3 V3 SE"]

    def test_solo_cuenta_como_calibrado_lo_sobrescrito(self, p):
        assert p.is_tuned
        assert p.tuned_keys == [
            "close_fan_the_first_x_layers",
            "filament_max_volumetric_speed",
            "hot_plate_temp",
            "hot_plate_temp_initial_layer",
            "nozzle_temperature",
            "nozzle_temperature_initial_layer",
            "slow_down_layer_time",
        ]

    def test_los_nil_no_cuentan_como_ajuste(self, p):
        assert "filament_wipe" not in p.tuned_keys
        assert "filament_retraction_speed" not in p.tuned_keys

    def test_los_metadatos_no_cuentan_como_ajuste(self, p):
        for k in ("name", "inherits", "filament_vendor", "compatible_printers",
                  "default_filament_colour", "version", "base_id"):
            assert k not in p.tuned_keys

    def test_valores_concretos(self, p):
        assert p.nozzle_temp == 210
        assert p.bed_temp == 50
        assert p.max_volumetric_speed == 21
        # Este perfil no toca flow ni pressure advance.
        assert p.flow_ratio is None
        assert p.pressure_advance is None

    def test_empareja_con_la_impresora_correcta(self, p):
        assert match_printer(p, IMPRESORAS).name == "Ender 3v3 SE"


class TestPerfilConPressureAdvance:
    """El caso que de verdad importa: filamento calibrado a fondo."""

    @pytest.fixture
    def p(self):
        return parse_orca_profile(PERFIL_CON_PA)

    def test_lee_pressure_advance_y_flow_ratio(self, p):
        assert p.pressure_advance == 0.045
        assert p.flow_ratio == 1.01
        assert p.pressure_advance_enabled

    def test_se_distingue_del_que_solo_toca_temperaturas(self, p):
        # Los dos perfiles están "tocados", pero no al mismo nivel.
        assert p.calibration_level == CAL_FULL
        assert parse_orca_profile(PERFIL_REAL).calibration_level == CAL_BASIC

    def test_compatible_printers_con_dos_modelos_distintos(self, p):
        # Seis entradas → dos modelos: la K1 y la K1 SE son máquinas distintas.
        assert p.printer_models == ["Creality K1", "Creality K1 SE"]

    def test_elige_la_k1se_y_no_la_k1(self, p):
        # Quedarse con el primer modelo daría la máquina equivocada.
        assert match_printer(p, IMPRESORAS).name == "Creality K1SE"

    def test_identidad(self, p):
        assert p.filament_name == "Elegoo PLA Mint"
        assert p.vendor == "Elegoo"
        assert p.material_type == "PLA"
        assert p.colour == "#D7FDB5"
        assert p.nozzle_mm == 0.4

    def test_un_cero_explicito_cuenta_como_ajuste(self, p):
        # "0" es un valor puesto a mano; "nil" es heredado. No confundirlos.
        assert "filament_wipe" in p.tuned_keys
        assert "filament_retract_when_changing_layer" in p.tuned_keys
        assert "filament_retraction_speed" not in p.tuned_keys


class TestGradoDeCalibracion:
    def test_sin_nada_sobrescrito(self):
        p = parse_orca_profile({"name": "X @Hi 0.4", "type": "filament",
                                "filament_wipe": "nil"})
        assert p.calibration_level == CAL_NONE

    def test_solo_flow_ratio_ya_es_completo(self):
        p = parse_orca_profile({"name": "X @Hi 0.4", "type": "filament",
                                "filament_flow_ratio": "0.98"})
        assert p.calibration_level == CAL_FULL

    def test_pa_presente_pero_desactivado_se_reporta(self):
        p = parse_orca_profile({"name": "X @Hi 0.4", "type": "filament",
                                "pressure_advance": "0.04",
                                "enable_pressure_advance": "0"})
        assert p.pressure_advance == 0.04
        assert not p.pressure_advance_enabled


class TestModeloYBoquilla:
    @pytest.mark.parametrize("entrada,esperado", [
        ("Creality Ender-3 V3 SE 0.4 nozzle", "Creality Ender-3 V3 SE"),
        ("Creality K1 (0.4 nozzle)", "Creality K1"),
        ("Creality K1 (0.4 nozzle) - Timelapse", "Creality K1"),
        ("Creality K1 SE 0.4 nozzle", "Creality K1 SE"),
        ("Creality Ender-3 V3 SE 0.8 nozzle", "Creality Ender-3 V3 SE"),
        ("Voron 2.4 0.6 nozzle", "Voron 2.4"),
        ("Creality Hi", "Creality Hi"),
    ])
    def test_quita_el_sufijo_de_boquilla(self, entrada, esperado):
        assert printer_model(entrada) == esperado

    @pytest.mark.parametrize("nombre,esperado", [
        ("Elegoo PLA Purple @ Ender3v3SE 0.4 Nozzle", 0.4),
        ("Elegoo PLA White @Voron 0.6", 0.6),
        ("Elegoo PLA White", None),          # sin sufijo
        ("Elegoo PLA @Hi nozzle", None),     # sufijo sin medida
    ])
    def test_boquilla_desde_el_nombre(self, nombre, esperado):
        assert nozzle_from_name(nombre) == esperado


class TestTipoDeMaterial:
    def test_explicito_si_esta(self):
        assert material_type({"filament_type": "PETG"}) == "PETG"

    def test_desde_el_nombre_cuando_se_hereda(self):
        assert material_type({"name": "SUNLU PETG Black @Ender 0.4"}) == "PETG"

    def test_desde_el_perfil_padre(self):
        assert material_type(
            {"name": "Mi rollo raro", "inherits": "Creality Generic ASA @Base"}) == "ASA"

    def test_normaliza_el_plus(self):
        assert material_type({"filament_type": "PLA+"}) == "PLA"

    def test_desconocido(self):
        assert material_type({"name": "Rollo sin tipo"}) is None


class TestCasosLimite:
    def test_perfil_sin_ajustes_no_esta_calibrado(self):
        p = parse_orca_profile({
            "name": "Generic PLA @Hi", "type": "filament",
            "inherits": "Generic PLA", "filament_wipe": "nil",
        })
        assert not p.is_tuned

    def test_compatible_printers_como_lista_json(self):
        p = parse_orca_profile({
            "name": "X @Voron 0.6", "type": "filament",
            "compatible_printers": ["Voron 2.4 0.4 nozzle", "Voron 2.4 0.6 nozzle"],
        })
        assert p.printer_models == ["Voron 2.4"]

    def test_ignora_perfiles_que_no_son_de_filamento(self):
        assert parse_orca_profile({"name": "Mi impresora", "type": "machine"}) is None
        assert parse_orca_profile({"name": "0.20 estándar", "type": "process"}) is None

    def test_entrada_invalida(self):
        assert parse_orca_profile(None) is None
        assert parse_orca_profile([]) is None
        assert parse_orca_profile({"type": "filament"}) is None   # sin nombre

    def test_sin_compatible_printers_cae_a_los_alias_del_historial(self):
        # Perfil escrito a mano: lo único que lo ata a una máquina es el sufijo.
        p = parse_orca_profile({"name": "Elegoo PLA @K1SE 0.4 Nozzle", "type": "filament"})
        assert p.printer_models == []
        assert match_printer(p, IMPRESORAS).name == "Creality K1SE"

    def test_sin_pistas_no_se_inventa_impresora(self):
        p = parse_orca_profile({"name": "Elegoo PLA genérico", "type": "filament"})
        assert match_printer(p, IMPRESORAS) is None

    def test_no_empareja_por_una_sola_palabra_comun(self):
        # "Creality" a secas no basta: hay dos Creality en la lista.
        p = OrcaProfile(name="X", printer_models=["Creality Alguna Otra"])
        assert match_printer(p, IMPRESORAS) is None
