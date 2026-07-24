"""Tests de cuándo se vuelve a preguntar la energía a Home Assistant.

El bucle de sondeo reprocesa el historial cada minuto: lo que decide aquí si un
job se consulta o no es la diferencia entre unas pocas llamadas y cientos por
minuto contra HA, indefinidamente.
"""

from datetime import datetime, timedelta, timezone

from app.models import PrintJob, Printer
from app.services.ingest import _is_settled, _resolve_energy

NOW = datetime.now(timezone.utc)
CUTOFF = NOW - timedelta(days=10)


class FakeHA:
    """Cliente de HA que cuenta las llamadas y devuelve lo que se le diga."""

    def __init__(self, value=None):
        self.value = value
        self.calls = 0

    def energy_between(self, entity_id, start, end):
        self.calls += 1
        return self.value


def _printer(entity="sensor.energia"):
    return Printer(id=1, name="Voron", host="10.0.0.1", ha_energy_entity=entity)


def _job(end_time=None, start_time=None):
    class _J:
        pass

    j = _J()
    j.job_id = "42"
    j.start_time = start_time or (NOW - timedelta(hours=2))
    j.end_time = end_time or (NOW - timedelta(hours=1))
    return j


# --- _is_settled: qué merece reprocesarse ----------------------------------- #
def test_con_energia_y_material_esta_resuelto():
    assert _is_settled(PrintJob(energy_kwh=0.5, material_id=3))


def test_energia_irrecuperable_tambien_resuelve():
    # Sin esta rama, el job se reintenta en cada sondeo para siempre.
    assert _is_settled(PrintJob(energy_kwh=None, energy_unavailable=True, material_id=3))


def test_sin_energia_todavia_recuperable_no_resuelve():
    assert not _is_settled(PrintJob(energy_kwh=None, energy_unavailable=False, material_id=3))


def test_sin_material_no_resuelve():
    # El material puede aparecer si lo creas a mano, así que se sigue mirando.
    assert not _is_settled(PrintJob(energy_kwh=0.5, material_id=None))


# --- _resolve_energy: a quién se le pregunta -------------------------------- #
def test_job_reciente_consulta_y_guarda():
    rec, ha = PrintJob(), FakeHA(value=0.42)
    _resolve_energy(rec, _printer(), _job(), ha, 0, CUTOFF)
    assert ha.calls == 1
    assert rec.energy_kwh == 0.42
    assert not rec.energy_unavailable


def test_job_fuera_de_retencion_no_llama_a_ha():
    rec, ha = PrintJob(), FakeHA(value=0.42)
    viejo = _job(end_time=NOW - timedelta(days=60), start_time=NOW - timedelta(days=60))
    _resolve_energy(rec, _printer(), viejo, ha, 0, CUTOFF)
    assert ha.calls == 0            # HA ya purgó ese historial
    assert rec.energy_unavailable   # y no se volverá a intentar


def test_ha_sin_datos_marca_irrecuperable():
    # HA contestó pero su ventana está vacía: no va a aparecer más tarde.
    rec, ha = PrintJob(), FakeHA(value=None)
    _resolve_energy(rec, _printer(), _job(), ha, 0, CUTOFF)
    assert ha.calls == 1
    assert rec.energy_unavailable


def test_ha_caido_deja_pendiente_para_reintentar():
    # ha=None es "no responde ahora": distinto de "no existe el dato".
    rec = PrintJob()
    _resolve_energy(rec, _printer(), _job(), None, 0, CUTOFF)
    assert rec.energy_kwh is None
    assert not rec.energy_unavailable


def test_sin_entidad_configurada_no_marca_irrecuperable():
    # Falta configuración, no faltan datos: se resuelve poniendo la entidad.
    rec, ha = PrintJob(), FakeHA(value=0.42)
    _resolve_energy(rec, _printer(entity=None), _job(), ha, 0, CUTOFF)
    assert ha.calls == 0
    assert not rec.energy_unavailable


def test_no_se_repregunta_lo_ya_resuelto():
    rec, ha = PrintJob(energy_kwh=1.5), FakeHA(value=0.42)
    _resolve_energy(rec, _printer(), _job(), ha, 0, CUTOFF)
    assert ha.calls == 0
    assert rec.energy_kwh == 1.5


def test_el_padding_ensancha_la_ventana():
    """El margen debe aplicarse a ambos lados (pico de calentamiento y enfriado)."""
    ventanas = []

    class SpyHA(FakeHA):
        def energy_between(self, entity_id, start, end):
            ventanas.append((start, end))
            return 0.1

    job = _job()
    _resolve_energy(PrintJob(), _printer(), job, SpyHA(), 120, CUTOFF)
    start, end = ventanas[0]
    assert start == job.start_time - timedelta(seconds=120)
    assert end == job.end_time + timedelta(seconds=120)
