"""SQLite storage for the QTT sample: schema, connections and first-start seeding.

The database is a single file (default `data/qtt.db` next to the `app` package). It is created and
seeded the first time the app starts; delete the file to start again from the seed.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = PACKAGE_DIR.parent / "data" / "qtt.db"

SCHEMA = """
CREATE TABLE users (
    user_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT    NOT NULL UNIQUE,
    full_name      TEXT    NOT NULL,
    email          TEXT    NOT NULL,
    role           TEXT    NOT NULL CHECK (role IN ('ANALYST', 'REVIEWER')),
    password_hash  TEXT    NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT    NOT NULL
);

CREATE TABLE loans (
    loan_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_no          TEXT    NOT NULL UNIQUE,
    borrower_name    TEXT    NOT NULL,
    co_borrower_name TEXT,
    product_code     TEXT    NOT NULL,
    loan_amount      NUMERIC NOT NULL,
    interest_rate    NUMERIC NOT NULL,
    closing_date     TEXT    NOT NULL,
    branch_code      TEXT    NOT NULL,
    branch_name      TEXT    NOT NULL,
    loan_officer     TEXT    NOT NULL,
    property_state   TEXT    NOT NULL,
    created_at       TEXT    NOT NULL
);

CREATE TABLE qc_reviews (
    review_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    review_no      TEXT    NOT NULL UNIQUE,
    loan_id        INTEGER NOT NULL REFERENCES loans (loan_id),
    review_type    TEXT    NOT NULL,
    status         TEXT    NOT NULL
                   CHECK (status IN ('OPEN', 'IN_PROGRESS', 'SUBMITTED', 'RETURNED', 'APPROVED')),
    assigned_to    INTEGER REFERENCES users (user_id),
    reviewer_id    INTEGER REFERENCES users (user_id),
    due_date       TEXT    NOT NULL,
    return_reason  TEXT,
    submitted_at   TEXT,
    approved_at    TEXT,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);
CREATE INDEX ix_qc_reviews_status ON qc_reviews (status);
CREATE INDEX ix_qc_reviews_assigned_to ON qc_reviews (assigned_to);

CREATE TABLE checklist_items (
    checklist_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_code         TEXT    NOT NULL UNIQUE,
    category          TEXT    NOT NULL
                      CHECK (category IN ('Credit', 'Income', 'Collateral', 'Compliance')),
    description       TEXT    NOT NULL,
    guidance          TEXT,
    sort_order        INTEGER NOT NULL,
    is_active         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE review_items (
    review_item_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id         INTEGER NOT NULL REFERENCES qc_reviews (review_id),
    checklist_item_id INTEGER NOT NULL REFERENCES checklist_items (checklist_item_id),
    result            TEXT    CHECK (result IN ('PASS', 'FAIL', 'NA')),
    comment           TEXT,
    updated_by        INTEGER REFERENCES users (user_id),
    updated_at        TEXT,
    UNIQUE (review_id, checklist_item_id)
);

CREATE TABLE review_audit (
    audit_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id    INTEGER NOT NULL REFERENCES qc_reviews (review_id),
    event        TEXT    NOT NULL,
    from_status  TEXT,
    to_status    TEXT,
    actor        TEXT    NOT NULL,
    detail       TEXT,
    created_at   TEXT    NOT NULL
);
CREATE INDEX ix_review_audit_review_id ON review_audit (review_id);
"""

TABLES = ("users", "loans", "qc_reviews", "checklist_items", "review_items", "review_audit")


def db_path_from_env() -> Path:
    return Path(os.environ.get("QTT_DB_PATH") or DEFAULT_DB_PATH)


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialise(path: Path) -> bool:
    """Create and seed the database if the file does not exist yet. True if it was created."""
    from app import seed

    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        seed.load(conn)
        conn.commit()
    except Exception:
        conn.close()
        path.unlink(missing_ok=True)
        raise
    conn.close()
    return True


def connections(path: Path) -> Iterator[sqlite3.Connection]:
    """A connection per request; committed by the code that changes data."""
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()
