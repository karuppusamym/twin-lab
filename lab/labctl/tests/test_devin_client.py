import time

import httpx
import pytest

from labctl.devin import mock
from labctl.devin.client import DevinClient, DevinError, session_id

KEY = "cog_test_fake_key_123"
ORG = "org-test"
STEP = 0.2


@pytest.fixture(scope="module")
def server():
    app = mock.create_app(api_key=KEY, step_seconds=STEP, retry_after="0.3")
    with mock.serve_in_thread(app) as url:
        yield url


@pytest.fixture
def slept():
    return []


@pytest.fixture
def client(server, slept):
    c = DevinClient(KEY, ORG, server, timeout=5, sleep=slept.append, backoff=0.01)
    yield c
    c.close()


def wait_for(c: DevinClient, devin_id: str, pred, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        s = c.get_session(devin_id)
        if pred(s):
            return s
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out; last {s}")
        time.sleep(0.05)


def test_upload_create_poll_to_finished_with_pr(server, client, tmp_path):
    f = tmp_path / "twinlab-demo-abcdef12.zip"
    f.write_bytes(b"PK fake zip")
    up = client.upload(f)
    assert up["name"] == f.name and up["attachment_id"] and up["url"].startswith(server)
    assert httpx.get(up["url"]).content == b"PK fake zip"

    s = client.create_session(
        "build it",
        repos=["acme/twin-lab"],
        attachment_urls=[up["url"]],
        title="Twin Lab: demo",
        tags=["twinlab", "app:demo"],
        max_acu_limit=10,
    )
    assert s["status"] == "new" and s["tags"] == ["twinlab", "app:demo"]
    assert s["title"] == "Twin Lab: demo" and s["url"]
    sid = s["session_id"]
    assert not sid.startswith("devin-")  # the client adds the prefix itself

    seen = set()
    done = wait_for(
        client,
        sid,
        lambda x: seen.add((x["status"], x["status_detail"])) or x["status_detail"] == "finished",
    )
    assert ("running", "working") in seen
    assert done["status"] == "running"
    assert done["pull_requests"][0]["pr_url"].startswith("https://github.com/acme/twin-lab/pull/")
    assert done["pull_requests"][0]["pr_state"] == "open"
    assert client.get_session("devin-" + sid)["session_id"] == sid  # with the prefix too


def test_waiting_for_user_then_message_resumes(client):
    sid = client.create_session("please [mock:ask]", repos=["acme/x"])["session_id"]
    wait_for(client, sid, lambda x: x["status_detail"] == "waiting_for_user")
    client.send_message(sid, "Use the simplest reading.")
    done = wait_for(client, sid, lambda x: x["status_detail"] == "finished")
    assert done["pull_requests"]


def test_terminate_exits(client):
    sid = client.create_session("short")["session_id"]
    assert client.terminate(sid, archive=True)["status"] == "exit"
    assert client.get_session(sid)["status"] == "exit"


def test_429_is_retried_honouring_retry_after(server, slept):
    c = DevinClient(KEY, ORG, server, sleep=slept.append, headers={"X-Mock-Rate-Limit": "once-1"})
    try:
        assert c.list_sessions(first=1)["items"] is not None
    finally:
        c.close()
    assert slept == [0.3]


def test_5xx_is_retried_with_backoff(server, slept):
    c = DevinClient(
        KEY,
        ORG,
        server,
        sleep=slept.append,
        backoff=0.5,
        headers={"X-Mock-Fail": "503", "X-Mock-Fail-Token": "fail-1"},
    )
    try:
        c.list_sessions()
    finally:
        c.close()
    assert slept == [0.5]


def test_retries_stop_after_five_tries():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, json={"title": "Unavailable", "status": 503, "detail": "down"})

    slept = []
    c = DevinClient(KEY, ORG, "http://devin.invalid", sleep=slept.append, backoff=1)
    c._http = httpx.Client(base_url="http://devin.invalid", transport=httpx.MockTransport(handler))
    with pytest.raises(DevinError) as err:
        c.get_session("x")
    assert len(calls) == 5 and slept == [1, 2, 4, 8]
    assert err.value.status == 503 and err.value.detail == "down"


