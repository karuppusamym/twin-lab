"""SYNTHETIC seed data for local runs. Every name, number and address here is made up.

Each table is filled only when it is empty, so tables the lab (or a data pack) already filled are
left alone. Test credentials live here and nowhere else.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.auth import hash_password

TEST_PASSWORD = "Qtt-Sample-2026"  # synthetic test credential, same for every seeded user

USERS = [
    ("analyst1", "Dana Whitfield", "ANALYST"),
    ("analyst2", "Priya Raman", "ANALYST"),
    ("reviewer1", "Morgan Ellis", "REVIEWER"),
]

CHECKLIST = [
    ("CRD-01", "Credit", "Credit report dated within 120 days of the note date"),
    ("CRD-02", "Credit", "Debt-to-income ratio within the product guideline"),
    ("CRD-03", "Credit", "Explanations on file for every recent credit inquiry"),
    ("INC-01", "Income", "Income documentation is complete"),
    ("INC-02", "Income", "Verbal verification of employment within 10 business days of closing"),
    ("INC-03", "Income", "Self-employment income calculation reviewed and supported"),
    ("COL-01", "Collateral", "Appraised value supports the loan-to-value ratio"),
    ("COL-02", "Collateral", "Appraisal matches the property on the note"),
    ("COL-03", "Collateral", "Flood certification on file for the property"),
    ("CMP-01", "Compliance", "Loan Estimate issued within 3 business days of application"),
    ("CMP-02", "Compliance", "Closing Disclosure received 3 business days before closing"),
    ("CMP-03", "Compliance", "HMDA data fields match the loan file"),
]

BRANCHES = [
    ("TX-104", "Austin Central", "TX", "Jordan Pike"),
    ("CO-201", "Denver North", "CO", "Avery Stone"),
    ("AZ-310", "Phoenix East", "AZ", "Riley Marsh"),
    ("OH-115", "Columbus West", "OH", "Casey Lund"),
]
PRODUCTS = ["A71", "A51", "F30", "F15", "J30", "VA", "FHA"]

FIRST = [
    "Alex",
    "Jamie",
    "Taylor",
    "Robin",
    "Quinn",
    "Morgan",
    "Parker",
    "Hayden",
    "Rowan",
    "Emerson",
    "Logan",
    "Sasha",
    "Devon",
    "Kendall",
    "Finley",
    "Harper",
    "Jules",
    "Marley",
    "Peyton",
    "Sage",
]
LAST = [
    "Fernwood",
    "Holloway",
    "Brightwater",
    "Castellan",
    "Ashford",
    "Delacroix",
    "Glenwood",
    "Marlowe",
    "Kessler",
    "Vale",
    "Pemberton",
    "Whitlock",
    "Ainsley",
    "Thorne",
    "Okafor",
    "Lindqvist",
    "Carrow",
    "Quill",
    "Hargrove",
    "Montrose",
]
LOAN_COUNT = 60  # more than the 50-row listing limit, so the limit can be seen

# review no, loan index, type, status, assignee, due offset (days)
REVIEWS = [
    ("R-1001", 0, "Standard", "OPEN", "analyst1", 6),
    ("R-1002", 1, "Standard", "IN_PROGRESS", "analyst1", 3),
    ("R-1003", 2, "Targeted", "SUBMITTED", "analyst2", 2),
    ("R-1004", 3, "Standard", "RETURNED", "analyst1", 1),
    ("R-1005", 4, "Targeted", "APPROVED", "analyst2", -4),
    ("R-1006", 4, "Standard", "OPEN", "analyst2", 9),
]


def _count(conn, table: str) -> int:
    return conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]


def seed_if_empty(conn) -> bool:
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    today = date.today()
    changed = False

    if not _count(conn, "users"):
        changed = True
        for username, full_name, role in USERS:
            conn.execute(
                "INSERT INTO users (username, full_name, email, role, password_hash, is_active,"
                " created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
                (
                    username,
                    full_name,
                    f"{username}@example.test",
                    role,
                    hash_password(TEST_PASSWORD),
                    str(now - timedelta(days=90)),
                ),
            )

    if not _count(conn, "checklist_items"):
        changed = True
        for order, (code, category, description) in enumerate(CHECKLIST, start=1):
            conn.execute(
                "INSERT INTO checklist_items (item_code, category, description, guidance,"
                " sort_order, is_active) VALUES (?, ?, ?, NULL, ?, 1)",
                (code, category, description, order * 10),
            )

    if not _count(conn, "loans"):
        changed = True
        for n in range(LOAN_COUNT):
            branch_code, branch_name, state, officer = BRANCHES[n % len(BRANCHES)]
            surname = LAST[(n * 7 + n // 20) % len(LAST)]
            borrower = f"{FIRST[n % len(FIRST)]} {surname}"
            co_borrower = f"{FIRST[(n + 5) % len(FIRST)]} {surname}" if n % 3 == 1 else None
            conn.execute(
                "INSERT INTO loans (loan_no, borrower_name, co_borrower_name, product_code,"
                " loan_amount, interest_rate, closing_date, branch_code, branch_name,"
                " loan_officer, property_state, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"L-{200001 + n}",
                    borrower,
                    co_borrower,
                    PRODUCTS[n % len(PRODUCTS)],
                    185000 + 12500 * n,
                    round(5.875 + 0.125 * (n % 7), 3),
                    (today - timedelta(days=20 + 3 * n)).isoformat(),
                    branch_code,
                    branch_name,
                    officer,
                    state,
                    str(now - timedelta(days=60)),
                ),
            )

    if not _count(conn, "qc_reviews"):
        users = {r["username"]: r["user_id"] for r in conn.execute("SELECT * FROM users")}
        loans = [r["loan_id"] for r in conn.execute("SELECT loan_id FROM loans ORDER BY loan_no")]
        if users and loans:
            changed = True
            for review_no, loan_ix, rtype, status, assignee, due in REVIEWS:
                created = str(now - timedelta(days=14))
                conn.execute(
                    "INSERT INTO qc_reviews (review_no, loan_id, review_type, status, assigned_to,"
                    " due_date, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        review_no,
                        loans[loan_ix],
                        rtype,
                        status,
                        users.get(assignee),
                        (today + timedelta(days=due)).isoformat(),
                        created,
                        created,
                    ),
                )
    return changed
