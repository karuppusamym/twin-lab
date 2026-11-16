"""End to end with Docker: Postgres, the showcase app and the lab agent in front of both.

Skipped when Docker is not available. Brings the stack in compose.yaml up once for the module and
always tears it down with its volumes. Host ports 18210 (agent) and 18211 (DB link proxy).

Run alone, with timings printed:
    python -m pytest -q -s lab/lab-agent/tests/test_docker_integration.py
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import shutil
import statistics
import subprocess
import time
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")

pytestmark = pytest.mark.docker

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
COMPOSE = HERE / "compose.yaml"
PROJECT = "tl-lab-agent-test"
BASE = "http://127.0.0.1:18210"
DB_PROXY = "postgresql://lab:lab-test-only@127.0.0.1:18211/showcase"
TOKEN = {"X-Lab-Token": "test-token"}
USER, PASSWORD = "demo.user", "workbench-demo"  # synthetic seed credentials of the showcase app


def _docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _run(*args: str, env: dict | None = None, timeout: int = 900) -> None:
    proc = subprocess.run(args, capture_output=True, text=True, env=env, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}")


def _compose(env: dict, *args: str, timeout: int = 300) -> None:
    _run("docker", "compose", "-f", str(COMPOSE), "-p", PROJECT, *args, env=env, timeout=timeout)


def _make_cert(directory: Path) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "db")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=7))
        .sign(key, hashes.SHA256())
    )
    (directory / "server.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (directory / "server.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )


def _wait_healthy(client: httpx.Client, seconds: float = 180) -> None:
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            r = client.get("/_lab/health", headers=TOKEN)
            last = r.text
            if r.status_code == 200:
                return
        except httpx.HTTPError as exc:
            last = repr(exc)
        time.sleep(1)
    raise RuntimeError(f"agent not healthy in {seconds}s: {last}")


class Lab:
    def __init__(self, env: dict) -> None:
        self.env = env
        self.http = httpx.Client(base_url=BASE, timeout=60)
        self.timings: dict[str, float] = {}

    def lab(self, method: str, path: str, **kw) -> httpx.Response:
        return self.http.request(method, path, headers=TOKEN, **kw)

    def login(self) -> dict:
        r = self.http.post("/api/login", json={"username": USER, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def timed(self, method: str, path: str, **kw) -> tuple[httpx.Response, float]:
        t = time.perf_counter()
        r = self.http.request(method, path, **kw)
        return r, time.perf_counter() - t


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    if os.environ.get("TL_SKIP_DOCKER") == "1" or not _docker_ok():
        pytest.skip("Docker is not available")
    pytest.importorskip("cryptography")
    certs = tmp_path_factory.mktemp("certs")
    _make_cert(certs)
    env = {**os.environ, "TL_CERT_DIR": str(certs)}
    if subprocess.run(
        ["docker", "image", "inspect", "twinlab/showcase:dev"], capture_output=True
    ).returncode:
        _run("docker", "build", "-t", "twinlab/showcase:dev", str(REPO / "apps" / "showcase"))
    _run(
        "docker", "build", "-f", str(REPO / "lab/lab-agent/Dockerfile"),
        "-t", "twinlab/lab-agent:dev", str(REPO),
    )  # fmt: skip
    _compose(env, "down", "-v", "--remove-orphans")
    lab = Lab(env)
    try:
        _compose(env, "up", "-d")
        _wait_healthy(lab.http)
        yield lab
    finally:
        lab.http.close()
        if lab.timings:
            print("\nmeasured:", {k: round(v, 3) for k, v in lab.timings.items()})
        try:
            subprocess.run(
                ["docker", "compose", "-f", str(COMPOSE), "-p", PROJECT, "logs", "--no-color", "agent"],
                env=env, capture_output=True, timeout=60,
            )  # fmt: skip
        finally:
            _compose(env, "down", "-v", "--remove-orphans")


def pack_cases() -> list[dict]:
    """Twelve synthetic cases on the seed's customers (1-40), categories and sub-categories."""
    rows = []
    statuses = ["OPEN", "OPEN", "OPEN", "IN_PROGRESS", "ESCALATED", "OPEN"] * 2
    for i, status in enumerate(statuses, start=1):
        categorised = status != "OPEN"
        rows.append(
            {
                "id": 500 + i,
                "case_no": f"C-{5000 + i}",
                "customer_id": i,
                "title": f"Pack case {i}",
                "status": status,
                "priority": ["LOW", "MEDIUM", "HIGH"][i % 3],
                "category_id": 1 if categorised else None,
                "subcategory_id": 1 if categorised else None,
                "escalation_reason": "Customer asked for a manager"
                if status == "ESCALATED"
                else None,
                "opened_at": f"2026-11-{i:02d}T09:00:00+00:00",
                "updated_at": f"2026-11-{i:02d}T09:00:00+00:00",
            }
        )
    return rows


