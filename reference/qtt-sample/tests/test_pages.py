import re
from pathlib import Path

from conftest import signed_in
from fastapi.testclient import TestClient

from app.seed import TEST_PASSWORD

SAMPLE = Path(__file__).resolve().parents[1]


def ids(html: str) -> set[str]:
    return set(re.findall(r'\bid="([^"]+)"', html))


def browser(app, username):
    client = TestClient(app)
    r = client.post(
        "/login",
        data={"username": username, "password": TEST_PASSWORD, "next": "/queue"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    return client


def test_login_page_has_the_form(qtt):
    html = TestClient(qtt).get("/login").text
    assert {"loginForm", "username", "password", "btnLogin"} <= ids(html)


def test_wrong_password_shows_the_error(qtt):
    r = TestClient(qtt).post("/login", data={"username": "analyst1", "password": "bad"})
    assert r.status_code == 401
    assert 'id="loginError"' in r.text and "Invalid user id or password." in r.text


def test_sign_in_sets_the_session_cookie_and_redirects(qtt):
    client = TestClient(qtt)
    r = client.post(
        "/login",
        data={"username": "analyst1", "password": TEST_PASSWORD, "next": "/reviews/R-1001"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/reviews/R-1001"
    assert "QTTSESSION" in r.cookies


def test_open_redirect_is_ignored(qtt):
    r = TestClient(qtt).post(
        "/login",
        data={"username": "analyst1", "password": TEST_PASSWORD, "next": "//evil.test/x"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/queue"


def test_pages_need_a_sign_in(qtt):
    client = TestClient(qtt)
    for path in ("/queue", "/reviews/R-1001", "/loans", "/loans/L-200001", "/admin/checklist"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    assert client.get("/", follow_redirects=False).headers["location"] == "/queue"


def test_logout_clears_the_session(qtt):
    client = browser(qtt, "analyst1")
    client.get("/logout")
    assert client.get("/queue", follow_redirects=False).status_code == 303


def test_queue_lists_reviews_with_filters(qtt):
    client = browser(qtt, "analyst1")
    html = client.get("/queue").text
    assert {"queueFilters", "statusFilter", "reviewSearch", "mineOnly", "btnFilter"} <= ids(html)
    assert {"queueTable", "resultCount", "open-R-1001"} <= ids(html)
    assert html.count('class="review-row"') == 50
    open_mine = client.get("/queue", params={"status": "OPEN", "mine": "1"}).text
    statuses = re.findall(r'<td class="status"><span class="badge status-\w+">(\w+)<', open_mine)
    assigned = re.findall(r'<td class="assignee">(\w+)<', open_mine)
    assert statuses and set(statuses) == {"OPEN"} and set(assigned) == {"analyst1"}
    one = client.get("/queue", params={"q": "R-1001"}).text
    assert one.count('class="review-row"') == 1
    none = client.get("/queue", params={"q": "nothing-matches"}).text
    assert 'id="noReviews"' in none


def test_review_page_for_the_assigned_analyst(qtt):
    html = browser(qtt, "analyst1").get("/reviews/R-1001").text
    expected = {
        "reviewNo", "reviewStatus", "message", "loanSummary", "loanNo", "borrowerName", "product",
        "loanAmount", "closingDate", "branch", "checklist", "findings", "btnSubmit", "auditTable",
        "result-CRD-01", "comment-CRD-01", "save-CRD-01", "itemStatus-CRD-01", "result-CMP-03",
        "progress", "dueDate", "assignee",
    }  # fmt: skip
    assert expected <= ids(html)
    assert 'id="reviewerPanel"' not in html
    assert '<option value="FAIL" >Fail</option>' in html and ">N/A<" in html
    assert 'id="result-CRD-01" class="result-select" name="result" >' in html  # enabled
    assert "/static/review.js" in html


def test_review_page_for_another_analyst_is_read_only(qtt):
    html = browser(qtt, "analyst2").get("/reviews/R-1001").text
    assert 'id="result-CRD-01" class="result-select" name="result" disabled' in html
    assert re.search(r'id="btnSubmit"[^>]*disabled', html)


def test_review_page_for_the_reviewer(qtt):
    html = browser(qtt, "reviewer1").get("/reviews/R-1003").text
    assert {"reviewerPanel", "returnReason", "btnApprove", "btnReturn"} <= ids(html)
    assert 'id="btnSubmit"' not in html
    assert not re.search(r'id="btnApprove"[^>]*disabled', html)


def test_approved_review_shows_the_lock(qtt):
    html = browser(qtt, "analyst1").get("/reviews/R-1005").text
    assert re.search(r'id="lockNotice" class="notice notice-locked" >', html)
    assert 'id="save-CRD-01" type="button" class="btn btn-small btn-save-item" disabled' in html


def test_returned_review_shows_reason_and_reopen(qtt):
    html = browser(qtt, "analyst2").get("/reviews/R-1006").text
    assert re.search(r'id="returnNotice" class="notice notice-returned" >', html)
    reason = re.search(r'id="returnReasonText">([^<]+)<', html).group(1)
    assert len(reason) >= 15
    assert re.search(r'id="btnReopen" type="button" class="btn" >', html)


def test_unknown_review_page_is_404(qtt):
    r = browser(qtt, "analyst1").get("/reviews/R-0000")
    assert r.status_code == 404 and 'data-code="REVIEW_NOT_FOUND"' in r.text


def test_loan_search_page(qtt):
    client = browser(qtt, "analyst1")
    empty = client.get("/loans").text
    assert {"loanSearchForm", "loanQuery", "btnSearch", "searchHint"} <= ids(empty)
    found = client.get("/loans", params={"q": "L-20000"}).text
    assert {"loanResults", "loanCount", "loan-L-200001"} <= ids(found)
    assert found.count('class="loan-row"') == 9
    none = client.get("/loans", params={"q": "qqqq"}).text
    assert 'id="noResults"' in none


def test_loan_detail_page(qtt):
    client = browser(qtt, "analyst1")
    html = client.get("/loans/L-200002").text
    assert {"loanNo", "borrowerName", "loanAmount", "interestRate", "loanReviews"} <= ids(html)
    assert 'href="/reviews/R-1001"' in html
    assert client.get("/loans/L-000000").status_code == 404


def test_admin_checklist_is_for_reviewers(qtt):
    html = browser(qtt, "reviewer1").get("/admin/checklist").text
    assert {"templateTable", "tplDesc-CRD-01", "tplActive-CRD-01", "tplSave-CRD-01"} <= ids(html)
    denied = browser(qtt, "analyst1").get("/admin/checklist")
    assert denied.status_code == 403 and 'data-code="FORBIDDEN_ROLE"' in denied.text
    assert browser(qtt, "analyst1").post("/admin/checklist/CRD-01").status_code == 403


def test_admin_checklist_edit(qtt):
    client = browser(qtt, "reviewer1")
    r = client.post(
        "/admin/checklist/CRD-01",
        data={"description": "Credit report dated within 90 days of the note date"},
    )
    assert r.status_code == 200 and 'id="adminMessage"' in r.text and "Saved CRD-01" in r.text
    assert "within 90 days" in r.text
    assert not re.search(r'id="tplActive-CRD-01"[^>]*checked', r.text)
    bad = client.post("/admin/checklist/CRD-01", data={"description": "short"})
    assert bad.status_code == 422 and "between 10 and 200" in bad.text
    missing = client.post("/admin/checklist/ZZZ-01", data={"description": "A long description"})
    assert missing.status_code == 404


def test_api_and_page_share_the_session(qtt):
    client = browser(qtt, "analyst1")
    assert client.get("/api/me").json()["username"] == "analyst1"
    api_client = signed_in(qtt, "analyst1")
    api_client.headers.pop("Authorization")
    assert api_client.get("/queue", follow_redirects=False).status_code == 200


def test_static_assets_are_served(qtt):
    client = TestClient(qtt)
    assert "saveItem" in client.get("/static/review.js").text
    assert client.get("/static/qtt.css").status_code == 200


def test_sqlserver_ddl_lists_every_table():
    ddl = (SAMPLE / "db" / "ddl-sqlserver.sql").read_text(encoding="utf-8")
    for table in ("users", "loans", "qc_reviews", "checklist_items", "review_items"):
        assert f"CREATE TABLE [dbo].[{table}](" in ddl
        assert f"CONSTRAINT [PK_{table}] PRIMARY KEY" in ddl
    assert "CREATE TABLE [dbo].[review_audit](" in ddl
    assert ddl.count("IDENTITY(1,1)") == 6
    assert "FK_review_items_qc_reviews" in ddl and "\nGO\n" in ddl
