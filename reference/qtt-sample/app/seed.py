"""Deterministic synthetic seed: 3 users, 80 loans, a 12-item checklist and 50 reviews.

Every value is generated from SEED, so two fresh databases hold exactly the same rows. Names,
loan numbers and branches are invented.
"""

from __future__ import annotations

import hashlib
import random
import sqlite3
from datetime import date, datetime, timedelta

SEED = 20261115
TEST_PASSWORD = "Qtt-Sample-2026"  # synthetic test password shared by the three sample users
SEEDED_AT = "2026-10-01T08:00:00Z"

USERS = [
    ("analyst1", "Avery Holt", "ANALYST"),
    ("analyst2", "Jordan Pike", "ANALYST"),
    ("reviewer1", "Morgan Reyes", "REVIEWER"),
]

PRODUCTS = {
    "FX30": "Fixed 30",
    "FX15": "Fixed 15",
    "ARM51": "ARM 5/1",
    "ARM71": "ARM 7/1",
    "FHA30": "FHA",
    "VA30": "VA",
    "JMB30": "Jumbo Fixed 30",
    "USDA30": "USDA",
}

BRANCHES = [
    ("BR-014", "Denver Tech Center", "CO"),
    ("BR-022", "Phoenix North", "AZ"),
    ("BR-031", "Austin Central", "TX"),
    ("BR-047", "Raleigh Midtown", "NC"),
    ("BR-053", "Columbus East", "OH"),
]

FIRST = [
    "Avery", "Blake", "Casey", "Dana", "Elliot", "Finley", "Gray", "Harper", "Indy", "Jules",
    "Kendall", "Logan", "Marlow", "Noel", "Oakley", "Parker", "Quinn", "Rowan", "Sage", "Tatum",
    "Umber", "Vale", "Wren", "Yael", "Zion", "Arden", "Briar", "Cove",
]  # fmt: skip
LAST = [
    "Holt", "Marsh", "Penrose", "Calder", "Whitlow", "Ashby", "Brannock", "Delacroix", "Fenwick",
    "Garrow", "Hartigan", "Ivers", "Jessup", "Kittredge", "Lundy", "Merriweather", "Norwood",
    "Oxley", "Pruett", "Quarles", "Rudd", "Stroud", "Thackeray", "Underhill", "Vickery", "Wexley",
]  # fmt: skip
OFFICERS = ["Riley Banner", "Skyler Moss", "Taylor Quint", "Emerson Lake", "Cameron Doyle"]

# (code, category, description, guidance)
CHECKLIST = [
    ("CRD-01", "Credit", "Credit report dated within 120 days of the note date",
     "Compare the credit report date with the note date."),
    ("CRD-02", "Credit", "Representative credit score meets the product minimum",
     "Use the middle score of the primary borrower."),
    ("CRD-03", "Credit", "Liabilities on the application match the credit report",
     "Every open tradeline is listed or explained."),
    ("INC-01", "Income", "Income documentation is complete (pay stubs, W-2s or tax returns)",
     "Two years of history for salaried and self-employed borrowers."),
    ("INC-02", "Income", "Verbal verification of employment within 10 business days of closing",
     "Not required for retired borrowers; mark N/A with a comment."),
    ("INC-03", "Income", "Debt-to-income ratio recalculated and within guideline",
     "Recalculate from documented income and liabilities."),
    ("COL-01", "Collateral", "Appraised value supports the loan-to-value ratio",
     "LTV from the lower of appraised value and price."),
    ("COL-02", "Collateral", "Appraiser license active on the inspection date", None),
    ("COL-03", "Collateral", "Title commitment clear of unresolved liens", None),
    ("CMP-01", "Compliance", "Loan Estimate issued within 3 business days of application", None),
    ("CMP-02", "Compliance", "Closing Disclosure received 3 business days before consummation",
     None),
    ("CMP-03", "Compliance", "HMDA data fields complete and consistent with the file", None),
]  # fmt: skip

