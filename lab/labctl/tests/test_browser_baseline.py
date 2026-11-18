"""Browser-data baselines on the replay side (replication gap R06).

Recorded on the to-do fixture from an empty and from a populated start; replayed on a replica
from a declared, synthetic baseline. Asserted on what the replica's server saw, and on acceptance
cases: refresh persistence, fresh-context isolation, storage content. No source cookie reaches
the replica. Everything is served from 127.0.0.1.
"""

from __future__ import annotations

import json

import pytest
import yaml

pytest.importorskip("playwright.sync_api")

import replica_kit as kit  # noqa: E402

from labctl import recorder  # noqa: E402
from labctl.behaviour import baseline as base_mod  # noqa: E402
from labctl.behaviour import fidelity  # noqa: E402

KEY = "tl-todos"
BASE_TITLES = ["Syn base one", "Syn base two"]


def url(server) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    server = kit.serve_dir(kit.make_app(tmp_path_factory.mktemp("src"), "source.html"))
    yield server
    server.shutdown()


def _record(source, folder, state=None):
    out = folder / "rec"
    recorder.record(f"{url(source)}/index.html", out, script=kit.add_one_flow, headless=True,
                    storage_state=state)  # fmt: skip
    kit.write_bindings(out, kit.SYNTHETIC[:1])
    return out


@pytest.fixture(scope="module")
def from_empty(source, tmp_path_factory):
    return _record(source, tmp_path_factory.mktemp("empty"))


@pytest.fixture(scope="module")
def from_populated(source, tmp_path_factory):
    todos = [{"id": 1, "title": "Alpha task", "completed": False},
             {"id": 2, "title": "Beta task", "completed": True}]  # fmt: skip
    state = {"cookies": [], "origins": [{"origin": url(source), "localStorage": [
        {"name": KEY, "value": json.dumps(todos)}]}]}  # fmt: skip
    return _record(source, tmp_path_factory.mktemp("populated"), state)


def populated_baseline(source_origin: str) -> dict:
    return {
        "version": 1,
        "source_origin": source_origin,
        "localStorage": {KEY: [{"id": 1, "title": BASE_TITLES[0], "completed": False},
                               {"id": 2, "title": BASE_TITLES[1], "completed": True}]},
        "sessionStorage": {"tl-tab": "syn-tab-1"},
        "cookies": [{"name": "theme", "value": "syn-dark"}],
        "indexedDB": [{"database": "tl-app", "version": 1, "store": "prefs", "keyPath": "id",
                       "records": [{"id": "layout", "value": "syn-compact"}]}],
    }  # fmt: skip


def cases(titles: list) -> dict:
    t = [*titles, {"binding": 3}]
    return {"cases": [
        {"id": "titles", "check": "texts", "locator": ".todo-list li label", "equals": t},
        {"id": "stored", "check": "storage", "area": "localStorage", "key": KEY,
         "path": "$.length", "equals": len(t)},
        {"id": "kept-after-reload", "check": "texts", "reload": True, "category": "data",
         "locator": ".todo-list li label", "equals": t},
        {"id": "isolated", "check": "texts", "fresh_context": True, "category": "data",
         "locator": ".todo-list li label", "equals": list(titles)},
    ]}  # fmt: skip


EXTRA = [
    {"id": "session", "check": "storage", "area": "sessionStorage", "key": "tl-tab",
     "equals": "syn-tab-1"},
    {"id": "cookie", "check": "storage", "area": "cookie", "key": "theme", "equals": "syn-dark"},
    {"id": "idb", "check": "storage", "area": "indexedDB", "database": "tl-app", "store": "prefs",
     "key": "layout", "path": "$.value", "equals": "syn-compact"},
    {"id": "no-source-cookie", "check": "storage", "area": "cookie", "key": "src_session",
     "present": False},
]  # fmt: skip


def replay_on(rec, tmp_path, baseline: dict | None, case_doc: dict, html=(), **kw):
    app = kit.make_app(tmp_path / "replica", "replica.html", html=html)
    server = kit.serve_dir(app)
    if baseline is not None:
        (tmp_path / "baseline.yaml").write_text(yaml.safe_dump(baseline), "utf-8")
    (tmp_path / "acceptance.yaml").write_text(yaml.safe_dump(case_doc), "utf-8")
    try:
        report = fidelity.replay(rec, url(server), artifacts=tmp_path / "art",
                                 baseline=(tmp_path / "baseline.yaml") if baseline is not None
                                 else False, acceptance=tmp_path / "acceptance.yaml", **kw)  # fmt: skip
        return report, list(server.seen)
    finally:
        server.shutdown()


def statuses(report) -> dict:
    return {c.id: c.status for c in report.cases}


