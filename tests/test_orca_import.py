"""Tests del importador de perfiles de Orca a la matriz de calibración."""

import pytest

from app.services.orca_import import SIMILAR_MIN, match_material


class MaterialFalso:
    def __init__(self, id, name):
        self.id = id
        self.name = name


MATERIALES = [
    MaterialFalso(1, "Elegoo PLA+ White"),
    MaterialFalso(2, "Elegoo PLA Purple"),
    MaterialFalso(3, "SUNLU PETG Black"),
    MaterialFalso(4, "PLA genérico"),
    MaterialFalso(5, "Elegoo Black ABS @K1SE 0.4 Nozzle"),
]


class TestEmparejarMaterial:
    def test_exacto(self):
        m, s = match_material("Elegoo PLA Purple", MATERIALES)
        assert m.id == 2 and s == 1.0

    def test_ignora_mayusculas_y_puntuacion(self):
        m, s = match_material("elegoo  pla+   WHITE", MATERIALES)
        assert m.id == 1 and s == 1.0

    def test_rescata_la_errata_de_tecleo(self):
        # "Whithe" existe en la BD real; sin similitud crearía un duplicado.
        m, s = match_material("Elegoo PLA+ Whithe", MATERIALES)
        assert m.id == 1
        assert s >= SIMILAR_MIN

    def test_un_filamento_nuevo_no_se_fuerza_a_ninguno(self):
        m, s = match_material("Polymaker PolyTerra Army Green", MATERIALES)
        assert m is None

    def test_no_confunde_colores_distintos_de_la_misma_marca(self):
        # El riesgo real de fusionar por parecido: mismo prefijo, otro color.
        m, _ = match_material("Elegoo PLA Mint", MATERIALES)
        assert m is None or m.name != "Elegoo PLA Purple"

    def test_nombre_vacio(self):
        assert match_material("", MATERIALES) == (None, 0.0)
        assert match_material(None, MATERIALES) == (None, 0.0)

    def test_sin_materiales(self):
        assert match_material("Elegoo PLA White", []) == (None, 0.0)


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    """App con BD limpia: el engine se crea al importar, así que va antes."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("CONFIG_PATH", "config.example.yaml")
    monkeypatch.setenv("HA_TOKEN", "")
    import importlib

    from app import config
    config.get_config.cache_clear()
    from app import db as dbmod
    importlib.reload(dbmod)
    from app import models
    importlib.reload(models)
    from app.services import orca_import
    importlib.reload(orca_import)

    dbmod.init_db()
    with dbmod.session_scope() as s:
        s.add(models.Printer(name="Creality K1SE", host="10.0.0.2"))
        s.add(models.Material(name="Elegoo PLA Mint", material_type="PLA"))
    yield dbmod, models, orca_import
    config.get_config.cache_clear()


PERFIL = {
    "name": "Elegoo PLA Mint @ K1SE 0.4 Nozzle",
    "type": "filament",
    "inherits": "Generic PLA @System",
    "filament_vendor": "Elegoo",
    "pressure_advance": "0.045",
    "filament_flow_ratio": "1.01",
    "nozzle_temperature": "205",
    "hot_plate_temp": "50",
    "enable_pressure_advance": "1",
    "default_filament_colour": "#D7FDB5",
    "compatible_printers": '"Creality K1 SE 0.4 nozzle"',
}


class TestPlanYAplicacion:
    def test_el_plan_no_escribe_nada(self, app_db):
        dbmod, models, oi = app_db
        with dbmod.session_scope() as s:
            antes = s.query(models.FilamentCalibration).count()
            plan = oi.plan_import(s, [PERFIL])
            assert s.query(models.FilamentCalibration).count() == antes

        fila = plan[0]
        assert fila["printer_name"] == "Creality K1SE"
        assert fila["material_name"] == "Elegoo PLA Mint"
        assert fila["match"] == "exact"
        assert fila["level"] == "FULL"
        assert fila["pressure_advance"] == 0.045
        assert fila["nozzle_mm"] == 0.4
        assert not fila["warnings"]

    def test_aplicar_crea_la_calibracion(self, app_db):
        dbmod, models, oi = app_db
        with dbmod.session_scope() as s:
            plan = oi.plan_import(s, [PERFIL])
            d = dict(plan[0]); d["profile_json"] = PERFIL
            resumen = oi.apply_import(s, [d])
            assert resumen["calibraciones_nuevas"] == 1

        with dbmod.session_scope() as s:
            cal = s.query(models.FilamentCalibration).one()
            assert cal.status == "FULL"
            assert cal.pressure_advance == 0.045
            assert cal.flow_ratio == 1.01
            assert cal.nozzle_mm == 0.4
            assert cal.source == "orca"
            # El color de Orca rellena el hueco del material existente.
            assert s.get(models.Material, cal.material_id).color_hex == "#D7FDB5"

    def test_reimportar_actualiza_en_vez_de_duplicar(self, app_db):
        dbmod, models, oi = app_db
        with dbmod.session_scope() as s:
            d = dict(oi.plan_import(s, [PERFIL])[0]); d["profile_json"] = PERFIL
            oi.apply_import(s, [d])
        with dbmod.session_scope() as s:
            d = dict(oi.plan_import(s, [PERFIL])[0]); d["profile_json"] = PERFIL
            resumen = oi.apply_import(s, [d])
            assert resumen["calibraciones_actualizadas"] == 1
            assert resumen["calibraciones_nuevas"] == 0
        with dbmod.session_scope() as s:
            assert s.query(models.FilamentCalibration).count() == 1

    def test_no_se_inventa_el_precio_del_material(self, app_db):
        # Orca no sabe lo que pagaste: el precio no se toca al importar.
        dbmod, models, oi = app_db
        with dbmod.session_scope() as s:
            m = s.query(models.Material).filter_by(name="Elegoo PLA Mint").one()
            m.price_per_kg = 480.0
        with dbmod.session_scope() as s:
            d = dict(oi.plan_import(s, [PERFIL])[0]); d["profile_json"] = PERFIL
            oi.apply_import(s, [d])
        with dbmod.session_scope() as s:
            assert s.query(models.Material).filter_by(
                name="Elegoo PLA Mint").one().price_per_kg == 480.0

    def test_sin_impresora_se_omite(self, app_db):
        dbmod, models, oi = app_db
        with dbmod.session_scope() as s:
            resumen = oi.apply_import(
                s, [{"profile_json": PERFIL, "printer_id": None, "nozzle_mm": 0.4}])
            assert resumen["omitidas"] == 1
            assert s.query(models.FilamentCalibration).count() == 0

    def test_perfil_sin_boquilla_avisa(self, app_db):
        dbmod, models, oi = app_db
        perfil = dict(PERFIL, name="Elegoo PLA Mint @ K1SE")
        with dbmod.session_scope() as s:
            fila = oi.plan_import(s, [perfil])[0]
            assert fila["nozzle_mm"] is None
            assert any("boquilla" in w for w in fila["warnings"])

    def test_material_nuevo_se_crea_con_marca_y_color(self, app_db):
        dbmod, models, oi = app_db
        perfil = dict(PERFIL, name="Polymaker PETG Army Green @ K1SE 0.4 Nozzle")
        with dbmod.session_scope() as s:
            fila = oi.plan_import(s, [perfil])[0]
            assert fila["match"] == "new"
            d = dict(fila); d["profile_json"] = perfil
            assert oi.apply_import(s, [d])["materiales_nuevos"] == 1
        with dbmod.session_scope() as s:
            m = s.query(models.Material).filter_by(
                name="Polymaker PETG Army Green").one()
            assert m.brand == "Elegoo"        # filament_vendor del perfil
            assert m.color_hex == "#D7FDB5"
            assert m.price_per_kg == 0.0


class TestBorradoDeMaterial:
    """Borrar un material no debe dejar referencias colgando (SQLite no las fuerza)."""

    def test_borrar_desliga_jobs_y_borra_calibraciones(self, app_db):
        dbmod, models, oi = app_db
        # Importa una calibración y crea un job que usa ese material.
        with dbmod.session_scope() as s:
            d = dict(oi.plan_import(s, [PERFIL])[0]); d["profile_json"] = PERFIL
            oi.apply_import(s, [d])
            cal = s.query(models.FilamentCalibration).one()
            mid, pid = cal.material_id, cal.printer_id
            s.add(models.PrintJob(printer_id=pid, moonraker_job_id="J1",
                                  material_id=mid, cost_filament=5.0))

        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            assert c.delete(f"/api/materials/{mid}").status_code == 204

        with dbmod.session_scope() as s:
            # El job sigue, pero desligado y sin coste de filamento fantasma.
            job = s.query(models.PrintJob).filter_by(moonraker_job_id="J1").one()
            assert job.material_id is None
            assert job.cost_filament == 0.0
            # Y la calibración de ese material desaparece, no queda huérfana.
            assert s.query(models.FilamentCalibration).count() == 0


class TestMismoFilamentoVariasImpresoras:
    """El caso central: un rollo calibrado en dos máquinas son dos perfiles."""

    def test_dos_perfiles_nuevos_del_mismo_filamento_no_chocan(self, app_db):
        dbmod, models, oi = app_db
        with dbmod.session_scope() as s:
            s.add(models.Printer(name="Creality Hi", host="10.0.0.3"))

        # Mismo filamento, dos impresoras: ambos se emparejan como material nuevo.
        base = {"type": "filament", "filament_vendor": "SUNLU",
                "filament_flow_ratio": "0.99", "default_filament_colour": "#FF8000"}
        p_hi = dict(base, name="SUNLU PETG Orange @ Hi 0.4 Nozzle",
                    compatible_printers='"Creality Hi 0.4 nozzle"')
        p_k1 = dict(base, name="SUNLU PETG Orange @ K1SE 0.4 Nozzle",
                    compatible_printers='"Creality K1 SE 0.4 nozzle"')

        with dbmod.session_scope() as s:
            plan = {f["profile"]: f for f in oi.plan_import(s, [p_hi, p_k1])}
            assert all(f["match"] == "new" for f in plan.values())
            decisiones = []
            for raw in (p_hi, p_k1):
                f = plan[raw["name"].strip('"')]
                d = dict(f); d["profile_json"] = raw
                decisiones.append(d)
            resumen = oi.apply_import(s, decisiones)

        with dbmod.session_scope() as s:
            # Un solo material, dos calibraciones (una por impresora).
            assert s.query(models.Material).filter_by(name="SUNLU PETG Orange").count() == 1
            assert resumen["materiales_nuevos"] == 1
            assert s.query(models.FilamentCalibration).count() == 2

    def test_dos_perfiles_a_la_misma_terna_no_chocan(self, app_db):
        # Autoflush=False: sin índice en memoria, la 2ª inserción de la misma
        # (material, impresora, boquilla) rompe la restricción UNIQUE al hacer flush.
        dbmod, models, oi = app_db
        p1 = dict(PERFIL, nozzle_temperature="205")
        p2 = dict(PERFIL, nozzle_temperature="215")   # mismo filamento/impresora/boquilla
        with dbmod.session_scope() as s:
            plan = oi.plan_import(s, [p1, p2])
            decis = []
            for f, raw in zip(plan, (p1, p2)):
                d = dict(f); d["profile_json"] = raw
                decis.append(d)
            resumen = oi.apply_import(s, decis)
            assert resumen["calibraciones_nuevas"] == 1
            assert resumen["calibraciones_actualizadas"] == 1
        with dbmod.session_scope() as s:
            cal = s.query(models.FilamentCalibration).one()   # una sola fila
            assert cal.nozzle_temp == 215                     # gana la última


class TestStockYCompra:
    """Nivel de bobina y enlace de compra: se rellenan a mano por ahora."""

    def test_valida_el_nivel_de_stock(self):
        from pydantic import ValidationError
        from app.schemas import MaterialIn
        assert MaterialIn(name="X", stock_level="low").stock_level == "low"
        with pytest.raises(ValidationError):
            MaterialIn(name="X", stock_level="mediollena")

    def test_valida_el_enlace_de_compra(self):
        # El enlace se pinta como <a href>: un javascript: sería ejecutable.
        from pydantic import ValidationError
        from app.schemas import MaterialIn
        assert MaterialIn(name="X", purchase_url="https://t.com/a").purchase_url == "https://t.com/a"
        assert MaterialIn(name="X", purchase_url="").purchase_url is None
        for malo in ("javascript:alert(1)", "data:text/html,x", "tienda.com"):
            with pytest.raises(ValidationError):
                MaterialIn(name="X", purchase_url=malo)

    def test_persiste_por_la_api(self, app_db):
        dbmod, models, oi = app_db
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            r = c.post("/api/materials", json={
                "name": "SUNLU PLA Rojo", "material_type": "PLA",
                "stock_level": "half", "purchase_url": "https://sunlu.com/rojo"})
            assert r.status_code == 201
            assert r.json()["stock_level"] == "half"
            assert r.json()["purchase_url"] == "https://sunlu.com/rojo"
        with dbmod.session_scope() as s:
            assert s.query(models.Material).filter_by(
                name="SUNLU PLA Rojo").one().stock_level == "half"

    def test_los_materiales_existentes_quedan_en_desconocido(self, app_db):
        dbmod, models, oi = app_db
        with dbmod.session_scope() as s:
            s.add(models.Material(name="Sin indicar", material_type="PLA"))
        with dbmod.session_scope() as s:
            assert s.query(models.Material).filter_by(
                name="Sin indicar").one().stock_level == "unknown"


# 1×1 PNG rojo, base64
_PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mP8z2D2HwAFEQIU3l3mFwAAAABJRU5ErkJggg==")


class TestFotosDeMaterial:
    def _client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    def _material(self, app_db):
        dbmod, models, _ = app_db
        with dbmod.session_scope() as s:
            return s.query(models.Material).filter_by(name="Elegoo PLA Mint").one().id

    def test_subir_listar_servir_borrar(self, app_db):
        mid = self._material(app_db)
        with self._client() as c:
            r = c.post(f"/api/materials/{mid}/photos", json={"kind": "spool", "data_uri": _PNG})
            assert r.status_code == 201
            pid = r.json()["id"]

            lst = c.get(f"/api/materials/{mid}/photos").json()
            assert len(lst) == 1 and lst[0]["kind"] == "spool"

            img = c.get(f"/api/materials/{mid}/photos/{pid}")
            assert img.status_code == 200
            assert img.headers["content-type"] == "image/png"
            assert len(img.content) > 0

            assert c.delete(f"/api/materials/{mid}/photos/{pid}").status_code == 204
            assert c.get(f"/api/materials/{mid}/photos").json() == []

    def test_limite_de_dos_bobinas(self, app_db):
        mid = self._material(app_db)
        with self._client() as c:
            for _ in range(2):
                assert c.post(f"/api/materials/{mid}/photos",
                              json={"kind": "spool", "data_uri": _PNG}).status_code == 201
            # La tercera se rechaza.
            assert c.post(f"/api/materials/{mid}/photos",
                          json={"kind": "spool", "data_uri": _PNG}).status_code == 409

    def test_limite_de_una_de_color(self, app_db):
        mid = self._material(app_db)
        with self._client() as c:
            assert c.post(f"/api/materials/{mid}/photos",
                          json={"kind": "color", "data_uri": _PNG}).status_code == 201
            assert c.post(f"/api/materials/{mid}/photos",
                          json={"kind": "color", "data_uri": _PNG}).status_code == 409
            # Pero un color no cuenta contra el límite de bobinas.
            assert c.post(f"/api/materials/{mid}/photos",
                          json={"kind": "spool", "data_uri": _PNG}).status_code == 201

    def test_rechaza_no_imagen_y_tipo_malo(self, app_db):
        mid = self._material(app_db)
        with self._client() as c:
            assert c.post(f"/api/materials/{mid}/photos",
                          json={"kind": "spool", "data_uri": "data:text/html;base64,PHNjcmlwdD4="}).status_code == 422
            assert c.post(f"/api/materials/{mid}/photos",
                          json={"kind": "portada", "data_uri": _PNG}).status_code == 422

    def test_borrar_material_arrastra_sus_fotos(self, app_db):
        dbmod, models, _ = app_db
        mid = self._material(app_db)
        with self._client() as c:
            c.post(f"/api/materials/{mid}/photos", json={"kind": "spool", "data_uri": _PNG})
            assert c.delete(f"/api/materials/{mid}").status_code == 204
        with dbmod.session_scope() as s:
            assert s.query(models.MaterialPhoto).count() == 0

    def test_color_hex_por_la_api(self, app_db):
        # El cuentagotas guarda el hex en el material.
        with self._client() as c:
            r = c.post("/api/materials", json={"name": "Con color", "color_hex": "#1A2B3C"})
            assert r.status_code == 201 and r.json()["color_hex"] == "#1A2B3C"
            assert c.post("/api/materials", json={"name": "Mal", "color_hex": "rojo"}).status_code == 422