# --- the tests, in order: each builds on the pack loaded by test_02 ----------------------------


def test_01_app_works_through_agent(stack: Lab):
    health = stack.lab("GET", "/_lab/health")
    assert health.json() == {"app": "ok", "db": "ok"}
    assert stack.http.get("/_lab/health").status_code == 401  # token required
    auth = stack.login()
    r = stack.http.get("/api/cases/search", params={"status": "OPEN"}, headers=auth)
    assert r.status_code == 200 and r.json()["count"] > 0
    assert stack.http.get("/api/cases/search").status_code == 401  # the app's own rule
    page = stack.http.get("/cases")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    info = stack.lab("GET", "/_lab/info").json()
    assert info["app"] == "showcase"
    tables = {t["name"]: t for t in info["tables"]}
    assert {"cases", "case_note", "case_audit", "app_user"} <= set(tables)
    note_case = next(c for c in tables["case_note"]["columns"] if c["name"] == "case_id")
    assert note_case["references"] == {"table": "cases", "column": "id"}
    recent = stack.lab("GET", "/_lab/requests", params={"limit": 5}).json()
    assert any(x["path"] == "/api/cases/search" and x["status"] == 200 for x in recent)

    # The image's own HEALTHCHECK (with the token) reports healthy.
    deadline, status = time.monotonic() + 90, ""
    while time.monotonic() < deadline:
        status = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Health.Status}}", f"{PROJECT}-agent-1"],
            capture_output=True, text=True,
        ).stdout.strip()  # fmt: skip
        if status == "healthy":
            break
        time.sleep(2)
    assert status == "healthy"


