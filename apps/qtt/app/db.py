"""Postgres access. The address comes from DATABASE_URL; tables come from db/schema.sql."""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger("qtt.db")

SCHEMA_PATH = Path(
    os.environ.get("QTT_SCHEMA_PATH", Path(__file__).resolve().parent.parent / "db" / "schema.sql")
)

_ready = threading.Event()


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    # Accept SQLAlchemy-style URLs too; psycopg wants plain postgresql://.
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@contextmanager
def connect():
    conn = psycopg.connect(database_url(), row_factory=dict_row, connect_timeout=5)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def ping() -> bool:
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001 - any failure means "database not reachable"
        return False


def ensure_schema(conn) -> bool:
    """Create the tables from db/schema.sql when the database has none. True if created."""
    row = conn.execute("SELECT to_regclass('public.users') AS t").fetchone()
    if row["t"] is not None:
        return False
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    return True


def initialise() -> None:
    """Create tables and the synthetic seed, retrying until the database answers."""
    from app import seed

    delay = 1.0
    while True:
        try:
            with connect() as conn:
                if ensure_schema(conn):
                    log.info("created tables from %s", SCHEMA_PATH)
            with connect() as conn:
                if seed.seed_if_empty(conn):
                    log.info("loaded synthetic seed data")
            _ready.set()
            return
        except Exception as exc:  # noqa: BLE001 - keep trying; the lab may start the db later
            log.warning("database not ready yet: %s", exc)
            time.sleep(delay)
            delay = min(delay * 2, 10.0)


def start_initialiser() -> None:
    threading.Thread(target=initialise, name="qtt-db-init", daemon=True).start()
