"""A small synthetic "replica" in SQLite, for tests and offline demos. Every value is made up.

Shape, chosen to exercise the closure:
- `customers` -> `accounts` (declared FK), and `customers.primary_account_id` -> `accounts`
  plus `customers.referred_by` -> `customers`: two cycles.
- `accounts` -> `branches` (a parent that must be pulled) and `transactions` (children).
- `addresses.customer_ref` holds a customer id with no declared FK, so only an association
  reaches it (what the app-load trace diff is meant to find).
- `crm_contacts` is the same people as kept by a second system (for cross-table masking).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE branches (
  id INTEGER PRIMARY KEY,
  code VARCHAR(8) NOT NULL UNIQUE,
  city VARCHAR(40) NOT NULL
);
CREATE TABLE customers (
  id INTEGER PRIMARY KEY,
  first_name VARCHAR(40) NOT NULL,
  last_name VARCHAR(40) NOT NULL,
  ssn VARCHAR(11) NOT NULL UNIQUE,
  birth_date DATE,
  email VARCHAR(80),
  phone VARCHAR(20),
  primary_account_id INTEGER REFERENCES accounts(id),
  referred_by INTEGER REFERENCES customers(id),
  updated_at TIMESTAMP
);
CREATE TABLE accounts (
  id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  branch_id INTEGER NOT NULL REFERENCES branches(id),
  account_no VARCHAR(12) NOT NULL UNIQUE,
  card_no VARCHAR(19),
  opened_on DATE,
  status VARCHAR(10) NOT NULL,
  LAST_MOD_TS TIMESTAMP
);
CREATE TABLE transactions (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  amount NUMERIC(12, 2) NOT NULL,
  posted_on DATE NOT NULL
);
CREATE TABLE addresses (
  id INTEGER PRIMARY KEY,
  customer_ref INTEGER NOT NULL,
  line1 VARCHAR(80) NOT NULL,
  city VARCHAR(40) NOT NULL,
  MODIFIED_DATE DATE
);
CREATE TABLE crm_contacts (
  id INTEGER PRIMARY KEY,
  given_name VARCHAR(40),
  surname VARCHAR(40),
  tax_ssn VARCHAR(11),
  dob DATE
);
"""

FIRST = ["Avery", "Blake", "Casey", "Devon", "Emery", "Finley"]
LAST = ["Quill", "Rowan", "Sable", "Thorn", "Umber", "Vale"]
CUSTOMERS = 6
ACCOUNTS_OF_FIRST = 5  # customer 1 fans out; the other customers have 2 accounts each


def luhn_digit(body: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return str((10 - total % 10) % 10)


def build(path: Path) -> Path:
    """Create the synthetic database at `path` (replaced if it exists)."""
    path = Path(path)
    if path.exists():
        path.unlink()
    db = sqlite3.connect(path)
    try:
        db.executescript(DDL)
        db.executemany(
            "INSERT INTO branches VALUES (?, ?, ?)",
            [(1, "BR01", "Northfield"), (2, "BR02", "Eastbay")],
        )
        accounts, transactions, addresses, customers = [], [], [], []
        acct_id = 100
        for c in range(1, CUSTOMERS + 1):
            n_acc = ACCOUNTS_OF_FIRST if c == 1 else 2
            first_acct = acct_id
            for k in range(n_acc):
                body = f"400000{c:02d}{k:02d}0000{acct_id % 100:02d}"[:15]
                accounts.append(
                    (
                        acct_id,
                        c,
                        1 + (c % 2),
                        f"7{c:03d}{k:04d}{acct_id:04d}"[:12],
                        body + luhn_digit(body),
                        f"20{10 + c:02d}-0{1 + k}-15",
                        "open" if k else "primary",
                        "2026-10-01 09:00:00",
                    )
                )
                for t in range(3):
                    transactions.append(
                        (acct_id * 10 + t, acct_id, f"{(t + 1) * 12.5:.2f}", f"2026-09-{10 + t}")
                    )
                acct_id += 1
            referred = 2 if c == 1 else (1 if c == 2 else None)  # 1 <-> 2 is a cycle
            customers.append(
                (
                    c,
                    FIRST[c - 1],
                    LAST[c - 1],
                    f"{200 + c:03d}-{10 + c:02d}-{1000 + c:04d}",
                    f"19{70 + c}-0{c}-1{c}",
                    f"{FIRST[c - 1].lower()}.{LAST[c - 1].lower()}@example.test",
                    f"(555) 01{c}-20{c:02d}",
                    first_acct,
                    referred,
                    "2026-10-02 10:00:00",
                )
            )
            for a in range(2):
                addresses.append(
                    (c * 10 + a, c, f"{10 + a + c} Elm Row", "Northfield", "2026-01-01")
                )
        db.execute("PRAGMA foreign_keys = OFF")
        db.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?,?)", customers)
        db.executemany("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)", accounts)
        db.executemany("INSERT INTO transactions VALUES (?,?,?,?)", transactions)
        db.executemany("INSERT INTO addresses VALUES (?,?,?,?,?)", addresses)
        db.executemany(
            "INSERT INTO crm_contacts VALUES (?,?,?,?,?)",
            [(900 + c[0], c[1], c[2], c[3], c[4]) for c in customers],
        )
        db.commit()
    finally:
        db.close()
    return path
