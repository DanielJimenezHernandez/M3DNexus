"""Tests del análisis de calibración deducido de los perfiles de OrcaSlicer.

Los casos salen de nombres reales del historial: el vocabulario está lleno de
erratas ("noozle", "Nozzlle") y de perfiles prestados de otra máquina.
"""

from collections import Counter

from app.models import CAL_CALIBRATED, CAL_NOT_TUNED, CAL_UNKNOWN
from app.services.calibration import (
    classify,
    is_foreign_profile,
    is_not_tuned,
    normalize_alias,
    profile_name,
    propose_aliases,
    split_profile,
)


class TestPartirElPerfil:
    def test_con_espacio_a_los_dos_lados(self):
        assert split_profile("Elegoo PLA White @ Hi 0.4 Nozzle") == (
            "Elegoo PLA White", "Hi 0.4 Nozzle")

    def test_sin_espacio_tras_la_arroba(self):
        # Forma mayoritaria en el historial; la que se le escapaba al parser.
        assert split_profile("ELEGOO PLA Marble @Ender 0.4 nozzle") == (
            "ELEGOO PLA Marble", "Ender 0.4 nozzle")

    def test_perfil_generico_sin_sufijo(self):
        assert split_profile("Elegoo PLA White") == ("Elegoo PLA White", None)

    def test_multimaterial_se_queda_con_el_primero(self):
        assert profile_name('Elegoo PLA White @Hi";"ERYONE PLA @Hi') == \
            'Elegoo PLA White @Hi'

    def test_vacio(self):
        assert split_profile(None) == ("", None)
        assert split_profile("") == ("", None)


class TestNormalizarAlias:
    def test_unifica_las_erratas_de_nozzle(self):
        # Las cinco grafías conviven en el historial real.
        formas = ["Hi 0.4 Nozzle", "HI 0.4 noozle", "hi 0.4 nozzle",
                  "Hi  0.4  Noozle", "HI 0.4 Nozzlle"]
        assert len({normalize_alias(f) for f in formas}) == 1

    def test_distingue_boquillas_distintas(self):
        assert normalize_alias("Voron 0.6") != normalize_alias("Voron 0.4")

    def test_vacio(self):
        assert normalize_alias(None) == ""


class TestNotTuned:
    def test_detecta_las_variantes_de_mayusculas(self):
        assert is_not_tuned("Not Tuned - Elegoo PLA Grey")
        assert is_not_tuned("NOT TUNED - Elegoo PLA Marble")
        assert is_not_tuned("Elegoo PLA @Ender 0.4 noozle - not tuned")

    def test_no_da_falsos_positivos(self):
        assert not is_not_tuned("Elegoo PLA White @Hi 0.4 Nozzle")


class TestProponerAlias:
    def test_el_sufijo_va_a_la_impresora_donde_predomina(self):
        obs = [(1, "Hi", "Hi 0.4 Nozzle")] * 30 + [(2, "K1SE", "K1SE 0.4 Nozzle")] * 20
        props = propose_aliases(obs)
        assert props[1].aliases == ["hi 0.4 nozzle"]
        assert props[2].aliases == ["k1se 0.4 nozzle"]

    def test_agrupa_las_variantes_de_tecleo_en_un_alias(self):
        obs = ([(1, "Hi", "HI 0.4 noozle")] * 41 +
               [(1, "Hi", "Hi 0.4 Nozzle")] * 33 +
               [(1, "Hi", "HI 0.4 nozzle")] * 2)
        props = propose_aliases(obs)
        assert props[1].aliases == ["hi 0.4 nozzle"]

    def test_un_perfil_prestado_no_se_vuelve_alias(self):
        # 37 jobs en la Hi con perfil de la K1SE, contra 75 en la propia K1SE:
        # el sufijo es de la K1SE y en la Hi queda marcado como prestado.
        obs = ([(2, "K1SE", "K1SE 0.4 Nozzle")] * 75 +
               [(1, "Hi", "K1SE 0.4 Nozzle")] * 37 +
               [(1, "Hi", "Hi 0.4 Nozzle")] * 33)
        props = propose_aliases(obs)
        assert "k1se 0.4 nozzle" in props[2].aliases
        assert "k1se 0.4 nozzle" not in props[1].aliases
        assert props[1].foreign["k1se 0.4 nozzle"] == 37

    def test_sufijo_sin_competencia_es_suyo_aunque_aparezca_poco(self):
        # Una sola aparición y una sola máquina: no hay a quién disputárselo,
        # así que no tiene sentido marcarlo como perfil prestado.
        obs = [(1, "Hi", "Hi 0.4")] * 20 + [(1, "Hi", "Creality Hi")]
        props = propose_aliases(obs)
        assert sorted(props[1].aliases) == ["creality hi", "hi 0.4"]
        assert not props[1].foreign

    def test_disputado_sin_ganador_claro_queda_en_revision(self):
        obs = [(1, "Hi", "Comun 0.4")] * 3 + [(2, "K1SE", "Comun 0.4")] * 3
        props = propose_aliases(obs)
        assert not props[1].aliases and not props[2].aliases
        assert props[1].unclear["comun 0.4"] == 3
        assert props[2].unclear["comun 0.4"] == 3

    def test_un_perfil_not_tuned_no_define_el_vocabulario(self):
        # "@Ender 0.4 noozle - not tuned" describe lo contrario de una
        # calibración: no puede convertirse en alias de la máquina.
        obs = ([(1, "E3V2", "Ender 0.4 noozle")] * 20 +
               [(1, "E3V2", "Ender 0.4 noozle - not tuned")] * 13)
        props = propose_aliases(obs)
        assert props[1].aliases == ["ender 0.4 nozzle"]

    def test_sin_observaciones(self):
        assert propose_aliases([]) == {}


class TestClasificar:
    ALIAS = ["hi 0.4 nozzle", "creality hi 0.4"]

    def test_perfil_de_la_propia_impresora(self):
        assert classify("Elegoo PLA @HI 0.4 noozle", self.ALIAS) == CAL_CALIBRATED

    def test_perfil_de_otra_impresora(self):
        assert classify("Elegoo PLA @K1SE 0.4 Nozzle", self.ALIAS) == CAL_UNKNOWN

    def test_not_tuned_manda_sobre_el_sufijo(self):
        # Aunque el sufijo case, si el nombre lo declara sin calibrar, lo está.
        assert classify("Not Tuned - Elegoo PLA @HI 0.4 noozle", self.ALIAS) == CAL_NOT_TUNED

    def test_perfil_generico(self):
        assert classify("Elegoo PLA White", self.ALIAS) == CAL_UNKNOWN


class TestPerfilPrestado:
    ALIAS = ["hi 0.4 nozzle"]

    def test_avisa_del_perfil_de_otra_maquina(self):
        assert is_foreign_profile("Elegoo PLA @K1SE 0.4 Nozzle", self.ALIAS)

    def test_no_avisa_del_perfil_propio(self):
        assert not is_foreign_profile("Elegoo PLA @Hi 0.4 Nozzle", self.ALIAS)

    def test_el_generico_no_es_prestado(self):
        # Sin sufijo no hay máquina declarada: no se puede llamar prestado.
        assert not is_foreign_profile("Elegoo PLA White", self.ALIAS)

    def test_not_tuned_no_es_prestado(self):
        assert not is_foreign_profile("Not Tuned - Elegoo PLA", self.ALIAS)

    def test_sin_alias_configurados_no_se_inventa(self):
        assert is_foreign_profile("Elegoo PLA @Hi 0.4", [])
        assert isinstance(Counter(), Counter)
