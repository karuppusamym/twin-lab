import sqlite3
from collections import Counter

import pytest
from conftest import signed_in
from fastapi.testclient import TestClient

from app.main import create_app
from app.seed import TEST_PASSWORD

CODES = [
    "CRD-01", "CRD-02", "CRD-03", "INC-01", "INC-02", "INC-03",
    "COL-01", "COL-02", "COL-03", "CMP-01", "CMP-02", "CMP-03",
]  # fmt: skip
OPEN_A1 = "R-1001"  # OPEN, analyst1
IN_PROGRESS_A2 = "R-1002"
SUBMITTED_A1 = "R-1003"
APPROVED_A1 = "R-1005"
RETURNED_A2 = "R-1006"


def code(response) -> str:
    return response.json()["detail"]["code"]


def fill(client, review_no, fail=None):
    for item in CODES:
        body = {"result": "PASS", "comment": ""}
        if item == fail:
            body = {"result": "FAIL", "comment": "Second year of W-2s missing from the file."}
        assert client.put(f"/api/reviews/{review_no}/items/{item}", json=body).status_code == 200


# --- seed and health -----------------------------------------------------------------------------


def dump(path):
    conn = sqlite3.connect(path)
    tables = ["users", "loans", "qc_reviews", "checklist_items", "review_items", "review_audit"]
    out = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}
    conn.close()
    return out


def test_seed_is_deterministic(tmp_path):
    create_app(tmp_path / "a.db")
    create_app(tmp_path / "b.db")
    assert dump(tmp_path / "a.db") == dump(tmp_path / "b.db")


def test_seed_sizes_and_states(qtt, analyst1):
    rows = dump(qtt.state.db_path)
    assert len(rows["users"]) == 3
    assert len(rows["loans"]) == 80
    assert len(rows["qc_reviews"]) == 50
    assert len(rows["checklist_items"]) == 12
    assert len(rows["review_items"]) == 600
    statuses = Counter(r["status"] for r in analyst1.get("/api/reviews").json()["items"])
    assert statuses == {
        "OPEN": 15, "IN_PROGRESS": 10, "SUBMITTED": 10, "RETURNED": 5, "APPROVED": 10,
    }  # fmt: skip


def test_existing_database_is_kept(tmp_path, qtt, analyst1):
    fill(analyst1, OPEN_A1)
    again = create_app(qtt.state.db_path)
    client = signed_in(again, "analyst1")
    assert client.get(f"/api/reviews/{OPEN_A1}").json()["status"] == "IN_PROGRESS"


def test_health_needs_no_sign_in(qtt):
    body = TestClient(qtt).get("/api/health").json()
    assert body["status"] == "ok" and body["db"] == "ok"


# --- sign in -------------------------------------------------------------------------------------


def test_api_login_and_bearer(qtt):
    client = TestClient(qtt)
    ok = client.post("/api/login", json={"username": "reviewer1", "password": TEST_PASSWORD})
    assert ok.status_code == 200
    assert ok.json()["role"] == "REVIEWER"
    fresh = TestClient(qtt)
    me = fresh.get("/api/me", headers={"Authorization": f"Bearer {ok.json()['token']}"})
    assert me.json()["username"] == "reviewer1"


def test_api_login_sets_cookie_too(qtt):
    client = TestClient(qtt)
    client.post("/api/login", json={"username": "analyst1", "password": TEST_PASSWORD})
    assert client.get("/api/me").json()["username"] == "analyst1"


def test_wrong_password_is_refused(qtt):
    r = TestClient(qtt).post("/api/login", json={"username": "analyst1", "password": "nope"})
    assert r.status_code == 401
    assert code(r) == "INVALID_CREDENTIALS"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/reviews"),
        ("GET", f"/api/reviews/{OPEN_A1}"),
        ("PUT", f"/api/reviews/{OPEN_A1}/items/CRD-01"),
        ("POST", f"/api/reviews/{OPEN_A1}/submit"),
        ("POST", f"/api/reviews/{SUBMITTED_A1}/approve"),
        ("POST", f"/api/reviews/{SUBMITTED_A1}/return"),
        ("GET", "/api/loans"),
    ],
)
def test_api_needs_a_sign_in(qtt, method, path):
    r = TestClient(qtt).request(method, path, json={})
    assert r.status_code == 401
    assert r.json() == {"detail": {"code": "NOT_AUTHENTICATED", "message": "Sign in first."}}


