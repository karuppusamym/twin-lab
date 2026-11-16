"""QC reviews: reading them in the shape the API returns, and the recorded rules (R1-R4)."""

from __future__ import annotations

from datetime import UTC, date, datetime

PRODUCTS = {
    "F30": "Fixed 30",
    "F15": "Fixed 15",
    "J30": "Fixed Jumbo 30",
    "A71": "ARM 7/1",
    "A51": "ARM 5/1",
    "VA": "VA",
    "FHA": "FHA 30",
}
STATUSES = ["OPEN", "IN_PROGRESS", "SUBMITTED", "RETURNED", "APPROVED"]
EDITABLE = ("OPEN", "IN_PROGRESS", "RETURNED")
LOCKED = ("SUBMITTED", "APPROVED")
RESULTS = {"PASS": "Pass", "FAIL": "Fail", "NA": "N/A"}
MIN_RETURN_REASON = 15


class RuleError(Exception):
    """A business-rule failure, answered as {"detail": {"code", "message"}}."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def product_name(code: str) -> str:
    return PRODUCTS.get(code, code)


def ts(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def ds(value: date | None) -> str | None:
    return value.isoformat() if value else None


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


SUMMARY_SQL = """
SELECT r.review_id, r.review_no, r.review_type, r.status, r.due_date, r.return_reason,
       r.created_at, r.updated_at, r.submitted_at, r.approved_at, r.assigned_to, r.reviewer_id,
       l.loan_id, l.loan_no, l.borrower_name, l.co_borrower_name, l.product_code, l.loan_amount,
       l.interest_rate, l.closing_date, l.branch_code, l.branch_name, l.loan_officer,
       l.property_state,
       a.username AS assignee, a.full_name AS assignee_name, v.username AS reviewer
