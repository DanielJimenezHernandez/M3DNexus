"""Configuración de SQLAlchemy: engine, sesión y Base declarativa."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_config


class Base(DeclarativeBase):
    pass


_config = get_config()
_engine = create_engine(
    f"sqlite:///{_config.db_path}",
    # SQLite + acceso desde el hilo de polling y desde FastAPI. ``timeout`` es el
    # busy_timeout: una escritura concurrente ESPERA hasta 30 s por el lock en
    # vez de fallar con "database is locked" (el sync retiene el lock mientras
    # baja miniaturas/energía de la red).
    connect_args={"check_same_thread": False, "timeout": 30},
    future=True,
)


@event.listens_for(_engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    """WAL: los lectores (p.ej. GET /jobs) no se bloquean mientras el sync
    escribe. Y busy_timeout por conexión, por si el connect_args no bastara."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()
SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Crea las tablas si no existen y aplica migraciones ligeras."""
    from . import models  # noqa: F401  (registra los modelos en Base.metadata)

    Base.metadata.create_all(_engine)
    _migrate()


def _migrate() -> None:
    """Añade columnas nuevas a tablas ya creadas (SQLite no lo hace solo)."""
    additions = {
        "materials": {
            "color": "ALTER TABLE materials ADD COLUMN color VARCHAR",
            "color_hex": "ALTER TABLE materials ADD COLUMN color_hex VARCHAR",
            "stock_level": "ALTER TABLE materials ADD COLUMN stock_level VARCHAR DEFAULT 'unknown'",
            "purchase_url": "ALTER TABLE materials ADD COLUMN purchase_url VARCHAR",
            "auto_created": "ALTER TABLE materials ADD COLUMN auto_created BOOLEAN DEFAULT 0",
        },
        "print_jobs": {
            "energy_unavailable": "ALTER TABLE print_jobs ADD COLUMN energy_unavailable BOOLEAN DEFAULT 0",
            "energy_estimated": "ALTER TABLE print_jobs ADD COLUMN energy_estimated BOOLEAN DEFAULT 0",
            "cost_maintenance": "ALTER TABLE print_jobs ADD COLUMN cost_maintenance FLOAT DEFAULT 0",
            "thumbnail": "ALTER TABLE print_jobs ADD COLUMN thumbnail BLOB",
            "has_thumbnail": "ALTER TABLE print_jobs ADD COLUMN has_thumbnail BOOLEAN DEFAULT 0",
            "thumbnail_tried": "ALTER TABLE print_jobs ADD COLUMN thumbnail_tried BOOLEAN DEFAULT 0",
            "material_manual": "ALTER TABLE print_jobs ADD COLUMN material_manual BOOLEAN DEFAULT 0",
        },
        "orders": {
            "folder": "ALTER TABLE orders ADD COLUMN folder VARCHAR",
            "deposit_amount": "ALTER TABLE orders ADD COLUMN deposit_amount FLOAT",
            "extra_expenses": "ALTER TABLE orders ADD COLUMN extra_expenses JSON",
            "postproc_rate": "ALTER TABLE orders ADD COLUMN postproc_rate FLOAT",
            "postproc_minutes": "ALTER TABLE orders ADD COLUMN postproc_minutes INTEGER DEFAULT 0",
        },
        "order_items": {
            "extra_expenses": "ALTER TABLE order_items ADD COLUMN extra_expenses JSON",
            "copy_status": "ALTER TABLE order_items ADD COLUMN copy_status JSON",
        },
        "printers": {
            "orca_aliases": "ALTER TABLE printers ADD COLUMN orca_aliases JSON",
            "multicolor": "ALTER TABLE printers ADD COLUMN multicolor BOOLEAN DEFAULT 0",
            "slot_count": "ALTER TABLE printers ADD COLUMN slot_count INTEGER DEFAULT 1",
            "loaded_materials": "ALTER TABLE printers ADD COLUMN loaded_materials JSON",
            "amortization_years": "ALTER TABLE printers ADD COLUMN amortization_years FLOAT DEFAULT 2",
            "active_days_per_year": "ALTER TABLE printers ADD COLUMN active_days_per_year FLOAT DEFAULT 250",
            "active_hours_per_day": "ALTER TABLE printers ADD COLUMN active_hours_per_day FLOAT DEFAULT 8",
            "resale_value": "ALTER TABLE printers ADD COLUMN resale_value FLOAT DEFAULT 0",
            "maintenance_per_hour": "ALTER TABLE printers ADD COLUMN maintenance_per_hour FLOAT DEFAULT 0",
            "power_ref_printer_id": "ALTER TABLE printers ADD COLUMN power_ref_printer_id INTEGER",
            "ui_port": "ALTER TABLE printers ADD COLUMN ui_port INTEGER DEFAULT 80",
            "host": "ALTER TABLE printers ADD COLUMN host VARCHAR DEFAULT ''",
            "moonraker_port": "ALTER TABLE printers ADD COLUMN moonraker_port INTEGER DEFAULT 7125",
        },
    }
    with _engine.begin() as conn:
        added: set[str] = set()
        for table, cols in additions.items():
            existing = {
                row[1]
                for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            }
            for col, ddl in cols.items():
                if col not in existing:
                    conn.exec_driver_sql(ddl)
                    added.add(col)

        # Migra y elimina la columna antigua 'moonraker_url': su NOT NULL rompía
        # los INSERT nuevos (el modelo ya no la rellena). Idempotente.
        pcols = {
            row[1]
            for row in conn.exec_driver_sql("PRAGMA table_info(printers)").fetchall()
        }
        if "moonraker_url" in pcols and "host" in pcols:
            from urllib.parse import urlparse

            for pid, murl, host in conn.exec_driver_sql(
                "SELECT id, moonraker_url, host FROM printers"
            ).fetchall():
                if (not host) and murl:
                    u = urlparse(murl)
                    conn.exec_driver_sql(
                        "UPDATE printers SET host = ?, moonraker_port = ? WHERE id = ?",
                        (u.hostname or "", u.port or 7125, pid),
                    )
            conn.exec_driver_sql("ALTER TABLE printers DROP COLUMN moonraker_url")

        # Elimina columnas muertas (quitadas del modelo) que conservan NOT NULL
        # y, por tanto, rompen los INSERT nuevos. Idempotente.
        removals = {
            "printers": ["expected_lifetime_hours", "maintenance_cost_per_hour"],
            # El post-procesado pasó de por-pieza a por-pedido.
            "order_items": ["postproc_minutes"],
        }
        for table, drop_cols in removals.items():
            existing = {
                row[1]
                for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            }
            for col in drop_cols:
                if col in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {col}")

        # Backfill: si acabamos de crear la amortización, derivamos los años
        # desde la antigua 'expected_lifetime_hours' para no cambiar el coste/h
        # (años = horas_vida / (250 días × 8 h)). Solo una vez.
        if "amortization_years" in added:
            cols = {
                row[1]
                for row in conn.exec_driver_sql("PRAGMA table_info(printers)").fetchall()
            }
            if "expected_lifetime_hours" in cols:
                conn.exec_driver_sql(
                    "UPDATE printers SET amortization_years = "
                    "expected_lifetime_hours / (250.0 * 8.0) "
                    "WHERE expected_lifetime_hours IS NOT NULL "
                    "AND expected_lifetime_hours > 0"
                )

        # Amplía la clave única de print_jobs a (printer, job_id, start_time).
        # Se hace al final: la tabla ya tiene todas sus columnas actuales.
        _widen_print_job_key(conn)


def _widen_print_job_key(conn) -> None:
    """Reconstruye print_jobs para meter start_time en la clave única.

    SQLite no deja soltar el auto-índice de una UNIQUE declarada, así que la
    única vía es recrear la tabla. Solo se hace si sigue la clave vieja de dos
    columnas; después es idempotente.
    """
    from .models import PrintJob

    uniques = [
        row[1]
        for row in conn.exec_driver_sql("PRAGMA index_list(print_jobs)").fetchall()
        if row[2]  # es UNIQUE
    ]
    old_key = None
    for name in uniques:
        cols = [
            r[2] for r in conn.exec_driver_sql(f"PRAGMA index_info('{name}')").fetchall()
        ]
        if cols == ["printer_id", "moonraker_job_id"]:
            old_key = name
            break
    if old_key is None:
        return  # ya migrada (o tabla recién creada con la clave nueva)

    old_cols = {
        row[1]
        for row in conn.exec_driver_sql("PRAGMA table_info(print_jobs)").fetchall()
    }
    common = [c.name for c in PrintJob.__table__.columns if c.name in old_cols]
    collist = ", ".join(common)

    conn.exec_driver_sql("ALTER TABLE print_jobs RENAME TO _print_jobs_old")
    # El índice con nombre viaja con la tabla al renombrarla; se suelta para que
    # al recrear la tabla no choque su nombre.
    conn.exec_driver_sql("DROP INDEX IF EXISTS ix_print_jobs_moonraker_job_id")
    PrintJob.__table__.create(bind=conn)
    conn.exec_driver_sql(
        f"INSERT INTO print_jobs ({collist}) SELECT {collist} FROM _print_jobs_old"
    )
    conn.exec_driver_sql("DROP TABLE _print_jobs_old")


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sesión transaccional para usar fuera de las rutas (poller, scripts)."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """Dependencia de FastAPI."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