def test_forged_token_is_refused(qtt):
    r = TestClient(qtt).get("/api/reviews", headers={"Authorization": "Bearer YW5hbHlzdDE.abc"})
    assert r.status_code == 401


# --- listing -------------------------------------------------------------------------------------


def test_list_filters_by_status_and_assignee(analyst1):
    body = analyst1.get("/api/reviews", params={"status": "open", "assignee": "analyst1"}).json()
    assert body["total"] == len(body["items"]) > 0
    assert {(i["status"], i["assignee"]) for i in body["items"]} == {("OPEN", "analyst1")}
    mine = analyst1.get("/api/reviews", params={"assignee": "me"}).json()["items"]
    assert {i["assignee"] for i in mine} == {"analyst1"}
    assert mine[0]["review_no"] == "R-1001"


def test_list_search_by_review_loan_or_borrower(analyst1):
    one = analyst1.get(f"/api/reviews/{OPEN_A1}").json()
    for q in (OPEN_A1.lower(), one["loan_no"], one["borrower_name"].split()[1].upper()):
        nos = [
            i["review_no"] for i in analyst1.get("/api/reviews", params={"q": q}).json()["items"]
        ]
        assert OPEN_A1 in nos


def test_list_rejects_unknown_status(analyst1):
    r = analyst1.get("/api/reviews", params={"status": "DONE"})
    assert r.status_code == 422 and code(r) == "INVALID_STATUS"


def test_review_detail_shape(analyst1):
    r = analyst1.get(f"/api/reviews/{OPEN_A1}").json()
    assert r["status"] == "OPEN"
    assert [i["item_id"] for i in r["items"]] == CODES
    assert {i["category"] for i in r["items"]} == {"Credit", "Income", "Collateral", "Compliance"}
    assert r["progress"] == {"answered": 0, "total": 12}
    assert r["loan"]["loan_no"].startswith("L-2000")
    assert r["allowed"]["edit_items"] and r["allowed"]["submit"]
    assert r["audit"][0]["event"] == "CREATED"


def test_unknown_review_is_404(analyst1):
    r = analyst1.get("/api/reviews/R-9999")
    assert r.status_code == 404 and code(r) == "REVIEW_NOT_FOUND"


# --- saving items --------------------------------------------------------------------------------


