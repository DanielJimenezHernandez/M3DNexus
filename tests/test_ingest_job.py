"""Tests de ingest_job: resolución/creación del material al ingerir.

Motor propio en fichero temporal, sin recargar módulos (usa las clases ya
importadas), para no chocar con otros tests que sí recargan ``app.models``.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Material, Printer
from app.integrations.moonraker import MoonrakerJob
from app.services.ingest import ingest_job

MARCA = datetime(2026, 5, 25, tzinfo=timezone.utc)   # autocreate_since
RECIENTE = datetime(2026, 7, 24, tzinfo=timezone.utc)
ANTIGUO = datetime(2025, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def Session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine)
    s = Local()
    s.add_all([
        Printer(name="Creality Hi", host="10.0.0.1"),
        # Genérico por tipo, al que caía el fallback.
        Material(name="PLA genérico", material_type="PLA", price_per_kg=300),
    ])
    s.commit()
    yield s
    s.close()


def _job(job_id, name, end=RECIENTE, status="completed"):
    return MoonrakerJob(
        job_id=job_id, filename="pieza.gcode", status=status,
        start_time=end - timedelta(hours=1), end_time=end,
        print_duration_s=3600, total_duration_s=3600,
        filament_used_mm=1000, filament_weight_g=25.0, filament_type="PLA",
        metadata={"filament_name": name, "filament_type": "PLA"},
    )


def _ingest(s, job):
    pr = s.query(Printer).one()
    return ingest_job(s, pr, job, ha=None, price_per_kwh=3.0, currency="$",
                      autocreate_since=MARCA)


def _mat(s, rec):
    return s.get(Material, rec.material_id)


class TestCreacionDeFilamento:
    def test_impresion_reciente_crea_el_filamento_nombrado(self, Session):
        s = Session
        rec = _ingest(s, _job("109", "Elegoo PLA+ Red @ Creality Hi 0.4 Nozzle"))
        assert _mat(s, rec).name == "Elegoo PLA+ Red"   # no "PLA genérico"

    def test_reusa_el_material_si_ya_existe(self, Session):
        s = Session
        s.add(Material(name="Elegoo PLA+ White", material_type="PLA", price_per_kg=500))
        s.commit()
        rec = _ingest(s, _job("1", "Elegoo PLA+ White @ K1SE 0.4 Nozzle"))
        assert _mat(s, rec).name == "Elegoo PLA+ White"
        # No se ha duplicado.
        assert s.query(Material).filter_by(name="Elegoo PLA+ White").count() == 1

    def test_back_catalogo_no_crea_material(self, Session):
        # Una impresión anterior a la marca cae al genérico, para no llenar la BD.
        s = Session
        rec = _ingest(s, _job("5", "Marca Rara PLA Exotico @ Hi", end=ANTIGUO))
        assert _mat(s, rec).name == "PLA genérico"
        assert s.query(Material).filter_by(name="Marca Rara PLA Exotico").count() == 0


class TestColisionDeJobId:
    """El historial de Moonraker se reinicia y un job_id viejo se reutiliza."""

    def test_reingesta_corrige_un_material_generico(self, Session):
        s = Session
        # Estado heredado del bug: el registro quedó en genérico y CON energía
        # medida, así que estaba "settled" y no se volvía a mirar.
        rec = _ingest(s, _job("109", "Elegoo PLA+ Red @ Creality Hi 0.4 Nozzle"))
        rec.material_id = s.query(Material).filter_by(name="PLA genérico").one().id
        rec.energy_kwh = 0.05
        s.commit()
        assert _mat(s, rec).name == "PLA genérico"

        # Al volver a sincronizar (mismo job_id, misma metadata) se corrige,
        # aunque estuviera settled: el material concreto difiere del guardado.
        rec2 = _ingest(s, _job("109", "Elegoo PLA+ Red @ Creality Hi 0.4 Nozzle"))
        assert rec2.id == rec.id                        # el mismo registro
        assert _mat(s, rec2).name == "Elegoo PLA+ Red"  # ya no genérico

    def test_job_ya_correcto_y_settled_no_se_reprocesa(self, Session):
        s = Session
        rec = _ingest(s, _job("109", "Elegoo PLA+ Red @ Creality Hi 0.4 Nozzle"))
        rec.energy_kwh = 0.05   # con energía → settled
        s.commit()
        computed_1 = rec.computed_at
        # Segunda pasada idéntica: settled y material correcto, no se recomputa.
        rec2 = _ingest(s, _job("109", "Elegoo PLA+ Red @ Creality Hi 0.4 Nozzle"))
        assert rec2.computed_at == computed_1

    def test_mismo_jobid_distinto_inicio_son_dos_registros(self, Session):
        # El historial se reinició: el job 109 se reutiliza para otra impresión
        # posterior. No debe pisar la primera, sino crear un registro nuevo.
        s = Session
        j1 = _job("109", "Elegoo PLA+ Red @ Hi", end=RECIENTE)
        j2 = _job("109", "Elegoo PLA+ White @ Hi",
                  end=RECIENTE + timedelta(days=1))
        r1 = _ingest(s, j1)
        r2 = _ingest(s, j2)
        s.commit()
        assert r1.id != r2.id                       # dos registros distintos
        assert _mat(s, r1).name == "Elegoo PLA+ Red"
        assert _mat(s, r2).name == "Elegoo PLA+ White"
        assert s.query(type(r1)).count() == 2

    def test_misma_impresion_resincronizada_no_se_duplica(self, Session):
        # Mismo job_id y mismo start_time: es la MISMA impresión, no se duplica.
        s = Session
        j = _job("109", "Elegoo PLA+ Red @ Hi", end=RECIENTE)
        r1 = _ingest(s, j)
        r2 = _ingest(s, _job("109", "Elegoo PLA+ Red @ Hi", end=RECIENTE))
        s.commit()
        assert r1.id == r2.id
        assert s.query(type(r1)).count() == 1
