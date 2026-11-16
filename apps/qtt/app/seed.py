"""SYNTHETIC seed data for local runs. Every name, number and address here is made up.

Loaded only when the `users` table is empty; the lab loads its own data packs over it.
Test credentials live here and nowhere else.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.auth import hash_password

TEST_PASSWORD = "Qtt-Sample-2026"  # synthetic test credential, same for every seeded user

USERS = [
    # username, full name, role
    ("analyst1", "Dana Whitfield", "ANALYST"),
    ("analyst2", "Priya Raman", "ANALYST"),
    ("reviewer1", "Morgan Ellis", "REVIEWER"),
]

CHECKLIST = [
    # code, category, description, guidance
    (
        "CRD-01",
        "Credit",
        "Credit report dated within 120 days of the note date",
        "Compare the report date with the note date on the closing package.",
    ),
    (
        "CRD-02",
        "Credit",
        "Debt-to-income ratio within the product guideline",
        "Use the final income and liabilities figures.",
    ),
    (
        "CRD-03",
        "Credit",
        "Explanations on file for every recent credit inquiry",
        "Check each inquiry made in the last 90 days.",
    ),
    (
        "INC-01",
        "Income",
        "Income documentation is complete (pay stubs, W-2s or tax returns)",
        "Two years of history for salaried and self-employed borrowers.",
    ),
    (
        "INC-02",
        "Income",
        "Verbal verification of employment within 10 business days of closing",
        "Use the employer's published phone number; note who answered.",
    ),
    (
        "INC-03",
        "Income",
        "Self-employment income calculation reviewed and supported",
        "Recalculate from the business returns where they are provided.",
    ),
    (
        "COL-01",
        "Collateral",
        "Appraised value supports the loan-to-value ratio",
        "LTV must not exceed the product maximum.",
    ),
    ("COL-02", "Collateral", "Appraisal matches the property on the note", None),
    ("COL-03", "Collateral", "Flood certification on file for the property", None),
    ("CMP-01", "Compliance", "Loan Estimate issued within 3 business days of application", None),
    ("CMP-02", "Compliance", "Closing Disclosure received 3 business days before closing", None),
    ("CMP-03", "Compliance", "HMDA data fields match the loan file", None),
]

BRANCHES = [
    ("TX-104", "Austin Central", "TX", "Jordan Pike"),
    ("CO-201", "Denver North", "CO", "Avery Stone"),
    ("AZ-310", "Phoenix East", "AZ", "Riley Marsh"),
    ("OH-115", "Columbus West", "OH", "Casey Lund"),
]

PRODUCTS = ["F30", "F15", "J30", "A71", "VA", "FHA"]

BORROWERS = [
    ("Alex Fernwood", None),
    ("Jamie Holloway", "Sam Holloway"),
    ("Taylor Brightwater", None),
    ("Robin Castellan", "Drew Castellan"),
    ("Quinn Ashford", None),
    ("Morgan Delacroix", None),
    ("Parker Glenwood", "Reese Glenwood"),
    ("Hayden Marlowe", None),
    ("Rowan Kessler", None),
    ("Emerson Vale", "Sky Vale"),
    ("Logan Pemberton", None),
    ("Sasha Whitlock", None),
    ("Devon Ainsley", None),
    ("Kendall Thorne", "Blair Thorne"),
    ("Finley Okafor", None),
    ("Harper Lindqvist", None),
    ("Jules Carrow", None),
    ("Marley Quill", "Ari Quill"),
    ("Peyton Hargrove", None),
    ("Sage Montrose", None),
    ("Toby Wren", None),
    ("Linden Brooks", None),
    ("Casey Halvorsen", "Jesse Halvorsen"),
    ("River Oakes", None),
]

# review no, loan index, type, status, assignee, items answered, failed codes, due offset (days)
REVIEWS = [
    ("R-1001", 0, "Standard", "OPEN", "analyst1", 0, [], 6),
    ("R-1002", 1, "Standard", "IN_PROGRESS", "analyst1", 5, ["CRD-02"], 3),
    ("R-1003", 2, "Targeted", "SUBMITTED", "analyst2", 12, ["INC-01"], 2),
    ("R-1004", 3, "Standard", "RETURNED", "analyst1", 12, ["CMP-01"], 1),
    ("R-1005", 4, "Targeted", "APPROVED", "analyst2", 12, [], -4),
    ("R-1006", 5, "Standard", "OPEN", "analyst2", 0, [], 9),
    ("R-1007", 6, "Standard", "OPEN", "analyst1", 0, [], -2),
    ("R-1008", 7, "Targeted", "SUBMITTED", "analyst1", 12, ["CRD-03", "INC-02"], 0),
    ("R-1009", 8, "Standard", "IN_PROGRESS", "analyst2", 8, [], 4),
    ("R-1010", 9, "Standard", "APPROVED", "analyst1", 12, ["COL-01"], -10),
]

RETURN_REASON = "Loan Estimate date does not match the disclosure log; please recheck CMP-01."


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def seed_if_empty(conn) -> bool:
    if conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"]:
        return False
    now = _now()
    today = date.today()
    users: dict[str, int] = {}
    for username, full_name, role in USERS:
        row = conn.execute(
            "INSERT INTO users (username, full_name, email, role, password_hash, is_active,"
            " created_at) VALUES (%s, %s, %s, %s, %s, true, %s) RETURNING user_id",
            (
                username,
                full_name,
                f"{username}@example.test",
                role,
                hash_password(TEST_PASSWORD),
                now - timedelta(days=90),
            ),
        ).fetchone()
        users[username] = row["user_id"]

    items: dict[str, int] = {}
    for order, (code, category, description, guidance) in enumerate(CHECKLIST, start=1):
        row = conn.execute(
            "INSERT INTO checklist_items (item_code, category, description, guidance, sort_order,"
            " is_active) VALUES (%s, %s, %s, %s, %s, true) RETURNING checklist_item_id",
            (code, category, description, guidance, order * 10),
        ).fetchone()
        items[code] = row["checklist_item_id"]

    loans: list[int] = []
    for n, (borrower, co_borrower) in enumerate(BORROWERS):
        branch_code, branch_name, state, officer = BRANCHES[n % len(BRANCHES)]
        row = conn.execute(
            "INSERT INTO loans (loan_no, borrower_name, co_borrower_name, product_code,"
            " loan_amount, interest_rate, closing_date, branch_code, branch_name, loan_officer,"
            " property_state, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " RETURNING loan_id",
            (
                f"L-{200001 + n}",
                borrower,
                co_borrower,
                PRODUCTS[n % len(PRODUCTS)],
                185000 + 12500 * n,
                round(5.875 + 0.125 * (n % 7), 3),
                today - timedelta(days=20 + 3 * n),
                branch_code,
                branch_name,
                officer,
                state,
                now - timedelta(days=60),
            ),
        ).fetchone()
        loans.append(row["loan_id"])

    codes = [c[0] for c in CHECKLIST]
    for review_no, loan_ix, rtype, status, assignee, answered, failed, due in REVIEWS:
        created = now - timedelta(days=14)
        updated = created
        submitted = approved = None
        reviewer = return_reason = None
        if status in ("SUBMITTED", "RETURNED", "APPROVED"):
            submitted = now - timedelta(days=2)
            updated = submitted
        if status in ("RETURNED", "APPROVED"):
            reviewer = users["reviewer1"]
            updated = now - timedelta(days=1)
        if status == "APPROVED":
            approved = updated
        if status == "RETURNED":
            return_reason = RETURN_REASON
        row = conn.execute(
            "INSERT INTO qc_reviews (review_no, loan_id, review_type, status, assigned_to,"
            " reviewer_id, due_date, return_reason, submitted_at, approved_at, created_at,"
            " updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " RETURNING review_id",
            (
                review_no,
                loans[loan_ix],
                rtype,
                status,
                users[assignee],
                reviewer,
                today + timedelta(days=due),
                return_reason,
                submitted,
                approved,
                created,
                updated,
            ),
        ).fetchone()
        review_id = row["review_id"]

        def audit(event, from_s, to_s, actor, detail, when, review_id=review_id):
            conn.execute(
                "INSERT INTO review_audit (review_id, event, from_status, to_status, actor,"
                " detail, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (review_id, event, from_s, to_s, actor, detail, when),
            )

        audit("CREATED", None, "OPEN", "system", f"Assigned to {assignee}", created)
        if answered:
            audit("STARTED", "OPEN", "IN_PROGRESS", assignee, None, created + timedelta(hours=1))
        for i, code in enumerate(codes):
            result = comment = by = at = None
            if i < answered:
                result = "FAIL" if code in failed else "PASS"
                comment = "Synthetic finding for the demo copy." if result == "FAIL" else None
                by = users[assignee]
                at = created + timedelta(hours=2, minutes=i)
                audit("ITEM_UPDATED", None, None, assignee, f"{code} set to {result}", at)
            conn.execute(
                "INSERT INTO review_items (review_id, checklist_item_id, result, comment,"
                " updated_by, updated_at) VALUES (%s, %s, %s, %s, %s, %s)",
                (review_id, items[code], result, comment, by, at),
            )
        if submitted:
            audit("SUBMITTED", "IN_PROGRESS", "SUBMITTED", assignee, None, submitted)
        if status == "RETURNED":
            audit("RETURNED", "SUBMITTED", "RETURNED", "reviewer1", return_reason, updated)
        if status == "APPROVED":
            audit("APPROVED", "SUBMITTED", "APPROVED", "reviewer1", None, updated)
    return True
