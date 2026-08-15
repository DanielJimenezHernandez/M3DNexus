"""Tests del guardado y servido de miniaturas del gcode."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import PrintJob, Printer
from app.services.sync import THUMBNAIL_MAX_BYTES, store_thumbnail

META = {"thumbnails": [
    {"width": 300, "height": 300, "size": 10, "relative_path": ".thumbs/x-300x300.png"},
]}


class FakeClient:
    """Moonraker de mentira: devuelve unos bytes de miniatura y registra llamadas."""

    def __init__(self, data=b"PNGDATA"):
        self.data = data
        self.calls = []

    def thumbnail(self, path):
        self.calls.append(path)
        return (self.data, "image/png") if self.data is not None else (None, None)


def test_store_guarda_y_marca():
    rec = PrintJob(filename="d/x.gcode", raw_metadata=META)
    c = FakeClient(b"PNG")
    assert store_thumbnail(c, rec) is True
    assert rec.thumbnail == b"PNG"
    assert rec.has_thumbnail is True
    assert rec.thumbnail_tried is True
    assert c.calls == ["d/.thumbs/x-300x300.png"]   # ruta recompuesta


def test_store_sin_ruta_marca_tried_sin_pedir():
    rec = PrintJob(filename="x.gcode", raw_metadata={})   # sin thumbnails
    c = FakeClient(b"PNG")
    assert store_thumbnail(c, rec) is False
    assert rec.thumbnail_tried is True
    assert not rec.has_thumbnail
    assert c.calls == []   # ni siquiera pidió (no había ruta)


def test_store_rechaza_miniatura_enorme():
    rec = PrintJob(filename="x.gcode", raw_metadata=META)
    c = FakeClient(b"x" * (THUMBNAIL_MAX_BYTES + 1))
    assert store_thumbnail(c, rec) is False
    assert rec.thumbnail_tried is True
    assert not rec.has_thumbnail
    assert rec.thumbnail is None


def test_store_no_reintenta_si_ya_intento():
    rec = PrintJob(filename="x.gcode", raw_metadata=META, thumbnail_tried=True)
    c = FakeClient(b"PNG")
    assert store_thumbnail(c, rec) is False
    assert c.calls == []   # ya se intentó: no vuelve a pedir


def test_store_no_200_marca_tried():
    # La impresora respondió pero sin miniatura (p.ej. gcode borrado): permanente.
    rec = PrintJob(filename="x.gcode", raw_metadata=META)
    c = FakeClient(None)   # thumbnail() devuelve (None, None)
    assert store_thumbnail(c, rec) is False
    assert rec.thumbnail_tried is True
    assert not rec.has_thumbnail


def test_store_impresora_caida_no_marca_tried():
    # Error de transporte (apagada/sin red): transitorio, NO se marca, se reintenta.
    class RaisingClient:
        def thumbnail(self, path):
            raise RuntimeError("connection refused")

    rec = PrintJob(filename="x.gcode", raw_metadata=META)
    assert store_thumbnail(RaisingClient(), rec) is False
    assert not rec.thumbnail_tried
    assert not rec.has_thumbnail


@pytest.fixture
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine)
    with Local() as s:
        s.add(Printer(id=1, name="Voron", host="10.0.0.1"))
        s.add(PrintJob(id=1, printer_id=1, moonraker_job_id="1", filename="a.gcode",
                       status="completed", thumbnail=b"\x89PNG-bytes", has_thumbnail=True,
                       start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                       end_time=datetime(2026, 1, 2, tzinfo=timezone.utc)))
        s.add(PrintJob(id=2, printer_id=1, moonraker_job_id="2", filename="b.gcode",
                       status="completed",
                       start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                       end_time=datetime(2026, 1, 2, tzinfo=timezone.utc)))
        s.commit()
    import app.db as dbmod
    orig = dbmod._engine
    dbmod._engine = engine
    dbmod.SessionLocal.configure(bind=engine)
    from fastapi.testclient import TestClient
    from app.main import app
    yield TestClient(app)
    dbmod._engine = orig
    dbmod.SessionLocal.configure(bind=orig)


def test_endpoint_sirve_la_miniatura(env):
    r = env.get("/api/jobs/1/thumbnail")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == b"\x89PNG-bytes"


def test_endpoint_404_sin_miniatura(env):
    assert env.get("/api/jobs/2/thumbnail").status_code == 404


def test_jobout_incluye_has_thumbnail(env):
    jobs = env.get("/api/jobs").json()
    by_id = {j["id"]: j for j in jobs}
    assert by_id[1]["has_thumbnail"] is True
    assert by_id[2]["has_thumbnail"] is False


def test_review_endpoint(env):
    # Califica con nota; luego la quita con rating 0.
    r = env.post("/api/jobs/1/review", json={"rating": 4, "notes": "Primera capa perfecta,\nun poco de stringing."})
    assert r.status_code == 200
    assert r.json()["rating"] == 4
    assert "stringing" in r.json()["review_notes"]
    r2 = env.post("/api/jobs/1/review", json={"rating": 0, "notes": ""})
    assert r2.json()["rating"] is None
    assert r2.json()["review_notes"] is None
    assert env.post("/api/jobs/1/review", json={"rating": 9}).status_code == 422
    assert env.post("/api/jobs/99999/review", json={"rating": 3}).status_code == 404