FROM qc_reviews r
JOIN loans l ON l.loan_id = r.loan_id
LEFT JOIN users a ON a.user_id = r.assigned_to
LEFT JOIN users v ON v.user_id = r.reviewer_id
"""


def summary(row: dict) -> dict:
    return {
        "review_no": row["review_no"],
        "loan_no": row["loan_no"],
        "borrower_name": row["borrower_name"],
        "product_code": row["product_code"],
        "product": product_name(row["product_code"]),
        "review_type": row["review_type"],
        "status": row["status"],
        "due_date": ds(row["due_date"]),
        "assignee": row["assignee"],
        "assignee_name": row["assignee_name"],
        "reviewer": row["reviewer"],
        "created_at": ts(row["created_at"]),
        "updated_at": ts(row["updated_at"]),
        "submitted_at": ts(row["submitted_at"]),
        "approved_at": ts(row["approved_at"]),
        "return_reason": row["return_reason"],
    }


def list_reviews(conn, status: str = "", q: str = "", mine_user_id: int | None = None) -> list:
    where, params = [], []
    if status:
        where.append("r.status = %s")
        params.append(status)
    if q:
        where.append("(r.review_no ILIKE %s OR l.loan_no ILIKE %s OR l.borrower_name ILIKE %s)")
        params += [f"%{q}%"] * 3
    if mine_user_id is not None:
        where.append("r.assigned_to = %s")
        params.append(mine_user_id)
    sql = SUMMARY_SQL + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY r.review_no"
    return [summary(r) for r in conn.execute(sql, params).fetchall()]


def _row(conn, review_no: str) -> dict | None:
    return conn.execute(SUMMARY_SQL + " WHERE r.review_no = %s", (review_no,)).fetchone()


def allowed(row: dict, user: dict) -> dict:
    is_assignee = user["role"] == "ANALYST" and row["assigned_to"] == user["user_id"]
    can_edit = is_assignee and row["status"] in EDITABLE
    is_reviewer = user["role"] == "REVIEWER" and row["status"] == "SUBMITTED"
    return {
        "edit_items": can_edit,
        "submit": can_edit,
        # No reopen route was recorded, so the demo copy never offers it.
        "reopen": False,
        "approve": is_reviewer,
        "return": is_reviewer,
    }


def detail(conn, review_no: str, user: dict) -> dict | None:
    row = _row(conn, review_no)
    if row is None:
        return None
    items = conn.execute(
        """
        SELECT c.item_code, c.category, c.description, c.guidance, i.result, i.comment,
               u.username AS updated_by, i.updated_at
        FROM review_items i
        JOIN checklist_items c ON c.checklist_item_id = i.checklist_item_id
        LEFT JOIN users u ON u.user_id = i.updated_by
        WHERE i.review_id = %s
        ORDER BY c.sort_order, c.item_code
        """,
        (row["review_id"],),
    ).fetchall()
    audit = conn.execute(
        "SELECT event, from_status, to_status, actor, detail, created_at FROM review_audit"
        " WHERE review_id = %s ORDER BY created_at, audit_id",
        (row["review_id"],),
    ).fetchall()
    out = summary(row)
    out["loan"] = {
        "loan_no": row["loan_no"],
        "borrower_name": row["borrower_name"],
        "co_borrower_name": row["co_borrower_name"],
        "product_code": row["product_code"],
        "product": product_name(row["product_code"]),
        "loan_amount": float(row["loan_amount"]),
        "interest_rate": float(row["interest_rate"]),
        "closing_date": ds(row["closing_date"]),
        "branch_code": row["branch_code"],
        "branch_name": row["branch_name"],
        "loan_officer": row["loan_officer"],
        "property_state": row["property_state"],
    }
    out["items"] = [
        {
            "item_id": i["item_code"],
            "category": i["category"],
            "description": i["description"],
            "guidance": i["guidance"],
            "result": i["result"],
            "comment": i["comment"],
            "updated_by": i["updated_by"],
            "updated_at": ts(i["updated_at"]),
        }
        for i in items
    ]
    out["findings"] = [
        {
            "item_id": i["item_code"],
            "category": i["category"],
            "description": i["description"],
            "comment": i["comment"] or "",
        }
        for i in items
        if i["result"] == "FAIL"
    ]
    out["progress"] = {
        "answered": sum(1 for i in items if i["result"]),
        "total": len(items),
    }
    out["audit"] = [
        {
            "event": a["event"],
            "from_status": a["from_status"],
            "to_status": a["to_status"],
            "actor": a["actor"],
            "detail": a["detail"],
            "created_at": ts(a["created_at"]),
        }
        for a in audit
    ]
    out["locked"] = row["status"] in LOCKED
    out["allowed"] = allowed(row, user)
    return out


def _audit(conn, review_id, event, from_s, to_s, actor, detail_text, when) -> None:
    conn.execute(
        "INSERT INTO review_audit (review_id, event, from_status, to_status, actor, detail,"
        " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (review_id, event, from_s, to_s, actor, detail_text, when),
    )


def _require(conn, review_no: str):
    row = conn.execute(
        "SELECT * FROM qc_reviews WHERE review_no = %s FOR UPDATE", (review_no,)
    ).fetchone()
    if row is None:
        raise RuleError(404, "NOT_FOUND", f"Review {review_no} was not found.")
    return row


def _not_allowed(review_no: str) -> RuleError:
    return RuleError(409, "NOT_ALLOWED", f"Review {review_no} cannot be changed now.")


def save_item(conn, review_no: str, item_code: str, body: dict, user: dict) -> None:
    review = _require(conn, review_no)
    if not allowed(review, user)["edit_items"]:
        raise _not_allowed(review_no)
    item = conn.execute(
        "SELECT i.review_item_id FROM review_items i"
        " JOIN checklist_items c ON c.checklist_item_id = i.checklist_item_id"
        " WHERE i.review_id = %s AND c.item_code = %s",
        (review["review_id"], item_code),
    ).fetchone()
    if item is None:
        raise RuleError(404, "NOT_FOUND", f"Checklist item {item_code} was not found.")
    result = str(body.get("result") or "").strip().upper()
    comment = str(body.get("comment") or "").strip()
    if result not in RESULTS:
        raise RuleError(422, "INVALID_RESULT", "Choose Pass, Fail or N/A.")
    if result == "FAIL" and not comment:
        # R2
        raise RuleError(422, "COMMENT_REQUIRED", "A comment is required when the result is Fail.")
    when = now()
    conn.execute(
        "UPDATE review_items SET result = %s, comment = %s, updated_by = %s, updated_at = %s"
        " WHERE review_item_id = %s",
        (result, comment or None, user["user_id"], when, item["review_item_id"]),
    )
    if review["status"] == "OPEN":
        _audit(
            conn,
            review["review_id"],
            "STARTED",
            "OPEN",
            "IN_PROGRESS",
            user["username"],
            None,
            when,
        )
        conn.execute(
            "UPDATE qc_reviews SET status = 'IN_PROGRESS' WHERE review_id = %s",
            (review["review_id"],),
        )
    _audit(
        conn,
        review["review_id"],
        "ITEM_UPDATED",
        None,
        None,
        user["username"],
        f"{item_code} set to {result}",
        when,
    )
    conn.execute(
        "UPDATE qc_reviews SET updated_at = %s WHERE review_id = %s", (when, review["review_id"])
    )


def submit(conn, review_no: str, user: dict) -> None:
    review = _require(conn, review_no)
    if not allowed(review, user)["submit"]:
        raise _not_allowed(review_no)
    missing = [
        r["item_code"]
        for r in conn.execute(
            "SELECT c.item_code FROM review_items i"
            " JOIN checklist_items c ON c.checklist_item_id = i.checklist_item_id"
            " WHERE i.review_id = %s AND i.result IS NULL ORDER BY c.sort_order, c.item_code",
            (review["review_id"],),
        ).fetchall()
    ]
    if missing:
        # R1 / R3
        raise RuleError(
            422,
            "ITEMS_INCOMPLETE",
            f"{len(missing)} checklist item(s) have no result: {', '.join(missing)}.",
        )
    when = now()
    conn.execute(
        "UPDATE qc_reviews SET status = 'SUBMITTED', submitted_at = %s, updated_at = %s"
        " WHERE review_id = %s",
        (when, when, review["review_id"]),
    )
    _audit(
        conn,
        review["review_id"],
        "SUBMITTED",
        review["status"],
        "SUBMITTED",
        user["username"],
        None,
        when,
    )


def return_review(conn, review_no: str, body: dict, user: dict) -> None:
    review = _require(conn, review_no)
    if not allowed(review, user)["return"]:
        raise _not_allowed(review_no)
    reason = str(body.get("reason") or "").strip()
    if len(reason) < MIN_RETURN_REASON:
        # R4
        raise RuleError(
            422,
            "RETURN_REASON_REQUIRED",
            "A reason of at least 15 characters is required to return a review.",
        )
    when = now()
    conn.execute(
        "UPDATE qc_reviews SET status = 'RETURNED', return_reason = %s, reviewer_id = %s,"
        " updated_at = %s WHERE review_id = %s",
        (reason, user["user_id"], when, review["review_id"]),
    )
    _audit(
        conn,
        review["review_id"],
        "RETURNED",
        "SUBMITTED",
        "RETURNED",
        user["username"],
        reason,
        when,
    )
