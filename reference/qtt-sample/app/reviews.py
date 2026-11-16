"""Review rules: reading reviews and loans, saving checklist items, submit, approve, return, reopen.

Every rule violation raises ApiError, which the API turns into
`{"detail": {"code": ..., "message": ...}}` with the given status.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.seed import PRODUCTS

STATUSES = ("OPEN", "IN_PROGRESS", "SUBMITTED", "RETURNED", "APPROVED")
RESULTS = {"PASS": "Pass", "FAIL": "Fail", "NA": "N/A"}
CATEGORIES = ("Credit", "Income", "Collateral", "Compliance")
EDITABLE = ("OPEN", "IN_PROGRESS")
COMMENT_MAX = 500
RETURN_REASON_MIN = 15
QUEUE_LIMIT = 100
LOAN_LIMIT = 50


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- reading -----------------------------------------------------------------------------------

_SUMMARY_SQL = """
SELECT r.review_id, r.review_no, r.review_type, r.status, r.due_date, r.created_at,
       r.updated_at, r.submitted_at, r.approved_at, r.return_reason,
       l.loan_no, l.borrower_name, l.product_code,
       a.username AS assignee, a.full_name AS assignee_name,
       v.username AS reviewer
FROM qc_reviews r
JOIN loans l ON l.loan_id = r.loan_id
LEFT JOIN users a ON a.user_id = r.assigned_to
LEFT JOIN users v ON v.user_id = r.reviewer_id
"""


def _summary(row: sqlite3.Row) -> dict:
    return {
        "review_no": row["review_no"],
        "loan_no": row["loan_no"],
        "borrower_name": row["borrower_name"],
        "product_code": row["product_code"],
        "product": PRODUCTS.get(row["product_code"], row["product_code"]),
        "review_type": row["review_type"],
        "status": row["status"],
        "due_date": row["due_date"],
        "assignee": row["assignee"],
        "assignee_name": row["assignee_name"],
        "reviewer": row["reviewer"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "submitted_at": row["submitted_at"],
        "approved_at": row["approved_at"],
        "return_reason": row["return_reason"],
    }


def list_reviews(
    conn: sqlite3.Connection,
    status: str | None = None,
    assignee: str | None = None,
    q: str | None = None,
) -> list[dict]:
    status = (status or "").strip().upper() or None
    if status and status not in STATUSES:
        raise ApiError(
            422, "INVALID_STATUS", f"Status must be one of {', '.join(STATUSES)}; got '{status}'."
        )
    where, args = [], []
    if status:
        where.append("r.status = ?")
        args.append(status)
    if assignee:
        where.append("a.username = ?")
        args.append(assignee.strip())
    if q and q.strip():
        where.append(
            "(UPPER(r.review_no) LIKE ? OR UPPER(l.loan_no) LIKE ? OR UPPER(l.borrower_name) LIKE ?)"
        )
        like = f"%{q.strip().upper()}%"
        args += [like, like, like]
    sql = _SUMMARY_SQL + (" WHERE " + " AND ".join(where) if where else "")
    sql += f" ORDER BY r.review_no LIMIT {QUEUE_LIMIT}"
    return [_summary(r) for r in conn.execute(sql, args)]


def _review_row(conn: sqlite3.Connection, review_no: str) -> sqlite3.Row:
    row = conn.execute(_SUMMARY_SQL + " WHERE r.review_no = ?", (review_no.upper(),)).fetchone()
    if row is None:
        raise ApiError(404, "REVIEW_NOT_FOUND", f"Review {review_no} does not exist.")
    return row


def loan_dict(row: sqlite3.Row) -> dict:
    return {
        "loan_no": row["loan_no"],
        "borrower_name": row["borrower_name"],
        "co_borrower_name": row["co_borrower_name"],
        "product_code": row["product_code"],
        "product": PRODUCTS.get(row["product_code"], row["product_code"]),
        "loan_amount": float(row["loan_amount"]),
        "interest_rate": float(row["interest_rate"]),
        "closing_date": row["closing_date"],
        "branch_code": row["branch_code"],
        "branch_name": row["branch_name"],
        "loan_officer": row["loan_officer"],
        "property_state": row["property_state"],
    }


def items_of(conn: sqlite3.Connection, review_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.item_code, c.category, c.description, c.guidance, ri.result, ri.comment,
               ri.updated_at, u.username AS updated_by
        FROM review_items ri
        JOIN checklist_items c ON c.checklist_item_id = ri.checklist_item_id
        LEFT JOIN users u ON u.user_id = ri.updated_by
        WHERE ri.review_id = ?
        ORDER BY c.sort_order
        """,
        (review_id,),
    )
    return [
        {
            "item_id": r["item_code"],
            "category": r["category"],
            "description": r["description"],
            "guidance": r["guidance"],
            "result": r["result"],
            "comment": r["comment"],
            "updated_by": r["updated_by"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


def audit_of(conn: sqlite3.Connection, review_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT event, from_status, to_status, actor, detail, created_at FROM review_audit"
        " WHERE review_id = ? ORDER BY created_at, audit_id",
        (review_id,),
    )
    return [dict(r) for r in rows]


def allowed(review: dict, user: sqlite3.Row) -> dict[str, bool]:
    """What the signed-in user may do with the review right now."""
    analyst = user["role"] == "ANALYST" and review["assignee"] == user["username"]
    reviewer = user["role"] == "REVIEWER"
    status = review["status"]
    return {
        "edit_items": analyst and status in EDITABLE,
        "submit": analyst and status in EDITABLE,
        "reopen": analyst and status == "RETURNED",
        "approve": reviewer and status == "SUBMITTED",
        "return": reviewer and status == "SUBMITTED",
    }


def get_review(conn: sqlite3.Connection, review_no: str, user: sqlite3.Row) -> dict:
    row = _review_row(conn, review_no)
    review = _summary(row)
    loan = conn.execute(
        "SELECT l.* FROM loans l JOIN qc_reviews r ON r.loan_id = l.loan_id WHERE r.review_id = ?",
        (row["review_id"],),
    ).fetchone()
    items = items_of(conn, row["review_id"])
    answered = sum(1 for i in items if i["result"])
    review.update(
        loan=loan_dict(loan),
        items=items,
        findings=[
            {
                "item_id": i["item_id"],
                "category": i["category"],
                "description": i["description"],
                "comment": i["comment"],
            }
            for i in items
            if i["result"] == "FAIL"
        ],  # fmt: skip
        progress={"answered": answered, "total": len(items)},
        audit=audit_of(conn, row["review_id"]),
        locked=review["status"] == "APPROVED",
    )
    review["allowed"] = allowed(review, user)
    return review


def search_loans(conn: sqlite3.Connection, q: str | None) -> list[dict]:
    text = (q or "").strip().upper()
    rows = conn.execute(
        "SELECT * FROM loans WHERE UPPER(loan_no) LIKE ? OR UPPER(borrower_name) LIKE ?"
        f" OR UPPER(COALESCE(co_borrower_name, '')) LIKE ? ORDER BY loan_no LIMIT {LOAN_LIMIT}",
        (f"%{text}%",) * 3,
    )
    return [loan_dict(r) for r in rows]


def get_loan(conn: sqlite3.Connection, loan_no: str) -> dict:
    row = conn.execute("SELECT * FROM loans WHERE loan_no = ?", (loan_no.upper(),)).fetchone()
    if row is None:
        raise ApiError(404, "LOAN_NOT_FOUND", f"Loan {loan_no} does not exist.")
    loan = loan_dict(row)
    loan["reviews"] = [
        _summary(r)
        for r in conn.execute(
            _SUMMARY_SQL + " WHERE r.loan_id = ? ORDER BY r.review_no", (row["loan_id"],)
        )
    ]
    return loan


# --- changing ----------------------------------------------------------------------------------


def _audit(
    conn: sqlite3.Connection,
    review_id: int,
    event: str,
    actor: str,
    from_status: str | None = None,
    to_status: str | None = None,
    detail: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO review_audit (review_id, event, from_status, to_status, actor, detail,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (review_id, event, from_status, to_status, actor, detail, now()),
    )


def _set_status(conn: sqlite3.Connection, review_id: int, status: str, **columns: object) -> None:
    sets = ", ".join(["status = ?", "updated_at = ?", *(f"{c} = ?" for c in columns)])
    conn.execute(
        f"UPDATE qc_reviews SET {sets} WHERE review_id = ?",
        (status, now(), *columns.values(), review_id),
    )


def _locked(row: sqlite3.Row) -> None:
    if row["status"] == "APPROVED":
        raise ApiError(
            409, "REVIEW_LOCKED", f"Review {row['review_no']} is approved and can no longer change."
        )


def _must_be_assignee(row: sqlite3.Row, user: sqlite3.Row) -> None:
    if user["role"] != "ANALYST":
        raise ApiError(403, "FORBIDDEN_ROLE", "Only an analyst can work on checklist items.")
    if row["assignee"] != user["username"]:
        raise ApiError(
            403, "NOT_ASSIGNED", f"Review {row['review_no']} is assigned to {row['assignee']}."
        )


def save_item(
    conn: sqlite3.Connection,
    review_no: str,
    item_id: str,
    result: str | None,
    comment: str | None,
    user: sqlite3.Row,
) -> None:
    row = _review_row(conn, review_no)
    item = conn.execute(
        "SELECT ri.review_item_id, ri.result FROM review_items ri JOIN checklist_items c"
        " ON c.checklist_item_id = ri.checklist_item_id"
        " WHERE ri.review_id = ? AND c.item_code = ?",
        (row["review_id"], item_id.upper()),
    ).fetchone()
    if item is None:
        raise ApiError(404, "ITEM_NOT_FOUND", f"Checklist item {item_id} is not on {review_no}.")
    _must_be_assignee(row, user)
    _locked(row)
    if row["status"] == "SUBMITTED":
        raise ApiError(
            409, "INVALID_STATUS", "The review is submitted; items cannot change until it returns."
        )
    if row["status"] == "RETURNED":
        raise ApiError(409, "INVALID_STATUS", "Reopen the returned review before changing items.")
    result = (result or "").strip().upper()
    if result not in RESULTS:
        raise ApiError(422, "INVALID_RESULT", "Result must be PASS, FAIL or NA.")
    comment = (comment or "").strip()
    if len(comment) > COMMENT_MAX:
        raise ApiError(
            422, "COMMENT_TOO_LONG", f"Comment must be at most {COMMENT_MAX} characters."
        )
    if result == "FAIL" and not comment:
        raise ApiError(422, "COMMENT_REQUIRED", "A comment is required when the result is Fail.")
    conn.execute(
        "UPDATE review_items SET result = ?, comment = ?, updated_by = ?, updated_at = ?"
        " WHERE review_item_id = ?",
        (result, comment or None, user["user_id"], now(), item["review_item_id"]),
    )
    if row["status"] == "OPEN":
        _set_status(conn, row["review_id"], "IN_PROGRESS")
        _audit(conn, row["review_id"], "STARTED", user["username"], "OPEN", "IN_PROGRESS")
    else:
        conn.execute(
            "UPDATE qc_reviews SET updated_at = ? WHERE review_id = ?", (now(), row["review_id"])
        )
    _audit(
        conn,
        row["review_id"],
        "ITEM_UPDATED",
        user["username"],
        detail=f"{item_id.upper()} set to {result}",
    )
    conn.commit()


def submit(conn: sqlite3.Connection, review_no: str, user: sqlite3.Row) -> None:
    row = _review_row(conn, review_no)
    _must_be_assignee(row, user)
    _locked(row)
    if row["status"] not in EDITABLE:
        raise ApiError(
            409, "INVALID_STATUS", f"A review in status {row['status']} cannot be submitted."
        )
    missing = [i["item_id"] for i in items_of(conn, row["review_id"]) if not i["result"]]
    if missing:
        raise ApiError(
            422,
            "ITEMS_INCOMPLETE",
            f"{len(missing)} checklist item(s) have no result: {', '.join(missing)}.",
        )
    _set_status(conn, row["review_id"], "SUBMITTED", submitted_at=now(), return_reason=None)
    _audit(conn, row["review_id"], "SUBMITTED", user["username"], row["status"], "SUBMITTED")
    conn.commit()


def _must_be_reviewer(row: sqlite3.Row, user: sqlite3.Row) -> None:
    if user["role"] != "REVIEWER":
        raise ApiError(403, "FORBIDDEN_ROLE", "Only a reviewer can approve or return a review.")
    _locked(row)
    if row["status"] != "SUBMITTED":
        raise ApiError(
            409,
            "INVALID_STATUS",
            f"Only a submitted review can be decided; this is {row['status']}.",
        )


def approve(conn: sqlite3.Connection, review_no: str, user: sqlite3.Row) -> None:
    row = _review_row(conn, review_no)
    _must_be_reviewer(row, user)
    _set_status(conn, row["review_id"], "APPROVED", approved_at=now(), reviewer_id=user["user_id"])
    _audit(conn, row["review_id"], "APPROVED", user["username"], "SUBMITTED", "APPROVED")
    conn.commit()


def return_review(
    conn: sqlite3.Connection, review_no: str, reason: str | None, user: sqlite3.Row
) -> None:
    row = _review_row(conn, review_no)
    _must_be_reviewer(row, user)
    reason = (reason or "").strip()
    if len(reason) < RETURN_REASON_MIN:
        raise ApiError(
            422,
            "RETURN_REASON_REQUIRED",
            f"A reason of at least {RETURN_REASON_MIN} characters is required to return a review.",
        )
    if len(reason) > COMMENT_MAX:
        raise ApiError(
            422, "RETURN_REASON_TOO_LONG", f"Reason must be at most {COMMENT_MAX} characters."
        )
    _set_status(
        conn, row["review_id"], "RETURNED", return_reason=reason, reviewer_id=user["user_id"]
    )
    _audit(conn, row["review_id"], "RETURNED", user["username"], "SUBMITTED", "RETURNED", reason)
    conn.commit()


def reopen(conn: sqlite3.Connection, review_no: str, user: sqlite3.Row) -> None:
    row = _review_row(conn, review_no)
    _must_be_assignee(row, user)
    _locked(row)
    if row["status"] != "RETURNED":
        raise ApiError(
            409,
            "INVALID_STATUS",
            f"Only a returned review can be reopened; this is {row['status']}.",
        )
    _set_status(conn, row["review_id"], "IN_PROGRESS")
    _audit(conn, row["review_id"], "REOPENED", user["username"], "RETURNED", "IN_PROGRESS")
    conn.commit()


# --- checklist template (admin) ----------------------------------------------------------------


def checklist(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM checklist_items ORDER BY sort_order")
    return [dict(r) for r in rows]


def update_checklist_item(
    conn: sqlite3.Connection, item_code: str, description: str, is_active: bool
) -> None:
    description = description.strip()
    if not 10 <= len(description) <= 200:
        raise ApiError(
            422, "DESCRIPTION_LENGTH", "Description must be between 10 and 200 characters."
        )
    cur = conn.execute(
        "UPDATE checklist_items SET description = ?, is_active = ? WHERE item_code = ?",
        (description, 1 if is_active else 0, item_code.upper()),
    )
    if cur.rowcount == 0:
        raise ApiError(404, "ITEM_NOT_FOUND", f"Checklist item {item_code} does not exist.")
    conn.commit()