def test_problem_json_is_mapped(client):
    with pytest.raises(DevinError) as err:
        client.get_session("devin-doesnotexist")
    assert err.value.status == 404
    assert err.value.title == "Not Found"
    assert "doesnotexist" in err.value.detail


def test_invalid_request_is_mapped_without_retry(client, slept):
    with pytest.raises(DevinError) as err:
        client.create_session("   ")
    assert err.value.status == 422 and "prompt" in err.value.detail
    assert slept == []


def test_wrong_key_gives_401(server, slept):
    c = DevinClient("cog_wrong_fake", ORG, server, sleep=slept.append)
    try:
        with pytest.raises(DevinError) as err:
            c.list_sessions()
    finally:
        c.close()
    assert err.value.status == 401 and err.value.title == "Unauthorized"
    assert slept == []


def test_key_never_in_repr_or_error(server):
    c = DevinClient("cog_secret_fake_999", ORG, server)
    try:
        assert "cog_secret" not in repr(c)
        with pytest.raises(DevinError) as err:
            c.list_sessions()
        assert "cog_secret" not in str(err.value)
    finally:
        c.close()


def test_unreachable_api_is_a_devin_error():
    c = DevinClient(KEY, ORG, "http://127.0.0.1:18499", timeout=1, sleep=lambda s: None)
    try:
        with pytest.raises(DevinError) as err:
            c.create_session("x")
    finally:
        c.close()
    assert err.value.status == 0


def test_session_id_prefix():
    assert session_id("abc") == "devin-abc"
    assert session_id("devin-abc") == "devin-abc"


def _scripted(responses):
    """A client whose transport answers from `responses` (a Response, or an exception to raise)."""
    calls, slept = [], []

    def handler(request):
        calls.append((request.method, request.url.path))
        item = responses[min(len(calls), len(responses)) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    c = DevinClient(KEY, ORG, "http://devin.invalid", sleep=slept.append, backoff=1)
    c._http = httpx.Client(base_url="http://devin.invalid", transport=httpx.MockTransport(handler))
    return c, calls, slept


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(500, json={"title": "Internal Server Error", "detail": "boom"}),
        httpx.Response(502, text="Bad gateway"),
        httpx.Response(504, text="Gateway Timeout"),
        httpx.ReadTimeout("timed out"),
        httpx.ConnectError("connection reset"),
    ],
    ids=["500", "502", "504", "read-timeout", "connect-error"],
)
def test_create_session_is_never_repeated_when_it_may_have_succeeded(answer):
    ok = httpx.Response(201, json={"session_id": "s1", "status": "new"})
    c, calls, slept = _scripted([answer, ok])
    with pytest.raises(DevinError) as err:
        c.create_session("build it")
    assert calls == [("POST", f"/v3/organizations/{ORG}/sessions")]  # one try, no second session
    assert slept == []
    assert "session may have been created" in str(err.value)
    assert "Devin web app" in str(err.value)


def test_create_session_retries_429_only():
    c, calls, slept = _scripted(
        [
            httpx.Response(429, headers={"Retry-After": "2"}, json={"title": "Too Many Requests"}),
            httpx.Response(201, json={"session_id": "s1", "status": "new"}),
        ]
    )
    assert c.create_session("build it")["session_id"] == "s1"
    assert len(calls) == 2 and slept == [2.0]


def test_get_still_retries_5xx_and_connection_errors():
    c, calls, slept = _scripted(
        [
            httpx.Response(503, text="down"),
            httpx.ConnectError("refused"),
            httpx.Response(200, json={"session_id": "s1", "status": "running"}),
        ]
    )
    assert c.get_session("s1")["status"] == "running"
    assert len(calls) == 3 and slept == [1, 2]