FAIL_COMMENTS = {
    "CRD-01": "Credit report is 134 days old at the note date; no updated report in the file.",
    "CRD-02": "Middle score 612 is below the 620 minimum for the product.",
    "CRD-03": "Auto loan on the credit report is not listed on the application.",
    "INC-01": "Only one year of W-2s in the file; second year missing.",
    "INC-02": "Verbal VOE dated 14 business days before closing.",
    "INC-03": "Recalculated DTI is 47.8%, above the 45% guideline.",
    "COL-01": "Appraised value gives 97% LTV against the 95% maximum.",
    "COL-02": "Appraiser license expired two weeks before the inspection.",
    "COL-03": "Prior mortgage lien not shown as released on the final title.",
    "CMP-01": "Loan Estimate issued on business day 5 after application.",
    "CMP-02": "Closing Disclosure receipt is 2 business days before consummation.",
    "CMP-03": "HMDA ethnicity field blank although the application has it.",
}
NA_COMMENT = "Not applicable to this loan."
RETURN_REASONS = [
    "Finding on INC-03 needs the recalculation worksheet attached.",
    "Please re-check the appraisal date against the license history.",
    "Comment on CRD-03 does not say which tradeline was missing.",
    "Closing Disclosure timing finding needs the e-sign receipt date.",
    "Please confirm the HMDA finding with the original application.",
]
REVIEW_TYPES = [
    "Post-Closing QC",
    "Post-Closing QC",
    "Post-Closing QC",
    "Targeted",
    "Discretionary",
]

# Status of each review, in review number order (R-1001 first). 15 OPEN, 10 IN_PROGRESS,
# 10 SUBMITTED, 5 RETURNED, 10 APPROVED.
STATUS_PATTERN = [
    "OPEN", "IN_PROGRESS", "SUBMITTED", "OPEN", "APPROVED",
    "RETURNED", "SUBMITTED", "IN_PROGRESS", "APPROVED", "OPEN",
]  # fmt: skip
REVIEW_COUNT = 50
LOAN_COUNT = 80


def password_hash(username: str, password: str) -> str:
    salt = f"qtt-sample:{username}".encode()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 50_000)
    return "pbkdf2_sha256$50000$" + digest.hex()