def test_02_load_pack_replaces_cases_and_clears_children(stack: Lab):
    before = stack.lab("GET", "/_lab/data").json()
    assert before["case_note"] and before["case_audit"]

    bad = pack_cases()
    bad[1]["opened_at"] = "yesterday"
    bad[2]["customer_id"] = 999
    bad[3]["colour"] = "blue"
    r = stack.lab("POST", "/_lab/data/load", json={"rows": {"cases": bad}})
    assert r.status_code == 422
    problems = r.json()["detail"]["problems"]
    found = {(p["table"], p["row"], p["column"]) for p in problems}
    assert {("cases", 2, "opened_at"), ("cases", 4, "colour")} <= found
    # All or nothing: nothing changed.
    assert stack.lab("GET", "/_lab/data").json() == before
    bad_fk = pack_cases()
    bad_fk[2]["customer_id"] = 999
    r = stack.lab("POST", "/_lab/data/load", json={"rows": {"cases": bad_fk}})
    assert r.status_code == 422
    assert ("cases", 3, "customer_id") in {
        (p["table"], p["row"], p["column"]) for p in r.json()["detail"]["problems"]
    }

    r = stack.lab(
        "POST",
        "/_lab/data/load",
        json={"rows": {"cases": pack_cases()}, "label": "Test pack", "pack": "test-pack"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["replaced"] == ["cases"]
    assert body["cleared"] == ["case_audit", "case_note"]
    assert body["counts"] == {"cases": 12, "case_audit": 0, "case_note": 0}
    assert body["baseline"]["label"] == "Test pack"

    after = stack.lab(
        "GET", "/_lab/data", params={"tables": "cases,case_note,case_audit,customer"}
    ).json()
    assert [c["case_no"] for c in after["cases"]] == [f"C-{5000 + i}" for i in range(1, 13)]
    assert after["case_note"] == [] and after["case_audit"] == []
    assert after["customer"] == before["customer"]  # untouched parent
    assert after["cases"][0]["opened_at"] == "2026-11-01T09:00:00+00:00"

    auth = stack.login()
    found = stack.http.get("/api/cases/search", params={"caseNo": "C-50"}, headers=auth).json()
    assert found["count"] == 12
    assert (
        stack.http.get("/api/cases/search", params={"caseNo": "C-10"}, headers=auth).json()["count"]
        == 0
    )


def _change_things(stack: Lab, auth: dict) -> int:
    r = stack.http.post(
        "/api/cases/C-5001/notes", json={"text": "Called the customer."}, headers=auth
    )
    assert r.status_code == 201, r.text
    r = stack.http.put(
        "/api/cases/C-5002",
        json={"status": "IN_PROGRESS", "category_id": 2, "subcategory_id": 3},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    return stack.http.post("/api/cases/C-5004/notes", json={"text": "x"}, headers=auth).json()["id"]


def test_03_reset_restores_row_for_row(stack: Lab):
    baseline = stack.lab("GET", "/_lab/data").json()
    auth = stack.login()
    last_note_id = _change_things(stack, auth)
    changed = stack.lab("GET", "/_lab/data").json()
    assert changed != baseline and len(changed["case_note"]) == 2

    t = time.perf_counter()
    r = stack.lab("POST", "/_lab/reset")
    stack.timings["reset_s"] = time.perf_counter() - t
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["cases"] == 12
    assert stack.lab("GET", "/_lab/data").json() == baseline

    # Sequences are back too: the same actions give the same ids.
    assert _change_things(stack, auth) == last_note_id
    stack.lab("POST", "/_lab/reset")
    assert stack.lab("GET", "/_lab/data").json() == baseline


def test_04_session_reports_exact_changes(stack: Lab):
    from labctl.models import Session

    assert stack.lab("POST", "/_lab/reset").status_code == 200
    auth = stack.login()
    r = stack.lab(
        "POST",
        "/_lab/session/start",
        json={"automation": "uipath-case-update", "label": "note+resolve"},
    )
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    assert stack.lab("POST", "/_lab/session/start", json={}).status_code == 409

    note = stack.http.post(
        "/api/cases/C-5003/notes", json={"text": "Fixed, confirmed."}, headers=auth
    )
    assert note.status_code == 201
    resolved = stack.http.put(
        "/api/cases/C-5003",
        json={"status": "RESOLVED", "category_id": 1, "subcategory_id": 1},
        headers=auth,
    )
    assert resolved.status_code == 200, resolved.text

    r = stack.lab("POST", "/_lab/session/stop")
    assert r.status_code == 200, r.text
    doc = r.json()
    session = Session.model_validate(doc)
    assert session.session_id == sid and session.automation == "uipath-case-update"
    assert session.apps[0].app == "showcase" and session.apps[0].data_pack == "test-pack"

    data = stack.lab("GET", "/_lab/data", params={"tables": "case_audit"}).json()
    audit_ids = sorted(str(a["id"]) for a in data["case_audit"])
    assert len(audit_ids) == 2
    got = sorted((c.table, c.op, c.key) for c in session.changes)
    expected = sorted(
        [("case_note", "insert", str(note.json()["id"])), ("cases", "update", "503")]
        + [("case_audit", "insert", k) for k in audit_ids]
    )
    assert got == expected

    assert session.summary.requests == 2 and session.summary.errors == 0
    assert [(q.method, q.path) for q in session.requests] == [
        ("POST", "/api/cases/C-5003/notes"),
        ("PUT", "/api/cases/C-5003"),
    ]
    assert session.summary.waiting_on_app_s <= session.summary.wall_s

    detail = stack.lab("GET", f"/_lab/sessions/{sid}/detail").json()
    upd = next(c for c in detail["changes"] if c["op"] == "update")
    assert {"status", "category_id", "subcategory_id", "updated_at"} == set(upd["columns"])
    assert upd["before"]["status"] == "OPEN" and upd["after"]["status"] == "RESOLVED"
    listed = stack.lab("GET", "/_lab/sessions").json()
    assert listed[0]["session_id"] == sid and listed[0]["label"] == "note+resolve"
    assert stack.lab("GET", f"/_lab/sessions/{sid}").json() == doc


def _median(stack: Lab, path: str, headers: dict, n: int = 5) -> float:
    times = []
    for _ in range(n):
        r, s = stack.timed("GET", path, headers=headers)
        assert r.status_code == 200, r.text
        times.append(s)
    return statistics.median(times)


def test_05_http_latency_rule_slows_only_matching_route(stack: Lab):
    auth = stack.login()
    rule = {
        "id": "slow-search",
        "seed": 5,
        "http": [{"match": "GET /api/cases/search*", "latency_ms": {"p50": 800, "p95": 800}}],
    }
    assert stack.lab("PUT", "/_lab/condition", json=rule).status_code == 200
    try:
        search = [
            stack.timed("GET", "/api/cases/search?caseNo=C-50", headers=auth)[1] for _ in range(3)
        ]
        other = [stack.timed("GET", "/api/categories", headers=auth)[1] for _ in range(3)]
        recent = stack.lab("GET", "/_lab/requests", params={"limit": 6}).json()
    finally:
        stack.lab("DELETE", "/_lab/condition")
    stack.timings["search_with_800ms_rule_s"] = min(search)
    stack.timings["categories_same_condition_s"] = max(other)
    assert min(search) >= 0.8
    assert max(other) < 0.4
    for rec in recent:
        if rec["path"] == "/api/cases/search":
            assert rec["injected_ms"] >= 800 and rec["app_ms"] < 400
        else:
            assert rec["injected_ms"] == 0


def test_06_db_link_latency_slows_queries(stack: Lab):
    auth = stack.login()
    path = "/api/cases/C-5001"  # case, customer, category and sub-category: several queries
    stack.lab("DELETE", "/_lab/condition")
    base = _median(stack, path, auth)
    cond = {"id": "db-slow", "db": {"latency_ms": 150, "jitter_ms": 0}}
    assert stack.lab("PUT", "/_lab/condition", json=cond).status_code == 200
    try:
        slow = _median(stack, path, auth)
        health_slow = _median(stack, "/api/health", {})
    finally:
        stack.lab("DELETE", "/_lab/condition")
    health_base = _median(stack, "/api/health", {})
    stack.timings["case_detail_base_s"] = base
    stack.timings["case_detail_db150_s"] = slow
    stack.timings["health_base_s"] = health_base
    stack.timings["health_db150_s"] = health_slow
    # /api/health makes three round trips to the database (pool pre-ping, the count, the
    # rollback when the session closes); the case page makes four.
    assert health_slow - health_base >= 0.45
    assert slow - base >= 0.45
    # Off again: back to normal speed.
    assert _median(stack, path, auth) < base + 0.15


def test_07_seeded_faults_repeat(stack: Lab):
    auth = stack.login()
    cond = {
        "id": "flaky",
        "seed": 42,
        "faults": [{"match": "GET /api/cases/C-*", "status": 503, "rate": 0.5}],
    }

    def run_once() -> list[int]:
        assert stack.lab("PUT", "/_lab/condition", json=cond).status_code == 200
        out = []
        for i in list(range(1, 13)) * 2:
            r = stack.http.get(f"/api/cases/C-{5000 + i}", headers=auth)
            if r.status_code == 503:
                assert r.headers["x-twinlab-fault"] == "1"
                assert r.json()["detail"]["code"] == "LAB_INJECTED_FAULT"
            out.append(r.status_code)
        return out

    try:
        first, second = run_once(), run_once()
    finally:
        stack.lab("DELETE", "/_lab/condition")
    stack.timings["faults_first_run"] = first.count(503)
    assert first == second
    assert 503 in first and 200 in first
    assert set(first) == {200, 503}


def test_08_drivers_and_tls_through_db_link(stack: Lab):
    psycopg = pytest.importorskip("psycopg")
    asyncpg = pytest.importorskip("asyncpg")

    with psycopg.connect(DB_PROXY + "?sslmode=require", connect_timeout=10) as conn:
        ssl = conn.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone()[0]
        assert ssl is True
        assert conn.execute("SELECT count(*) FROM cases").fetchone()[0] == 12

    async def many() -> list[int]:
        async def one(i: int) -> int:
            conn = await asyncpg.connect(DB_PROXY, ssl="require", timeout=20)
            try:
                return await conn.fetchval("SELECT $1::int + count(*) FROM cases", i)
            finally:
                await conn.close()

        return await asyncio.gather(*(one(i) for i in range(60)))

    t = time.perf_counter()
    assert asyncio.run(many()) == [12 + i for i in range(60)]
    stack.timings["asyncpg_60_tls_connections_s"] = time.perf_counter() - t


def test_09_restart_keeps_baseline_and_condition(stack: Lab):
    cond = {
        "id": "kept",
        "seed": 9,
        "http": [{"match": "GET /api/categories", "latency_ms": {"p50": 50, "p95": 100}}],
    }
    assert stack.lab("PUT", "/_lab/condition", json=cond).status_code == 200
    assert stack.lab("POST", "/_lab/reset").status_code == 200
    baseline = stack.lab("GET", "/_lab/info").json()["baseline"]
    data = stack.lab("GET", "/_lab/data").json()

    _compose(stack.env, "restart", "agent")
    _wait_healthy(stack.http)

    got = stack.lab("GET", "/_lab/condition").json()
    assert got["id"] == "kept" and got["seed"] == 9 and got["http"][0]["latency_ms"]["p95"] == 100
    assert stack.lab("GET", "/_lab/info").json()["baseline"] == baseline
    auth = stack.login()  # the app reconnects through the restarted DB link
    stack.http.post("/api/cases/C-5001/notes", json={"text": "after restart"}, headers=auth)
    r = stack.lab("POST", "/_lab/reset")
    assert r.status_code == 200, r.text
    assert stack.lab("GET", "/_lab/data").json() == data
    stack.lab("DELETE", "/_lab/condition")


def test_10_intervals_survive_postgres_and_reset_exactly(stack: Lab):
    psycopg = pytest.importorskip("psycopg")
    from lab_agent.values import to_json

    values = [
        dt.timedelta(days=30, seconds=1),
        dt.timedelta(days=30, seconds=1, microseconds=1),
        dt.timedelta(days=12345, seconds=86399, microseconds=999999),
        dt.timedelta(microseconds=-1),
        dt.timedelta(days=-30, seconds=-1, microseconds=-250000),
        dt.timedelta(0),
    ]
    with psycopg.connect(DB_PROXY, connect_timeout=10, autocommit=True) as conn:
        # Postgres parses the text to_json writes back to the identical interval.
        for v in values:
            assert conn.execute("SELECT %s::interval", (to_json(v),)).fetchone()[0] == v
        conn.execute("CREATE TABLE interval_probe (id int PRIMARY KEY, d interval NOT NULL)")
        try:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO interval_probe VALUES (%s, %s)", list(enumerate(values))
                )
            r = stack.lab("POST", "/_lab/data/baseline", json={"label": "intervals"})
            assert r.status_code == 200, r.text
            conn.execute("UPDATE interval_probe SET d = '0'")
            r = stack.lab("POST", "/_lab/reset")
            assert r.status_code == 200, r.text
            got = conn.execute("SELECT d FROM interval_probe ORDER BY id").fetchall()
            assert [g[0] for g in got] == values
        finally:
            conn.execute("DROP TABLE interval_probe")
