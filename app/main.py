"""Punto de entrada de la app: arranque, seed inicial y servidor web."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router as api_router
from .config import get_config
from .db import init_db, session_scope
from .models import (
    SETTING_AUTOCREATE_SINCE,
    SETTING_CURRENCY,
    SETTING_ELECTRICITY_PRICE,
    Material,
    Printer,
    Setting,
)
from .services.live import LiveLoop
from .services.sync import SyncLoop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("printcost")

STATIC_DIR = Path(__file__).parent / "web" / "static"


def seed_from_bootstrap() -> None:
    """Vuelca la config inicial a la BD (solo si las tablas están vacías)."""
    cfg = get_config()
    boot = cfg.bootstrap
    with session_scope() as session:
        # Ajustes globales: se crean solo si no existen (la UI manda después).
        if session.get(Setting, SETTING_ELECTRICITY_PRICE) is None:
            session.add(
                Setting(
                    key=SETTING_ELECTRICITY_PRICE,
                    value=str(boot.electricity_price_per_kwh),
                )
            )
        if session.get(Setting, SETTING_CURRENCY) is None:
            session.add(Setting(key=SETTING_CURRENCY, value=boot.currency))

        # Marca a partir de la cual se auto-crean filamentos: la fijamos en el
        # primer arranque con esta función, así el back-catálogo no la dispara.
        if session.get(Setting, SETTING_AUTOCREATE_SINCE) is None:
            session.add(
                Setting(
                    key=SETTING_AUTOCREATE_SINCE,
                    value=datetime.now(timezone.utc).isoformat(),
                )
            )
            log.info("Auto-creación de filamentos activada desde ahora.")

        # Impresoras: upsert por nombre solo si la tabla está vacía.
        if session.query(Printer).count() == 0:
            for p in boot.printers:
                session.add(Printer(**p.model_dump()))
                log.info("Seed impresora: %s", p.name)

        # Materiales: igual.
        if session.query(Material).count() == 0:
            for m in boot.materials:
                session.add(Material(**m.model_dump()))
                log.info("Seed material: %s", m.name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    init_db()
    seed_from_bootstrap()
    loop = SyncLoop(cfg.poll_interval_seconds)
    loop.start()
    app.state.sync_loop = loop
    live = LiveLoop(interval=15)
    live.start()
    app.state.live_loop = live
    log.info("M3D Nexus listo. HA=%s", cfg.ha_url)
    try:
        yield
    finally:
        loop.stop()
        live.stop()


app = FastAPI(title="M3D Nexus", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)


@app.get("/")
def index():
    # El index nunca se cachea: es quien referencia app.js/style.css con su
    # ?v=… de versión. Si el navegador sirviera un index viejo, cargaría el JS
    # anterior y "los cambios no se ven" hasta un Ctrl+F5.
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-cache"},
    )


# Rutas de la SPA: cada sección tiene su URL propia (/pedidos, /impresiones…).
# La navegación en el navegador es con history.pushState —no recarga—, pero al
# entrar directo o recargar (F5) el servidor tiene que devolver el index para
# que el front resuelva la sección. Se registran una a una, y ANTES del mount de
# estáticos, para no capturar /app.js ni /style.css con un comodín.
SPA_PAGES = (
    "dashboard", "trabajos", "impresiones", "impresoras", "materiales",
    "calibracion", "estimacion", "cotizacion", "ajustes",
    "pedidos",   # alias histórico de /trabajos
)
for _page in SPA_PAGES:
    app.get(f"/{_page}", include_in_schema=False)(index)


app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