def test_an_empty_baseline_replays_predictably(from_empty, tmp_path):
    seq = kit.typed_steps(from_empty)[0]
    assert seq == 3
    for n in range(2):
        report, _ = replay_on(from_empty, tmp_path / str(n), {"version": 1}, cases([]))
        assert report.verdict == "PASS WITHIN DECLARED SCOPE", report.markdown()
        assert report.baseline["empty"] is True and report.baseline["applied"] is False


def test_a_populated_baseline_replays_predictably_keeps_state_on_refresh_and_is_isolated(
    source, from_populated, tmp_path
):
    doc = cases(BASE_TITLES)
    doc["cases"] += EXTRA
    results = []
    for n in range(2):
        report, seen = replay_on(from_populated, tmp_path / str(n),
                                 populated_baseline(url(source)), doc)  # fmt: skip
        assert report.verdict == "PASS WITHIN DECLARED SCOPE", report.markdown()
        results.append(statuses(report))
        # the blank page that set the storage never reached the replica's server
        assert not any(base_mod.BLANK_PATH in p for p, _ in seen)
        assert all(c == "theme=syn-dark" for p, c in seen), seen
        b = report.baseline
        assert b["applied"] is True and b["localStorage"] == [KEY]
        assert b["cookies"] == ["theme"] and b["indexedDB"] == ["tl-app/prefs: 1"]
        assert "syn-dark" not in json.dumps(b) and "Syn base" not in json.dumps(b)  # names only
    assert results[0] == results[1] and set(results[0].values()) == {"PASS"}


def test_no_source_cookie_or_request_reaches_the_replica(source, from_populated, tmp_path):
    # browser data taken from the source, with its session cookie: the replay never sends it
    leaked = {"cookies": [{"name": "src_session", "value": "real-looking-secret",
                           "domain": "127.0.0.1", "path": "/", "expires": -1, "httpOnly": False,
                           "secure": False, "sameSite": "Lax"}], "origins": []}  # fmt: skip
    doc = cases(BASE_TITLES)
    doc["cases"] += EXTRA
    # a replica page that still points at the source (an image from it)
    pointer = f'<img src="{url(source)}/index.html?from-replica" alt="">'
    before = len(source.seen)
    report, seen = replay_on(from_populated, tmp_path, populated_baseline(url(source)), doc,
                             html=[("</body>", pointer + "</body>")],
                             storage_state=leaked)  # fmt: skip
    assert statuses(report)["no-source-cookie"] == "PASS", report.markdown()
    assert seen and not any(c and "src_session" in c for _, c in seen), seen
    assert report.baseline["cookies_not_sent"] == 1
    assert report.baseline["source_requests_blocked"] >= 1
    assert not any("from-replica" in p for p, _ in source.seen[before:])
    assert any("request(s) to the source" in p for p in report.problems)


def test_a_credential_or_foreign_cookie_in_a_baseline_blocks(source, from_empty, tmp_path):
    bad = {"cookies": [{"name": "JSESSIONID", "value": "x"},
                       {"name": "pref", "value": "y", "domain": "example.com"}]}  # fmt: skip
    report, seen = replay_on(from_empty, tmp_path, bad, cases([]))
    assert report.status == "blocked" and report.verdict == "BLOCKED"
    assert any("JSESSIONID" in p and "session or credential" in p for p in report.problems)
    assert any("names a domain" in p for p in report.problems)
    assert seen == []  # nothing ran against the replica
    made_up = {"cookies": [{"name": "sessionid", "value": "syn-1", "synthetic": True}]}
    report, seen = replay_on(from_empty, tmp_path / "ok", made_up, cases([]))
    assert report.verdict == "PASS WITHIN DECLARED SCOPE", report.markdown()
    assert all(c == "sessionid=syn-1" for _, c in seen)


def test_a_baseline_template_has_the_origin_and_todo_values(from_empty, tmp_path):
    from typer.testing import CliRunner

    from labctl.behaviour.cli import app

    out = tmp_path / "baseline.yaml"
    result = CliRunner().invoke(app, ["baseline", str(from_empty), "--out", str(out)])
    assert result.exit_code == 0, result.output
    doc = yaml.safe_load(out.read_text("utf-8"))
    assert doc["source_origin"].startswith("http://127.0.0.1:") and doc["cookies"] == []
    # the keys the recorder saw (its per-step `storage`: keys and shapes only), values TODO
    assert doc["localStorage"] == {KEY: "TODO"}
    assert "Water" not in out.read_text("utf-8")
    loaded = base_mod.load(out)
    assert loaded.problems == [f"localStorage {KEY!r}: the value is still TODO"]  # it blocks
