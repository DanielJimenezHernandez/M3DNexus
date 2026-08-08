"""Tests de las fotos de producto terminado (pedido/proyecto)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  (registra las tablas en Base.metadata)
from app.db import Base

PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
       "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    import app.db as dbmod
    orig = dbmod._engine
    dbmod._engine = engine
    dbmod.SessionLocal.configure(bind=engine)
    from fastapi.testclient import TestClient
    from app.main import app
    yield TestClient(app)
    dbmod._engine = orig
    dbmod.SessionLocal.configure(bind=orig)


def test_foto_de_proyecto_ciclo_completo(client):
    pid = client.post("/api/projects", json={"name": "P", "parts": []}).json()["id"]
    # sin fotos al inicio
    assert client.get(f"/api/entity-photos?entity_type=project&entity_id={pid}").json() == []
    # subir
    r = client.post("/api/entity-photos",
                    json={"entity_type": "project", "entity_id": pid, "data_uri": PNG})
    assert r.status_code == 201
    photo_id = r.json()["id"]
    # aparece en la lista y en el proyecto (photo_url)
    assert len(client.get(f"/api/entity-photos?entity_type=project&entity_id={pid}").json()) == 1
    assert client.get("/api/projects").json()[0]["photo_url"] == f"/api/entity-photos/{photo_id}"
    # se sirve como imagen
    img = client.get(f"/api/entity-photos/{photo_id}")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    # borrar
    assert client.delete(f"/api/entity-photos/{photo_id}").status_code == 204
    assert client.get("/api/projects").json()[0]["photo_url"] is None


def test_foto_entity_type_invalido(client):
    r = client.post("/api/entity-photos",
                    json={"entity_type": "material", "entity_id": 1, "data_uri": PNG})
    assert r.status_code == 422


def test_foto_entidad_inexistente(client):
    r = client.post("/api/entity-photos",
                    json={"entity_type": "order", "entity_id": 99999, "data_uri": PNG})
    assert r.status_code == 404
