"""Tests del factor térmico por material y su uso en avg_power_w.

Cuando no hay energía medida de un material, su consumo se estima escalando la
media disponible por un factor según sus temperaturas (cama/boquilla). Así PETG
y ABS no salen iguales que PLA hasta que se midan.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Material, PrintJob, Printer
from app.services.estimate import avg_power_w, material_power_factor

WHEN = datetime(2026, 7, 1, tzinfo=timezone.utc)


def test_factor_pla_es_uno():
    assert material_power_factor("PLA") == 1.0
    assert material_power_factor(None) == 1.0
    assert material_power_factor("desconocido") == 1.0  # tipo no tabulado → base


def test_factor_crece_con_la_temperatura():
    fpetg = material_power_factor("PETG")
    fabs = material_power_factor("ABS")
    assert fpetg == pytest.approx(1.27, abs=0.02)
    assert fabs == pytest.approx(1.54, abs=0.02)
    assert material_power_factor("ASA") == fabs        # mismas temps que ABS
    assert 1.0 < fpetg < fabs                          # PLA < PETG < ABS
    assert material_power_factor("TPU") < 1.0          # cama más fría que PLA


@pytest.fixture
def Session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    pla = Material(name="PLA gen", material_type="PLA", price_per_kg=300)
    s.add_all([pla, Printer(name="Hi", host="1.1.1.1"),
               Printer(name="Voron", host="2.2.2.2")])
    s.commit()
    # 100 W medidos de PLA en la Hi (0.1 kWh en 1 h).
    hi = s.query(Printer).filter_by(name="Hi").one()
    s.add(PrintJob(printer_id=hi.id, moonraker_job_id="1", material_id=pla.id,
                   status="completed", print_duration_s=3600, energy_kwh=0.1,
                   start_time=WHEN - timedelta(hours=1), end_time=WHEN))
    s.commit()
    yield s
    s.close()


def test_pla_medido_se_usa_tal_cual(Session):
    s = Session
    hi = s.query(Printer).filter_by(name="Hi").one()
    pw, src = avg_power_w(s, hi.id, "PLA")
    assert pw == pytest.approx(100.0, abs=1.0)
    assert src == "impresora+tipo"


def test_petg_sin_datos_escala_por_temperatura(Session):
    s = Session
    hi = s.query(Printer).filter_by(name="Hi").one()
    pw, src = avg_power_w(s, hi.id, "PETG")
    assert pw == pytest.approx(100.0 * 1.273, abs=1.0)   # ~127 W, no 100
    assert src == "impresora×térmico"


def test_abs_usa_estimado_termico_de_su_propia_base(Session):
    # Aunque haya ABS medido en OTRA máquina (Voron), la Hi —que tiene base
    # propia— lo estima desde SU consumo × factor, no toma prestada la medida.
    s = Session
    absm = Material(name="ABS gen", material_type="ABS", price_per_kg=350)
    s.add(absm); s.commit()
    voron = s.query(Printer).filter_by(name="Voron").one()
    s.add(PrintJob(printer_id=voron.id, moonraker_job_id="2", material_id=absm.id,
                   status="completed", print_duration_s=3600, energy_kwh=0.25,
                   start_time=WHEN - timedelta(hours=1), end_time=WHEN))
    s.commit()
    hi = s.query(Printer).filter_by(name="Hi").one()
    pw, src = avg_power_w(s, hi.id, "ABS")
    assert pw == pytest.approx(100.0 * 1.536, abs=1.0)   # ~154 W, NO los 250 de la Voron
    assert src == "impresora×térmico"


def test_impresora_sin_base_usa_medida_de_tipo_de_otra(Session):
    # Una impresora SIN nada medido sí toma la media del tipo de otras máquinas
    # (dato real del material) antes que caer al valor por defecto.
    s = Session
    absm = Material(name="ABS gen", material_type="ABS", price_per_kg=350)
    s.add(absm); s.commit()
    voron = s.query(Printer).filter_by(name="Voron").one()
    s.add(PrintJob(printer_id=voron.id, moonraker_job_id="2", material_id=absm.id,
                   status="completed", print_duration_s=3600, energy_kwh=0.25,
                   start_time=WHEN - timedelta(hours=1), end_time=WHEN))
    s.commit()
    nueva = Printer(name="Recien", host="9.9.9.9")   # sin jobs → sin base propia
    s.add(nueva); s.commit()
    pw, src = avg_power_w(s, nueva.id, "ABS")
    assert pw == pytest.approx(250.0, abs=1.0)   # media de tipo de la Voron
    assert src == "tipo"


def test_sin_ningun_dato_usa_defecto_por_temperatura(Session):
    s = Session
    voron = s.query(Printer).filter_by(name="Voron").one()   # sin jobs
    pw, src = avg_power_w(s, voron.id, "PETG")
    assert pw == pytest.approx(100.0 * 1.273, abs=1.0)
    assert src == "defecto×térmico"