def test_saving_an_item_starts_the_review(analyst1):
    r = analyst1.put(f"/api/reviews/{OPEN_A1}/items/CRD-01", json={"result": "PASS"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "IN_PROGRESS"
    assert body["items"][0]["result"] == "PASS"
    assert body["items"][0]["updated_by"] == "analyst1"
    assert body["progress"]["answered"] == 1
    events = [a["event"] for a in body["audit"]]
    assert events[-2:] == ["STARTED", "ITEM_UPDATED"]
    assert body["audit"][-1]["detail"] == "CRD-01 set to PASS"


def test_fail_needs_a_comment(analyst1):
    for comment in (None, "", "   "):
        r = analyst1.put(
            f"/api/reviews/{OPEN_A1}/items/INC-01", json={"result": "FAIL", "comment": comment}
        )
        assert r.status_code == 422 and code(r) == "COMMENT_REQUIRED"
    assert analyst1.get(f"/api/reviews/{OPEN_A1}").json()["status"] == "OPEN"


def test_fail_with_comment_becomes_a_finding(analyst1):
    r = analyst1.put(
        f"/api/reviews/{OPEN_A1}/items/INC-01",
        json={"result": "FAIL", "comment": "Only one year of W-2s."},
    )
    assert r.status_code == 200
    assert r.json()["findings"] == [
        {
            "item_id": "INC-01",
            "category": "Income",
            "description": r.json()["items"][3]["description"],
            "comment": "Only one year of W-2s.",
        }
    ]


def test_na_and_lower_case_results_are_accepted(analyst1):
    r = analyst1.put(f"/api/reviews/{OPEN_A1}/items/inc-02", json={"result": "na"})
    assert r.status_code == 200
    assert r.json()["items"][4]["result"] == "NA"


@pytest.mark.parametrize("result", [None, "", "MAYBE"])
def test_result_must_be_pass_fail_or_na(analyst1, result):
    r = analyst1.put(f"/api/reviews/{OPEN_A1}/items/CRD-01", json={"result": result})
    assert r.status_code == 422 and code(r) == "INVALID_RESULT"


def test_comment_is_at_most_500_characters(analyst1):
    r = analyst1.put(
        f"/api/reviews/{OPEN_A1}/items/CRD-01", json={"result": "PASS", "comment": "x" * 501}
    )
    assert r.status_code == 422 and code(r) == "COMMENT_TOO_LONG"


def test_unknown_item_is_404(analyst1):
    r = analyst1.put(f"/api/reviews/{OPEN_A1}/items/XYZ-99", json={"result": "PASS"})
    assert r.status_code == 404 and code(r) == "ITEM_NOT_FOUND"


def test_only_the_assignee_saves_items(analyst2):
    r = analyst2.put(f"/api/reviews/{OPEN_A1}/items/CRD-01", json={"result": "PASS"})
    assert r.status_code == 403 and code(r) == "NOT_ASSIGNED"


def test_a_reviewer_cannot_save_items(reviewer):
    r = reviewer.put(f"/api/reviews/{OPEN_A1}/items/CRD-01", json={"result": "PASS"})
    assert r.status_code == 403 and code(r) == "FORBIDDEN_ROLE"


def test_submitted_review_items_cannot_change(analyst1):
    r = analyst1.put(f"/api/reviews/{SUBMITTED_A1}/items/CRD-01", json={"result": "PASS"})
    assert r.status_code == 409 and code(r) == "INVALID_STATUS"


def test_returned_review_must_be_reopened_first(analyst2):
    r = analyst2.put(f"/api/reviews/{RETURNED_A2}/items/CRD-01", json={"result": "PASS"})
    assert r.status_code == 409 and code(r) == "INVALID_STATUS"


def test_malformed_body_uses_the_error_shape(analyst1):
    r = analyst1.put(
        f"/api/reviews/{OPEN_A1}/items/CRD-01",
        content="not json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422 and code(r) == "VALIDATION_ERROR"


# --- submit --------------------------------------------------------------------------------------


def test_submit_needs_every_item(analyst1):
    analyst1.put(f"/api/reviews/{OPEN_A1}/items/CRD-01", json={"result": "PASS"})
    r = analyst1.post(f"/api/reviews/{OPEN_A1}/submit")
    assert r.status_code == 422 and code(r) == "ITEMS_INCOMPLETE"
    assert r.json()["detail"]["message"].startswith("11 checklist item(s) have no result: CRD-02")


def test_submit_moves_to_submitted(analyst1):
    fill(analyst1, OPEN_A1, fail="INC-01")
    r = analyst1.post(f"/api/reviews/{OPEN_A1}/submit")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "SUBMITTED"
    assert body["submitted_at"]
    assert [f["item_id"] for f in body["findings"]] == ["INC-01"]
    assert body["audit"][-1]["event"] == "SUBMITTED"
    assert not body["allowed"]["edit_items"]
    again = analyst1.post(f"/api/reviews/{OPEN_A1}/submit")
    assert again.status_code == 409 and code(again) == "INVALID_STATUS"


def test_only_the_assignee_submits(analyst2, reviewer):
    assert code(analyst2.post(f"/api/reviews/{OPEN_A1}/submit")) == "NOT_ASSIGNED"
    assert code(reviewer.post(f"/api/reviews/{OPEN_A1}/submit")) == "FORBIDDEN_ROLE"


# --- approve and return --------------------------------------------------------------------------


def test_approve_locks_the_review(reviewer, analyst1):
    r = reviewer.post(f"/api/reviews/{SUBMITTED_A1}/approve")
    assert r.status_code == 200
    assert r.json()["status"] == "APPROVED" and r.json()["locked"]
    assert r.json()["reviewer"] == "reviewer1"
    for call in (
        lambda: analyst1.put(f"/api/reviews/{SUBMITTED_A1}/items/CRD-01", json={"result": "PASS"}),
        lambda: analyst1.post(f"/api/reviews/{SUBMITTED_A1}/submit"),
        lambda: analyst1.post(f"/api/reviews/{SUBMITTED_A1}/reopen"),
        lambda: reviewer.post(f"/api/reviews/{SUBMITTED_A1}/approve"),
        lambda: reviewer.post(
            f"/api/reviews/{SUBMITTED_A1}/return", json={"reason": "Changed my mind on this one."}
        ),
    ):
        response = call()
        assert response.status_code == 409 and code(response) == "REVIEW_LOCKED"


def test_seeded_approved_review_is_read_only(analyst1):
    body = analyst1.get(f"/api/reviews/{APPROVED_A1}").json()
    assert body["locked"] and not any(body["allowed"].values())
    r = analyst1.put(f"/api/reviews/{APPROVED_A1}/items/CRD-01", json={"result": "PASS"})
    assert r.status_code == 409 and code(r) == "REVIEW_LOCKED"


def test_only_a_reviewer_decides(analyst1):
    for action in ("approve", "return"):
        r = analyst1.post(f"/api/reviews/{SUBMITTED_A1}/{action}", json={"reason": "x" * 20})
        assert r.status_code == 403 and code(r) == "FORBIDDEN_ROLE"


def test_only_submitted_reviews_are_decided(reviewer):
    r = reviewer.post(f"/api/reviews/{OPEN_A1}/approve")
    assert r.status_code == 409 and code(r) == "INVALID_STATUS"


@pytest.mark.parametrize("reason", [None, "", "Too short", "  fourteen chars  "])
def test_return_needs_a_reason_of_15_characters(reviewer, reason):
    r = reviewer.post(f"/api/reviews/{SUBMITTED_A1}/return", json={"reason": reason})
    assert r.status_code == 422 and code(r) == "RETURN_REASON_REQUIRED"


def test_return_then_reopen_then_resubmit(reviewer, analyst1):
    reason = "Attach the DTI recalculation worksheet."
    r = reviewer.post(f"/api/reviews/{SUBMITTED_A1}/return", json={"reason": reason})
    assert r.status_code == 200
    seen = analyst1.get(f"/api/reviews/{SUBMITTED_A1}").json()
    assert seen["status"] == "RETURNED" and seen["return_reason"] == reason
    assert seen["audit"][-1] == {**seen["audit"][-1], "event": "RETURNED", "detail": reason}
    assert seen["allowed"]["reopen"] and not seen["allowed"]["edit_items"]
    mine = analyst1.get("/api/reviews", params={"status": "RETURNED", "assignee": "me"}).json()
    assert SUBMITTED_A1 in [i["review_no"] for i in mine["items"]]

    reopened = analyst1.post(f"/api/reviews/{SUBMITTED_A1}/reopen")
    assert reopened.status_code == 200 and reopened.json()["status"] == "IN_PROGRESS"
    assert (
        analyst1.put(
            f"/api/reviews/{SUBMITTED_A1}/items/INC-03", json={"result": "PASS"}
        ).status_code
        == 200
    )
    resubmitted = analyst1.post(f"/api/reviews/{SUBMITTED_A1}/submit")
    assert resubmitted.json()["status"] == "SUBMITTED"
    assert resubmitted.json()["return_reason"] is None
    events = [a["event"] for a in resubmitted.json()["audit"]]
    assert events[-4:] == ["RETURNED", "REOPENED", "ITEM_UPDATED", "SUBMITTED"]


def test_reopen_needs_a_returned_review(analyst1, analyst2):
    r = analyst1.post(f"/api/reviews/{OPEN_A1}/reopen")
    assert r.status_code == 409 and code(r) == "INVALID_STATUS"
    assert code(analyst1.post(f"/api/reviews/{RETURNED_A2}/reopen")) == "NOT_ASSIGNED"
    assert analyst2.post(f"/api/reviews/{RETURNED_A2}/reopen").status_code == 200


# --- loans ---------------------------------------------------------------------------------------


def test_loan_search_by_number_and_name(analyst1):
    by_no = analyst1.get("/api/loans", params={"q": "l-200007"}).json()
    assert [i["loan_no"] for i in by_no["items"]] == ["L-200007"]
    name = by_no["items"][0]["borrower_name"]
    by_name = analyst1.get("/api/loans", params={"q": name.split()[1][1:4].lower()}).json()
    assert "L-200007" in [i["loan_no"] for i in by_name["items"]]
    assert analyst1.get("/api/loans", params={"q": "zzzz"}).json() == {"items": [], "total": 0}
    assert analyst1.get("/api/loans").json()["total"] == 50  # capped


def test_loan_detail_lists_its_reviews(analyst1):
    review = analyst1.get(f"/api/reviews/{OPEN_A1}").json()
    loan = analyst1.get(f"/api/loans/{review['loan_no']}").json()
    assert [r["review_no"] for r in loan["reviews"]] == [OPEN_A1]
    r = analyst1.get("/api/loans/L-999999")
    assert r.status_code == 404 and code(r) == "LOAN_NOT_FOUND"
