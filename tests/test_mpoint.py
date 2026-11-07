"""Terminal Mercado Pago Point: ajustes, cobro y mapeo de estados."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db import Base
from app.models import Setting
from app.services import mpoint


@pytest.fixture
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}")
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine)
    with Local() as s:
        s.add(Setting(key="electricity_price_per_kwh", value="4"))
        s.add(Setting(key="currency", value="$"))
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


def test_ajustes_token_enmascarado_y_sin_cambio(env):
    # Sin configurar: mp_configured False y /mp/charge pide el token.
    s = env.get("/api/settings").json()
    assert s["mp_configured"] is False
    assert env.post("/api/mp/charge", json={"amount": 100}).status_code == 400

    # Guardar token + device: el token NO regresa, solo su cola.
    s = env.put("/api/settings", json={
        "mp_access_token": "APP_USR-secreto-1234",
        "mp_device_id": "PAX_A910__X1", "mp_device_name": "PAX_A910__X1",
        "mp_print_receipt": False}).json()
    assert s["mp_configured"] is True and s["mp_token_tail"] == "1234"
    assert "APP_USR" not in str(s)
    # Un PUT sin el campo no toca el token; "" lo borra.
    s = env.put("/api/settings", json={"currency": "$"}).json()
    assert s["mp_configured"] is True
    s = env.put("/api/settings", json={"mp_access_token": ""}).json()
    assert s["mp_configured"] is False


def test_cobro_crea_intencion_en_centavos(env, monkeypatch):
    env.put("/api/settings", json={"mp_access_token": "tok",
                                   "mp_device_id": "DEV1"})
    visto = {}

    def fake_create(token, device, cents, reference, print_receipt):
        visto.update(token=token, device=device, cents=cents,
                     reference=reference, print_receipt=print_receipt)
        return {"id": "intent-1"}

    monkeypatch.setattr(mpoint, "create_intent", fake_create)
    r = env.post("/api/mp/charge", json={"amount": 150.0})
    assert r.status_code == 201
    assert r.json()["intent_id"] == "intent-1"
    assert visto["cents"] == 15000            # $150.00 → centavos sin decimales
    assert visto["device"] == "DEV1"
    assert visto["print_receipt"] is True      # default
    assert visto["reference"].startswith("m3dnexus-pos-")
    # El redondeo de flotantes no puede perder un centavo: 10.35 → 1035.
    env.post("/api/mp/charge", json={"amount": 10.35})
    assert visto["cents"] == 1035


def test_estado_finished_exige_pago_aprobado(env, monkeypatch):
    env.put("/api/settings", json={"mp_access_token": "tok",
                                   "mp_device_id": "DEV1"})
    intents = {"i1": {"state": "ON_TERMINAL"},
               "i2": {"state": "FINISHED", "payment": {"id": 777}},
               "i3": {"state": "FINISHED", "payment": {"id": 778}},
               "i4": {"state": "CANCELED"},
               "i5": {"state": "FINISHED"}}
    pagos = {"777": {"status": "approved", "status_detail": "accredited",
                     "card": {"last_four_digits": "4321"}},
             "778": {"status": "rejected", "status_detail": "cc_rejected_other_reason"}}
    monkeypatch.setattr(mpoint, "get_intent", lambda t, i: intents[i])
    monkeypatch.setattr(mpoint, "get_payment", lambda t, p: pagos[p])

    assert env.get("/api/mp/charge/i1").json()["approved"] is None   # sigue en curso
    ok = env.get("/api/mp/charge/i2").json()
    assert ok["approved"] is True and ok["card_last4"] == "4321"
    bad = env.get("/api/mp/charge/i3").json()
    assert bad["approved"] is False and "cc_rejected" in bad["detail"]
    assert env.get("/api/mp/charge/i4").json()["approved"] is False
    # FINISHED sin pago nunca se da por cobrado.
    assert env.get("/api/mp/charge/i5").json()["approved"] is False


def test_cancelar_y_errores_de_mp(env, monkeypatch):
    env.put("/api/settings", json={"mp_access_token": "tok",
                                   "mp_device_id": "DEV1"})

    def boom(*a, **k):
        raise mpoint.MPointError("Mercado Pago respondió 409: ya en terminal")

    monkeypatch.setattr(mpoint, "cancel_intent", boom)
    r = env.delete("/api/mp/charge/i1")
    assert r.status_code == 409 and "409" in r.json()["detail"]

    monkeypatch.setattr(mpoint, "list_devices", boom)
    assert env.get("/api/mp/devices").status_code == 502

    monkeypatch.setattr(mpoint, "list_devices",
                        lambda t: [{"id": "DEV1", "operating_mode": "STANDALONE"}])
    assert env.get("/api/mp/devices").json()["devices"][0]["id"] == "DEV1"

    visto = {}
    monkeypatch.setattr(mpoint, "set_operating_mode",
                        lambda t, d, m: visto.update(device=d, mode=m) or {})
    r = env.post("/api/mp/device-mode",
                 json={"device_id": "DEV1", "operating_mode": "PDV"})
    assert r.status_code == 200 and visto == {"device": "DEV1", "mode": "PDV"}
    # Modo inválido no pasa la validación.
    assert env.post("/api/mp/device-mode",
                    json={"device_id": "DEV1", "operating_mode": "X"}).status_code == 422