def _ts(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def load(conn: sqlite3.Connection) -> None:
    rng = random.Random(SEED)
    user_ids: dict[str, int] = {}
    for username, full_name, role in USERS:
        cur = conn.execute(
            "INSERT INTO users (username, full_name, email, role, password_hash, is_active,"
            " created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
            (
                username,
                full_name,
                f"{username}@qtt.example.test",
                role,
                password_hash(username, TEST_PASSWORD),
                SEEDED_AT,
            ),
        )
        user_ids[username] = cur.lastrowid

    item_ids: list[tuple[int, str]] = []
    for order, (code, category, description, guidance) in enumerate(CHECKLIST, start=1):
        cur = conn.execute(
            "INSERT INTO checklist_items (item_code, category, description, guidance, sort_order,"
            " is_active) VALUES (?, ?, ?, ?, ?, 1)",
            (code, category, description, guidance, order * 10),
        )
        item_ids.append((cur.lastrowid, code))

    loans: list[tuple[int, date]] = []
    used_names: set[str] = set()
    for n in range(LOAN_COUNT):
        while True:
            name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if name not in used_names:
                used_names.add(name)
                break
        co = f"{rng.choice(FIRST)} {name.split()[1]}" if rng.random() < 0.3 else None
        product = rng.choice(list(PRODUCTS))
        low, high = (766_600, 1_450_000) if product == "JMB30" else (95_000, 760_000)
        amount = round(rng.randint(low, high) / 500) * 500
        rate = 5.25 + rng.randint(0, 18) * 0.125
        closing = date(2026, 7, 1) + timedelta(days=rng.randint(0, 91))
        branch_code, branch_name, state = rng.choice(BRANCHES)
        cur = conn.execute(
            "INSERT INTO loans (loan_no, borrower_name, co_borrower_name, product_code,"
            " loan_amount, interest_rate, closing_date, branch_code, branch_name, loan_officer,"
            " property_state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"L-{200001 + n}",
                name,
                co,
                product,
                amount,
                round(rate, 3),
                closing.isoformat(),
                branch_code,
                branch_name,
                rng.choice(OFFICERS),
                state,
                SEEDED_AT,
            ),
        )
        loans.append((cur.lastrowid, closing))

    chosen = rng.sample(range(LOAN_COUNT), REVIEW_COUNT)
    for n, loan_index in enumerate(chosen):
        loan_id, closing = loans[loan_index]
        status = STATUS_PATTERN[n % len(STATUS_PATTERN)]
        analyst = "analyst1" if n % 2 == 0 else "analyst2"
        created = datetime.combine(
            closing + timedelta(days=rng.randint(20, 40)), datetime.min.time()
        )
        created = created.replace(hour=8, minute=rng.randint(0, 59))
        due = (created + timedelta(days=30)).date()
        clock = created
        audit: list[tuple] = [
            ("CREATED", None, "OPEN", "system", f"Assigned to {analyst}", created)
        ]

        def tick(hours_low: int = 1, hours_high: int = 6) -> datetime:
            nonlocal clock
            clock = clock + timedelta(hours=rng.randint(hours_low, hours_high))
            return clock

        filled = {"OPEN": 0, "IN_PROGRESS": rng.randint(3, 9)}.get(status, len(item_ids))
        results: list[tuple[int, str | None, str | None, datetime | None]] = []
        for index, (item_id, code) in enumerate(item_ids):
            if index >= filled:
                results.append((item_id, None, None, None))
                continue
            if index == 0:
                tick(24, 48)
                audit.append(("STARTED", "OPEN", "IN_PROGRESS", analyst, None, clock))
            roll = rng.random()
            if roll < 0.12:
                result, comment = "FAIL", FAIL_COMMENTS[code]
            elif roll < 0.18 and code in ("INC-02", "CMP-03", "COL-02"):
                result, comment = "NA", NA_COMMENT
            else:
                result, comment = "PASS", None
            at = tick(0, 2)
            results.append((item_id, result, comment, at))
            audit.append(("ITEM_UPDATED", None, None, analyst, f"{code} set to {result}", at))

        submitted_at = approved_at = reason = None
        reviewer = None
        if status in ("SUBMITTED", "RETURNED", "APPROVED"):
            submitted_at = tick(2, 20)
            audit.append(("SUBMITTED", "IN_PROGRESS", "SUBMITTED", analyst, None, submitted_at))
        if status == "RETURNED":
            reason = RETURN_REASONS[rng.randrange(len(RETURN_REASONS))]
            reviewer = user_ids["reviewer1"]
            audit.append(("RETURNED", "SUBMITTED", "RETURNED", "reviewer1", reason, tick(4, 30)))
        if status == "APPROVED":
            approved_at = tick(4, 30)
            reviewer = user_ids["reviewer1"]
            audit.append(("APPROVED", "SUBMITTED", "APPROVED", "reviewer1", None, approved_at))

        cur = conn.execute(
            "INSERT INTO qc_reviews (review_no, loan_id, review_type, status, assigned_to,"
            " reviewer_id, due_date, return_reason, submitted_at, approved_at, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"R-{1001 + n}",
                loan_id,
                rng.choice(REVIEW_TYPES),
                status,
                user_ids[analyst],
                reviewer,
                due.isoformat(),
                reason,
                _ts(submitted_at) if submitted_at else None,
                _ts(approved_at) if approved_at else None,
                _ts(created),
                _ts(clock),
            ),
        )
        review_id = cur.lastrowid
        for item_id, result, comment, at in results:
            conn.execute(
                "INSERT INTO review_items (review_id, checklist_item_id, result, comment,"
                " updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    review_id,
                    item_id,
                    result,
                    comment,
                    user_ids[analyst] if result else None,
                    _ts(at) if at else None,
                ),
            )
        for event, from_status, to_status, actor, detail, at in audit:
            conn.execute(
                "INSERT INTO review_audit (review_id, event, from_status, to_status, actor, detail,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (review_id, event, from_status, to_status, actor, detail, _ts(at)),
            )
