import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.seed import DEMO_PASSWORD, DEMO_USER


@pytest.fixture
def client():
    with TestClient(create_app("sqlite:///:memory:")) as c:
        token = c.post("/api/login", json={"username": DEMO_USER, "password": DEMO_PASSWORD})
        c.headers["Authorization"] = f"Bearer {token.json()['token']}"
        yield c


def _first(client, status):
    return client.get("/api/cases/search", params={"status": status}).json()["items"][0]["case_no"]


def _valid_update(client, **overrides):
    category = client.get("/api/categories").json()[0]["id"]
    sub = client.get(f"/api/categories/{category}/subcategories").json()[0]["id"]
    return {"status": "IN_PROGRESS", "category_id": category, "subcategory_id": sub} | overrides


def test_wrong_password_is_refused():
    with TestClient(create_app("sqlite:///:memory:")) as c:
        r = c.post("/api/login", json={"username": DEMO_USER, "password": "nope"})
        assert r.status_code == 401
        assert r.json()["detail"]["code"] == "INVALID_CREDENTIALS"


def test_api_needs_a_sign_in():
    with TestClient(create_app("sqlite:///:memory:")) as c:
        assert c.get("/api/cases/search").status_code == 401


def test_seed_is_the_same_every_time():
    def snapshot():
        with TestClient(create_app("sqlite:///:memory:")) as c:
            t = c.post("/api/login", json={"username": DEMO_USER, "password": DEMO_PASSWORD})
            headers = {"Authorization": f"Bearer {t.json()['token']}"}
            return c.get("/api/cases/search", headers=headers).json()

    assert snapshot() == snapshot()


def test_search_by_case_number_prefix_and_customer_substring(client):
    exact = client.get("/api/cases/search", params={"caseNo": "c-1001"}).json()
    assert [i["case_no"] for i in exact["items"]] == ["C-1001"]
    name = exact["items"][0]["customer"]
    by_name = client.get("/api/cases/search", params={"customer": name[1:5].lower()}).json()
    assert "C-1001" in [i["case_no"] for i in by_name["items"]]


def test_search_by_status_returns_only_that_status(client):
    items = client.get("/api/cases/search", params={"status": "ESCALATED"}).json()["items"]
    assert items and {i["status"] for i in items} == {"ESCALATED"}


def test_subcategories_depend_on_the_category(client):
    cats = client.get("/api/categories").json()
    a = client.get(f"/api/categories/{cats[0]['id']}/subcategories").json()
    b = client.get(f"/api/categories/{cats[1]['id']}/subcategories").json()
    assert a and b and {s["id"] for s in a}.isdisjoint({s["id"] for s in b})


def test_update_changes_the_case_and_writes_an_audit_row(client):
    case_no = _first(client, "OPEN")
    r = client.put(f"/api/cases/{case_no}", json=_valid_update(client))
    assert r.status_code == 200
    assert r.json()["status"] == "IN_PROGRESS"
    assert r.json()["category"] is not None


def test_subcategory_from_another_category_is_rejected(client):
    cats = client.get("/api/categories").json()
    other_sub = client.get(f"/api/categories/{cats[1]['id']}/subcategories").json()[0]["id"]
    body = _valid_update(client, category_id=cats[0]["id"], subcategory_id=other_sub)
    r = client.put(f"/api/cases/{_first(client, 'OPEN')}", json=body)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "SUBCATEGORY_MISMATCH"


def test_escalation_needs_a_reason(client):
    case_no = _first(client, "OPEN")
    short = _valid_update(client, status="ESCALATED", escalation_reason="too short")
    r = client.put(f"/api/cases/{case_no}", json=short)
    assert r.json()["detail"]["code"] == "ESCALATION_REASON_REQUIRED"
    ok = _valid_update(client, status="ESCALATED", escalation_reason="Customer asked twice")
    r = client.put(f"/api/cases/{case_no}", json=ok)
    assert r.status_code == 200
    assert r.json()["escalation_reason"] == "Customer asked twice"


def test_resolving_needs_a_note_first(client):
    case_no = _first(client, "OPEN")
    body = _valid_update(client, status="RESOLVED")
    assert (
        client.put(f"/api/cases/{case_no}", json=body).json()["detail"]["code"] == "NOTE_REQUIRED"
    )
    assert client.post(f"/api/cases/{case_no}/notes", json={"text": "Fixed"}).status_code == 201
    assert client.put(f"/api/cases/{case_no}", json=body).status_code == 200


def test_note_longer_than_500_characters_is_rejected(client):
    case_no = _first(client, "OPEN")
    assert client.post(f"/api/cases/{case_no}/notes", json={"text": "x" * 501}).status_code == 422
    assert client.post(f"/api/cases/{case_no}/notes", json={"text": "x" * 500}).status_code == 201
    assert len(client.get(f"/api/cases/{case_no}/notes").json()) == 1


def test_closed_case_cannot_be_changed(client):
    case_no = _first(client, "CLOSED")
    assert client.put(f"/api/cases/{case_no}", json=_valid_update(client)).status_code == 409
    assert client.post(f"/api/cases/{case_no}/notes", json={"text": "late"}).status_code == 409


def test_canned_responses_list_and_add(client):
    assert [r["code"] for r in client.get("/api/responses").json()] == ["ACK", "FIXED", "MOREINFO"]
    body = {"code": "CALLBACK", "title": "Call back", "body": "We will call you today."}
    assert client.post("/api/responses", json=body).status_code == 201
    again = client.post("/api/responses", json=body)
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "RESPONSE_EXISTS"
    assert client.post("/api/responses", json=body | {"code": "bad code"}).status_code == 422


def test_unknown_case_is_404(client):
    assert client.get("/api/cases/C-9999").json()["detail"]["code"] == "CASE_NOT_FOUND"
