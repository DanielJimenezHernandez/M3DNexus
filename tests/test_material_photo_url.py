"""La lista de materiales expone la URL de la primera foto de bobina."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Material, MaterialPhoto


@pytest.fixture
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine)
    with Local() as s:
        s.add(Material(id=1, name="Con foto", material_type="PLA"))
        s.add(Material(id=2, name="Sin foto", material_type="PETG"))
        # Dos fotos de bobina en el id 1: debe devolver la de menor id.
        s.add(MaterialPhoto(id=10, material_id=1, kind="spool", data=b"a"))
        s.add(MaterialPhoto(id=11, material_id=1, kind="spool", data=b"b"))
        s.add(MaterialPhoto(id=12, material_id=1, kind="color", data=b"c"))
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


def test_photo_url_de_la_primera_bobina(env):
    mats = {m["id"]: m for m in env.get("/api/materials").json()}
    assert mats[1]["photo_url"] == "/api/materials/1/photos/10"  # menor id spool
    assert mats[2]["photo_url"] is None                          # sin fotos
