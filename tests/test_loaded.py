"""Tests de qué filamento está cargado en cada impresora.

Motor propio en un fichero temporal, sin recargar módulos: usa las clases ya
importadas, así no choca con otros tests que sí recargan ``app.models``.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Material, PrintJob, Printer
from app.services.loaded import resolve_slots, set_slots


@pytest.fixture
def Session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine)
    s = Local()
    s.add_all([
        Printer(name="Ender", host="10.0.0.1"),
        Printer(name="Creality Hi", host="10.0.0.2", multicolor=True, slot_count=4),
        Material(name="PLA Rojo", material_type="PLA", color_hex="#FF0000"),
        Material(name="PLA Azul", material_type="PLA", color_hex="#0000FF"),
    ])
    s.commit()
    yield s
    s.close()


def _p(s, name):
    return s.query(Printer).filter_by(name=name).one()


def _mat(s, name):
    return s.query(Material).filter_by(name=name).one()


class TestResolucion:
    def test_un_color_sin_nada_cae_al_ultimo_impreso(self, Session):
        s = Session
        pr, rojo = _p(s, "Ender"), _mat(s, "PLA Rojo")
        s.add(PrintJob(printer_id=pr.id, moonraker_job_id="J1", material_id=rojo.id,
                       end_time=datetime(2026, 1, 1, tzinfo=timezone.utc)))
        s.commit()
        slots = resolve_slots(s, pr)
        assert len(slots) == 1
        assert slots[0]["material"] == "PLA Rojo"
        assert slots[0]["source"] == "last-job"
        assert slots[0]["color_hex"] == "#FF0000"

    def test_toma_el_ultimo_no_uno_cualquiera(self, Session):
        s = Session
        pr, rojo, azul = _p(s, "Ender"), _mat(s, "PLA Rojo"), _mat(s, "PLA Azul")
        s.add(PrintJob(printer_id=pr.id, moonraker_job_id="J1", material_id=rojo.id,
                       end_time=datetime(2026, 1, 1, tzinfo=timezone.utc)))
        s.add(PrintJob(printer_id=pr.id, moonraker_job_id="J2", material_id=azul.id,
                       end_time=datetime(2026, 6, 1, tzinfo=timezone.utc)))
        s.commit()
        assert resolve_slots(s, pr)[0]["material"] == "PLA Azul"

    def test_lo_manual_manda_sobre_lo_impreso(self, Session):
        s = Session
        pr, rojo, azul = _p(s, "Ender"), _mat(s, "PLA Rojo"), _mat(s, "PLA Azul")
        s.add(PrintJob(printer_id=pr.id, moonraker_job_id="J1", material_id=rojo.id,
                       end_time=datetime(2026, 1, 1, tzinfo=timezone.utc)))
        set_slots(pr, [azul.id])
        s.commit()
        slots = resolve_slots(s, pr)
        assert slots[0]["material"] == "PLA Azul"
        assert slots[0]["source"] == "manual"

    def test_multicolor_da_un_hueco_por_bobina(self, Session):
        s = Session
        pr, rojo, azul = _p(s, "Creality Hi"), _mat(s, "PLA Rojo"), _mat(s, "PLA Azul")
        set_slots(pr, [rojo.id, None, azul.id, None])
        s.commit()
        slots = resolve_slots(s, pr)
        assert len(slots) == 4
        assert slots[0]["material"] == "PLA Rojo"
        assert slots[1]["source"] == "empty"
        assert slots[2]["material"] == "PLA Azul"
        assert slots[3]["source"] == "empty"

    def test_multicolor_no_deduce_del_ultimo(self, Session):
        # En multicolor no se sabe de una impresión qué había en cada ranura.
        s = Session
        pr, rojo = _p(s, "Creality Hi"), _mat(s, "PLA Rojo")
        s.add(PrintJob(printer_id=pr.id, moonraker_job_id="J9", material_id=rojo.id,
                       end_time=datetime(2026, 1, 1, tzinfo=timezone.utc)))
        s.commit()
        assert all(sl["source"] == "empty" for sl in resolve_slots(s, pr))

    def test_material_borrado_deja_el_hueco_vacio(self, Session):
        s = Session
        pr = _p(s, "Ender")
        set_slots(pr, [999])   # id inexistente
        s.commit()
        assert resolve_slots(s, pr)[0]["source"] == "empty"


class TestSetSlots:
    def test_recorta_a_la_longitud_de_huecos(self, Session):
        s = Session
        pr = _p(s, "Ender")   # slot_count = 1
        set_slots(pr, [1, 2, 3])
        assert pr.loaded_materials == [1]

    def test_rellena_con_none_hasta_completar(self, Session):
        s = Session
        pr = _p(s, "Creality Hi")   # 4 huecos
        set_slots(pr, [1])
        assert pr.loaded_materials == [1, None, None, None]

    def test_todo_vacio_se_guarda_como_null(self, Session):
        s = Session
        pr = _p(s, "Creality Hi")
        set_slots(pr, [None, None, None, None])
        assert pr.loaded_materials is None
