"""Tests del préstamo de energía entre impresoras.

Una impresora sin sensor propio (``ha_energy_entity`` vacío) pero con
``power_ref_printer_id`` estima su energía con la potencia media de la de
referencia, en vez de cobrar $0 de luz. El job se marca ``energy_estimated``.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.integrations.moonraker import MoonrakerJob
from app.models import Material, PrintJob, Printer
from app.services.ingest import ingest_job

MARCA = datetime(2026, 5, 25, tzinfo=timezone.utc)
RECIENTE = datetime(2026, 7, 24, tzinfo=timezone.utc)


@pytest.fixture
def Session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine)
    s = Local()
    pla = Material(name="PLA genérico", material_type="PLA", price_per_kg=300)
    # Referencia CON sensor y con historial medido del que sacar la potencia.
    ref = Printer(name="Creality Hi", host="10.0.0.1",
                  ha_energy_entity="sensor.hi_energy")
    s.add_all([pla, ref])
    s.commit()
    # Un job medido en la referencia: 0.5 kWh en 1 h → 500 W de media.
    s.add(PrintJob(
        printer_id=ref.id, moonraker_job_id="1", material_id=pla.id,
        status="completed", print_duration_s=3600, energy_kwh=0.5,
        start_time=RECIENTE - timedelta(hours=1), end_time=RECIENTE,
    ))
    # Gemela SIN sensor que toma prestada la potencia de la referencia.
    s.add(Printer(name="Creality Hi 2", host="10.0.0.2",
                  ha_energy_entity=None, power_ref_printer_id=ref.id))
    s.commit()
    yield s
    s.close()


def _job(job_id, printer, hours=2.0):
    end = RECIENTE + timedelta(days=1)
    return MoonrakerJob(
        job_id=job_id, filename="pieza.gcode", status="completed",
        start_time=end - timedelta(hours=hours), end_time=end,
        print_duration_s=hours * 3600, total_duration_s=hours * 3600,
        filament_used_mm=1000, filament_weight_g=25.0, filament_type="PLA",
        metadata={"filament_name": "PLA genérico", "filament_type": "PLA"},
    )


def _borrower(s):
    return s.query(Printer).filter_by(name="Creality Hi 2").one()


def test_impresora_sin_sensor_estima_desde_la_referencia(Session):
    s = Session
    b = _borrower(s)
    rec = ingest_job(s, b, _job("50", b, hours=2.0), ha=None,
                     price_per_kwh=4.0, currency="$", autocreate_since=MARCA)
    # 500 W × 2 h = 1.0 kWh estimado (no $0).
    assert rec.energy_estimated is True
    assert rec.energy_kwh == pytest.approx(1.0, abs=1e-3)
    # Y el coste de luz refleja la estimación: 1.0 kWh × 4.0 = 4.0.
    assert rec.cost_energy == pytest.approx(4.0, abs=1e-3)
    assert rec.energy_unavailable is False


def test_no_pisa_energia_ya_medida(Session):
    # Si el job ya trae energía medida, el préstamo no la toca.
    s = Session
    b = _borrower(s)
    from app.services.ingest import _borrow_energy
    rec = PrintJob(printer_id=b.id, moonraker_job_id="x",
                   print_duration_s=7200, energy_kwh=0.33)
    _borrow_energy(s, rec, b, None)
    assert rec.energy_kwh == 0.33
    assert not rec.energy_estimated   # None/False en memoria: no se estimó


def test_impresora_con_sensor_no_toma_prestado(Session):
    # La referencia SÍ tiene sensor: aunque no midamos (ha=None), no pide prestado.
    s = Session
    ref = s.query(Printer).filter_by(name="Creality Hi").one()
    from app.services.ingest import _borrow_energy
    rec = PrintJob(printer_id=ref.id, moonraker_job_id="y", print_duration_s=3600)
    _borrow_energy(s, rec, ref, None)
    assert rec.energy_kwh is None
    assert not rec.energy_estimated   # None/False en memoria: no se estimó


def test_energia_prestada_excluida_del_promedio(Session):
    # La potencia media se deriva SOLO de energía medida; la prestada (estimada)
    # no debe contaminar el promedio (evita que se derive de sí misma).
    s = Session
    pla = s.query(Material).filter_by(name="PLA genérico").one()
    b = _borrower(s)
    # Job PRESTADO con una potencia implícita disparatada (5 kWh en 1 h = 5000 W).
    s.add(PrintJob(printer_id=b.id, moonraker_job_id="est", material_id=pla.id,
                   status="completed", print_duration_s=3600, energy_kwh=5.0,
                   energy_estimated=True))
    s.commit()
    from app.services.estimate import _avg
    # Solo cuenta el job medido de la referencia: 0.5 kWh / 1 h = 500 W.
    assert _avg(s, printer_id=None, material_type="PLA") == pytest.approx(500.0, abs=1.0)


def test_estimada_se_reestima_pero_medida_no(Session):
    # Una estimación previa puede refrescarse; una medida real nunca se pisa.
    s = Session
    b = _borrower(s)
    pla = s.query(Material).filter_by(name="PLA genérico").one()
    from app.services.ingest import _borrow_energy
    # Estimada con un valor viejo raro → se recalcula a 500 W × 2 h = 1.0 kWh.
    rec = PrintJob(printer_id=b.id, moonraker_job_id="r", print_duration_s=7200,
                   energy_kwh=99.0, energy_estimated=True)
    _borrow_energy(s, rec, b, pla)
    assert rec.energy_kwh == pytest.approx(1.0, abs=1e-3)


def test_job_cero_segundos_queda_resuelto(Session):
    # Job terminado de 0 s en impresora que presta: nada que estimar, pero debe
    # quedar 'resuelto' (energy_unavailable) para no reprocesarse en cada sondeo.
    s = Session
    b = _borrower(s)
    from app.services.ingest import _borrow_energy, _is_settled
    rec = PrintJob(printer_id=b.id, moonraker_job_id="z", material_id=1,
                   print_duration_s=0)
    _borrow_energy(s, rec, b, None)
    assert rec.energy_kwh is None
    assert rec.energy_unavailable is True
    assert _is_settled(rec) is True
