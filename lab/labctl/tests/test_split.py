"""Splitting one recording of several applications into one recording per application."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from labctl import har
from labctl import model_build as mb
from labctl import split as sp
from labctl.evidence_cli import app as cli_app

T0 = datetime(2026, 11, 16, 3, 0, 0, tzinfo=timezone.utc)

A = "https://a.example.com"
B = "https://b.example.org:8443"
SSO = "https://login.sso.example.net"
PAY = "https://pay.example.com"


def _t(sec: float) -> str:
    return (T0 + timedelta(seconds=sec)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _page(title: str, body: str = "", password: bool = False) -> str:
    pw = '<input type="password" id="pw">' if password else ""
    return f'<html><head><title>{title}</title></head><body><h1 id="h">{title}</h1>{body}{pw}</body></html>'


def _entry(url, at, referer=None, document=False, status=200, mime=None, body="{}"):
    headers = [{"name": "Sec-Fetch-Dest", "value": "document" if document else "empty"}]
    if referer:
        headers.append({"name": "Referer", "value": referer})
    kind = mime or ("text/html" if document else "application/json")
    return {"pageref": "page_1", "startedDateTime": _t(at), "time": 5,
            "request": {"method": "GET", "url": url, "httpVersion": "HTTP/1.1", "headers": headers,
                        "queryString": [], "cookies": [], "headersSize": -1, "bodySize": 0},
            "response": {"status": status, "statusText": "", "httpVersion": "HTTP/1.1", "headers": [],
                         "cookies": [], "redirectURL": "", "headersSize": -1, "bodySize": -1,
                         "content": {"size": len(body), "mimeType": kind, "text": body}},
            "cache": {}, "timings": {"send": 0, "wait": 5, "receive": 0}}  # fmt: skip


IFRAME = '<iframe id="pay" src="https://pay.example.com/f"></iframe>'

# step n has ts n*10; entries sit just after the step that caused them
DEFAULT_STEPS = [
    (f"{A}/home", "goto", None, {}, _page("A home")),  # 1
    (f"{A}/cases/R-1001", "click", "a#open", {}, _page("A case", IFRAME)),  # 2
    (f"{A}/cases/R-1001", "click", "iframe#pay >>> button#go", {"frame": "iframe#pay"},
     _page("A case", IFRAME)),  # 3
    (f"{B}/dash", "goto", None, {}, _page("B dash")),  # 4
    (f"{SSO}/login", "goto", None, {}, _page("Sign in", password=True)),  # 5
    (f"{SSO}/login", "submit", "form#f", {}, _page("Sign in", password=True)),  # 6
    (f"{B}/dash/items", "click", "a#items", {}, _page("B items")),  # 7
    (f"{A}/home", "goto", None, {}, _page("A home again")),  # 8
]  # fmt: skip
DEFAULT_ENTRIES = [
    _entry(f"{A}/home", 9.5, document=True, mime="text/html", body="<html></html>"),
    _entry("https://cdn.example.net/lib.js", 9.8, referer=f"{A}/home", mime="text/javascript", body="x"),
    _entry(f"{A}/api/cases/R-1001", 20.2, referer=f"{A}/cases/R-1001", body='{"id": 1}'),
    _entry(f"{B}/dash", 39.5, document=True, mime="text/html", body="<html></html>"),
    _entry("https://api.b-backend.example.net/v1/stats", 40.3, referer=f"{B}/", body='{"n": 1}'),
    _entry(f"{SSO}/login", 49.5, document=True, status=302, mime="text/html", body=""),
    _entry(f"{B}/dash/items", 70.2, referer=f"{B}/dash", body='{"items": []}'),
    _entry(f"{A}/home", 79.5, document=True, mime="text/html", body="<html></html>"),
    _entry("https://cdn.example.net/app.css", 79.9, referer=f"{A}/home", mime="text/css", body="x"),
]  # fmt: skip


def make_recording(tmp_path: Path, name: str = "rec-1", steps=None, entries=None) -> Path:
    rec = tmp_path / "in" / name
    (rec / "dom").mkdir(parents=True)
    (rec / "screens").mkdir()
    steps = steps if steps is not None else DEFAULT_STEPS
    lines = []
    for seq, (url, action, selector, extra, html) in enumerate(steps, 1):
        step = {"seq": seq, "ts": _t(seq * 10), "url": url, "action": action, "selector": selector,
                "dom": f"dom/{seq:04d}.html", "dom_before": f"dom/{seq:04d}.before.html",
                "screenshot": f"screens/{seq:04d}.png"}  # fmt: skip
        step.update(extra)
        (rec / step["dom"]).write_text(html, encoding="utf-8")
        (rec / step["dom_before"]).write_text(html, encoding="utf-8")
        (rec / step["screenshot"]).write_bytes(b"\x89PNG-" + str(seq).encode())
        lines.append(json.dumps(step))
    (rec / "steps.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    ents = entries if entries is not None else DEFAULT_ENTRIES
    log = {"version": "1.2", "creator": {"name": "t", "version": "1"},
           "pages": [{"id": "page_1", "title": "x", "startedDateTime": _t(0), "pageTimings": {}}],
           "entries": ents}  # fmt: skip
    (rec / "recording.har").write_text(json.dumps({"log": log}), encoding="utf-8")
    return rec


@pytest.fixture
def rec(tmp_path):
    return make_recording(tmp_path)


def by_origin(p: sp.Proposal) -> dict:
    return {e.origin: e for e in p.apps}


def read_steps(folder: Path) -> list[dict]:
    return [json.loads(x) for x in (folder / "steps.jsonl").read_text("utf-8").splitlines()]


def har_urls(folder: Path) -> list[str]:
    doc = json.loads((folder / "recording.har").read_text("utf-8"))
    return [e["request"]["url"] for e in doc["log"]["entries"]]


# --- propose -----------------------------------------------------------------------------------------


def test_groups_by_origin_with_ids_and_kinds(rec):
    p = sp.propose(rec)
    assert p.recording == "rec-1"
    got = by_origin(p)
    assert list(got) == [A, B, SSO]  # in order of first visit
    assert got[A].id == "a-example-com" and got[A].kind == "app" and got[A].include
    assert got[B].id == "b-example-org-8443"
    assert (got[A].steps, got[B].steps, got[SSO].steps) == (4, 2, 2)
    assert got[A].screens == 2  # /home and /cases/{id}
    assert got[SSO].kind == "sign-on" and got[SSO].include is False
    assert got[B].include is True


def test_cross_origin_iframe_is_embedded_not_a_group(rec, tmp_path):
    p = sp.propose(rec)
    got = by_origin(p)
    assert PAY not in got
    assert got[A].embedded == [PAY]
    saved = yaml.safe_load(sp.save(p, tmp_path / "p.yaml").read_text("utf-8"))
    assert saved["apps"][0]["embedded"] == ["embedded: https://pay.example.com"]


def test_sign_on_heuristics(tmp_path):
    steps = [
        ("https://idp.example.com/x/start", "goto", None, {}, _page("Welcome", password=True)),
        ("https://app.example.com/home", "goto", None, {}, _page("Home")),
        ("https://app2.example.com/login", "goto", None, {}, _page("Login")),
        ("https://app2.example.com/cases", "goto", None, {}, _page("Cases")),
        ("https://app3.example.com/oauth/authorize", "goto", None, {}, _page("Allow")),
        ("https://sso.corp.example.com/", "goto", None, {}, _page("SSO")),
    ]
    entries = [_entry("https://idp.example.com/x/start", 9, document=True, status=302,
                      mime="text/html", body="")]  # fmt: skip
    got = by_origin(sp.propose(make_recording(tmp_path, steps=steps, entries=entries)))
    kinds = {origin: e.kind for origin, e in got.items()}
    assert kinds["https://idp.example.com"] == "sign-on"  # redirect, then only a credential form
    assert kinds["https://app.example.com"] == "app"
    assert (
        kinds["https://app2.example.com"] == "app"
    )  # its own /login page does not make it sign-on
    assert kinds["https://app3.example.com"] == "sign-on"  # every visit on auth paths
    assert kinds["https://sso.corp.example.com"] == "sign-on"  # by host


def test_default_ports_and_unique_ids(tmp_path):
    steps = [
        ("http://localhost:8001/a", "goto", None, {}, _page("a")),
        ("http://localhost:8002/a", "goto", None, {}, _page("b")),
        ("https://WWW.Example.com:443/x", "goto", None, {}, _page("c")),
    ]
    p = sp.propose(make_recording(tmp_path, steps=steps, entries=[]))
    assert [e.id for e in p.apps] == ["localhost-8001", "localhost-8002", "example-com"]
    assert p.apps[2].origin == "https://www.example.com"


def test_propose_errors(tmp_path):
    with pytest.raises(sp.ProposalError, match="recording folder"):
        sp.propose(tmp_path / "nope")
    empty = tmp_path / "e"
    empty.mkdir()
    with pytest.raises(sp.ProposalError, match="steps.jsonl"):
        sp.propose(empty)


# --- apply -------------------------------------------------------------------------------------------


def test_apply_writes_one_recording_per_app_and_leaves_the_original(rec, tmp_path):
    root = tmp_path / "repo"
    before = {p.relative_to(rec): p.read_bytes() for p in rec.rglob("*") if p.is_file()}
    out = sp.apply(rec, sp.propose(rec), root)
    assert [p.relative_to(root).as_posix() for p in out] == [
        "evidence/a-example-com/rec-1",
        "evidence/b-example-org-8443/rec-1",
    ]  # the sign-on hop is excluded
    assert before == {p.relative_to(rec): p.read_bytes() for p in rec.rglob("*") if p.is_file()}

    a, b = out
    a_steps = read_steps(a)
    assert [s["global_seq"] for s in a_steps] == [1, 2, 3, 8]
    assert [s["seq"] for s in a_steps] == [1, 2, 3, 4]
    assert {s["url"].split("/")[2] for s in a_steps} == {"a.example.com"}  # iframe step stays in A
    assert a_steps[2]["frame"] == "iframe#pay"
    assert [s["dom"] for s in a_steps] == [f"dom/{n:04d}.html" for n in (1, 2, 3, 4)]
    assert "A home again" in (a / "dom" / "0004.html").read_text("utf-8")
    assert (a / "screens" / "0004.png").read_bytes().endswith(b"-8")
    b_steps = read_steps(b)
    assert [s["global_seq"] for s in b_steps] == [4, 7]
    assert [s["selector"] for s in b_steps] == [None, "a#items"]

    assert har_urls(a) == [f"{A}/home", "https://cdn.example.net/lib.js", f"{A}/api/cases/R-1001",
                           f"{A}/home", "https://cdn.example.net/app.css"]  # fmt: skip
    assert har_urls(b) == [f"{B}/dash", "https://api.b-backend.example.net/v1/stats",
                           f"{B}/dash/items"]  # fmt: skip


def test_readers_work_on_each_part(rec, tmp_path):
    root = tmp_path / "repo"
    a, b = sp.apply(rec, sp.propose(rec), root)
    for folder, expected in ((a, 4), (b, 2)):
        recording = har.load(folder)
        assert recording.problems == []
        assert len(recording.steps) == expected
        assert all(s.dom is not None for s in recording.steps)
    model_a = mb.from_recording("a-example-com", a, root, None)
    model_b = mb.from_recording("b-example-org-8443", b, root, None)
    assert model_a.screens and model_b.screens
    assert any("/api/cases" in r.key for r in model_a.routes)
    assert any(
        "/dash/items" in r.key for r in model_b.routes
    )  # har.py itself skips other-host APIs
    assert not any("/api/cases" in r.key for r in model_b.routes)


def test_sequence_file_keeps_global_order(rec, tmp_path):
    root = tmp_path / "repo"
    sp.apply(rec, sp.propose(rec), root)
    doc = yaml.safe_load(sp.sequence_path(rec, root).read_text("utf-8"))
    assert doc["recording"] == "rec-1" and doc["id"] == "rec-1"
    assert [(s["n"], s["app"]) for s in doc["steps"]] == [
        (1, "a-example-com"), (2, "a-example-com"), (3, "a-example-com"),
        (4, "b-example-org-8443"), (5, "login-sso-example-net"), (6, "login-sso-example-net"),
        (7, "b-example-org-8443"), (8, "a-example-com"),
    ]  # fmt: skip
    s2 = doc["steps"][1]
    assert (
        s2["path"] == "/cases/{id}" and s2["screen"] == "cases-item" and s2["do"] == "click a#open"
    )
    assert doc["steps"][4]["excluded"] is True and "excluded" not in doc["steps"][0]
    assert doc["steps"][2]["frame"] == "iframe#pay"
    assert set(doc["apps"]) == {"a-example-com", "b-example-org-8443", "login-sso-example-net"}


def test_refuses_to_overwrite_unless_asked(rec, tmp_path):
    root = tmp_path / "repo"
    p = sp.propose(rec)
    sp.apply(rec, p, root)
    with pytest.raises(sp.ProposalError, match="already exists"):
        sp.apply(rec, p, root)
    assert len(sp.apply(rec, p, root, overwrite=True)) == 2


def test_include_sign_on_when_person_asks(rec, tmp_path):
    p = sp.propose(rec)
    for e in p.apps:
        e.include = True
    out = sp.apply(rec, p, tmp_path / "repo")
    assert len(out) == 3
    assert (tmp_path / "repo/evidence/login-sso-example-net/rec-1/steps.jsonl").is_file()


# --- editing the proposal ----------------------------------------------------------------------------


def _edited(rec, tmp_path, edit):
    path = sp.save(sp.propose(rec), tmp_path / "p.yaml")
    data = yaml.safe_load(path.read_text("utf-8"))
    edit(data)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return sp.load(path)


def test_save_load_roundtrip_yaml_and_json(rec, tmp_path):
    p = sp.propose(rec)
    for name in ("p.yaml", "p.json"):
        assert sp.load(sp.save(p, tmp_path / name)) == p


def test_rename_an_app(rec, tmp_path):
    def edit(d):
        d["apps"][0]["id"] = "case-portal"

    root = tmp_path / "repo"
    out = sp.apply(rec, _edited(rec, tmp_path, edit), root)
    assert out[0].parent.name == "case-portal"
    seq = yaml.safe_load(sp.sequence_path(rec, root).read_text("utf-8"))
    assert seq["steps"][0]["app"] == "case-portal"


def test_merge_two_origins_into_one_app(rec, tmp_path):
    def edit(d):
        d["apps"][1]["id"] = d["apps"][0]["id"]  # B joins A

    out = sp.apply(rec, _edited(rec, tmp_path, edit), tmp_path / "repo")
    assert [o.parent.name for o in out] == ["a-example-com"]
    steps = read_steps(out[0])
    assert [s["global_seq"] for s in steps] == [1, 2, 3, 4, 7, 8]  # step order preserved
    assert [s["seq"] for s in steps] == [1, 2, 3, 4, 5, 6]
    urls = har_urls(out[0])
    assert f"{B}/dash" in urls and f"{A}/home" in urls
    assert har.load(out[0]).problems == []


def test_drop_a_group(rec, tmp_path):
    def edit(d):
        d["apps"][1]["include"] = False

    out = sp.apply(rec, _edited(rec, tmp_path, edit), tmp_path / "repo")
    assert [o.parent.name for o in out] == ["a-example-com"]


def _portal(tmp_path):
    host = "https://portal.example.com"
    steps = [
        (f"{host}/hr/people", "goto", None, {}, _page("People")),  # 1
        (f"{host}/fin/ledger", "goto", None, {}, _page("Ledger")),  # 2
        (f"{host}/hr/people/E-1", "click", "a#p", {}, _page("Person")),  # 3
        (f"{host}/", "goto", None, {}, _page("Portal home")),  # 4
    ]
    entries = [
        _entry(f"{host}/hr/people", 9.5, document=True, mime="text/html", body="<html></html>"),
        _entry(f"{host}/fin/ledger", 19.5, document=True, mime="text/html", body="<html></html>"),
        _entry(f"{host}/api/ledger", 20.2, referer=f"{host}/fin/ledger", body="{}"),
        _entry(f"{host}/api/people", 30.2, referer=f"{host}/hr/people/E-1", body="{}"),
        _entry(f"{host}/static/portal.css", 40.2, referer=f"{host}/", mime="text/css", body="x"),
    ]  # fmt: skip
    return make_recording(tmp_path, steps=steps, entries=entries), host


def test_split_one_host_by_path_prefix(tmp_path):
    rec, host = _portal(tmp_path)
    path = sp.save(sp.propose(rec), tmp_path / "p.yaml")
    d = yaml.safe_load(path.read_text("utf-8"))
    base = d["apps"][0]
    d["apps"] = [
        {**base, "id": "portal"},  # the rest of the host
        {**base, "id": "hr", "path_prefix": "/hr"},
        {**base, "id": "finance", "path_prefix": "/fin/"},
    ]
    path.write_text(yaml.safe_dump(d), encoding="utf-8")
    p = sp.load(path)
    assert [e.path_prefix for e in p.apps] == [None, "/hr", "/fin"]
    out = {o.parent.name: o for o in sp.apply(rec, p, tmp_path / "repo")}
    assert set(out) == {"portal", "hr", "finance"}

    def seqs(folder):
        return [s["global_seq"] for s in read_steps(folder)]

    assert seqs(out["hr"]) == [1, 3] and seqs(out["finance"]) == [2] and seqs(out["portal"]) == [4]

    def paths(folder):
        return [u.removeprefix(host) for u in har_urls(folder)]

    assert paths(out["finance"]) == ["/fin/ledger", "/api/ledger"]
    assert paths(out["hr"]) == ["/hr/people", "/api/people"]  # by the page that asked (Referer)
    assert paths(out["portal"]) == ["/static/portal.css"]
    for folder in out.values():
        assert har.load(folder).problems == []


def test_separate_an_embedded_iframe_into_its_own_app(rec, tmp_path):
    def edit(d):
        d["apps"].append({"id": "payments", "origin": PAY, "kind": "app"})

    out = {o.parent.name: o for o in sp.apply(rec, _edited(rec, tmp_path, edit), tmp_path / "repo")}
    assert [s["global_seq"] for s in read_steps(out["payments"])] == [3]
    assert 3 not in [s["global_seq"] for s in read_steps(out["a-example-com"])]


def test_canvas_pictures_and_documents_follow_their_step(tmp_path):
    rec = make_recording(tmp_path)
    (rec / "screens" / "0004.c1.png").write_bytes(b"canvas")
    (rec / "dom" / "0004.html").write_text(
        _page("B", '<tl-canvas-text data-tl-image="screens/0004.c1.png"></tl-canvas-text>'), "utf-8"
    )
    (rec / "docs").mkdir()
    (rec / "docs" / "01.pdf").write_bytes(b"%PDF-a")
    (rec / "docs" / "02.pdf").write_bytes(b"%PDF-b")
    docs = [
        {"url": f"{A}/files/a.pdf", "file": "docs/01.pdf", "pages": 1, "fields": []},
        {"url": f"{B}/files/b.pdf", "file": "docs/02.pdf", "pages": 2,
         "fields": [{"name": "f", "type": "Tx"}]},
    ]  # fmt: skip
    (rec / "docs" / "documents.json").write_text(json.dumps(docs), "utf-8")
    a, b = sp.apply(rec, sp.propose(rec), tmp_path / "repo")
    assert (b / "screens" / "0001.c1.png").read_bytes() == b"canvas"
    assert "screens/0001.c1.png" in (b / "dom" / "0001.html").read_text("utf-8")
    assert json.loads((a / "docs" / "documents.json").read_text("utf-8"))[0]["pages"] == 1
    docs_b = json.loads((b / "docs" / "documents.json").read_text("utf-8"))
    assert len(docs_b) == 1 and docs_b[0]["file"] == "docs/01.pdf"
    assert (b / "docs" / "01.pdf").read_bytes() == b"%PDF-b"
    model = mb.from_recording("b", b, tmp_path / "repo", None)
    assert any(s.name.startswith("document ") for s in model.screens)


# --- bad proposals -----------------------------------------------------------------------------------


def _drop_all(d):
    for a in d["apps"]:
        a["include"] = False


def _same_id_other_include(d):
    d["apps"][1].update(id=d["apps"][0]["id"], include=False)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda d: d["apps"][0].update(id="Bad Id"), "lower-case letters"),
        (lambda d: d["apps"][0].update(origin="a.example.com"), "origin"),
        (lambda d: d["apps"][0].update(origin="https://a.example.com/home"), "origin"),
        (lambda d: d["apps"][0].update(kind="portal"), "kind"),
        (lambda d: d["apps"][0].update(include="yes"), "include"),
        (lambda d: d["apps"][0].update(path_prefix="hr"), "path_prefix"),
        (lambda d: d["apps"][0].update(colour="red"), "unknown field"),
        (lambda d: d["apps"].append(dict(d["apps"][0], id="other")), "appears twice"),
        (_drop_all, "every entry is dropped"),
        (lambda d: d.pop("recording"), "recording"),
        (lambda d: d.update(apps=[]), "non-empty"),
        (_same_id_other_include, "disagree"),
    ],
)  # fmt: skip
def test_bad_proposals_have_clear_messages(rec, tmp_path, edit, message):
    path = sp.save(sp.propose(rec), tmp_path / "p.yaml")
    data = yaml.safe_load(path.read_text("utf-8"))
    edit(data)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(sp.ProposalError, match=message):
        sp.load(path)


def test_unreadable_proposal_file(tmp_path):
    bad = tmp_path / "p.yaml"
    bad.write_text("apps: [unclosed", "utf-8")
    with pytest.raises(sp.ProposalError, match="cannot read"):
        sp.load(bad)
    bad.write_text("- just\n- a list\n", "utf-8")
    with pytest.raises(sp.ProposalError, match="mapping"):
        sp.load(bad)


def test_apply_rejects_unknown_and_uncovered_origins(rec, tmp_path):
    p = sp.propose(rec)
    p.apps.append(sp.Entry("ghost", "https://ghost.example.com"))
    with pytest.raises(sp.ProposalError, match="does not appear"):
        sp.apply(rec, p, tmp_path / "repo")
    p = sp.propose(rec)
    p.apps = p.apps[:2]
    with pytest.raises(sp.ProposalError, match="not in the proposal"):
        sp.apply(rec, p, tmp_path / "repo")
    assert not (tmp_path / "repo").exists()


# --- command -----------------------------------------------------------------------------------------


def test_command_proposes_then_applies(rec, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from labctl import repo

    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(repo, "find_root", lambda: root)
    runner = CliRunner()
    prop = tmp_path / "split.yaml"
    r = runner.invoke(cli_app, ["split", str(rec), "--proposal", str(prop)])
    assert r.exit_code == 0, r.output
    assert "a-example-com" in r.output and "sign-on" in r.output
    assert "embedded: https://pay.example.com" in r.output
    assert prop.is_file() and not (root / "evidence").exists()
    r = runner.invoke(cli_app, ["split", str(rec), "--proposal", str(prop), "--apply"])
    assert r.exit_code == 0, r.output
    assert (root / "evidence/a-example-com/rec-1/recording.har").is_file()
    r = runner.invoke(cli_app, ["split", str(rec), "--proposal", str(prop), "--apply"])
    assert r.exit_code == 1 and "already exists" in r.output
    r = runner.invoke(cli_app, ["split", str(rec), "--proposal", str(prop), "--apply", "--force"])
    assert r.exit_code == 0
    r = runner.invoke(cli_app, ["split", str(tmp_path / "missing")])
    assert r.exit_code == 2


def test_a_value_that_travels_is_named_by_the_element_that_showed_it():
    """Trial finding 11: a value shown in one application and typed into another is said by
    element (`b#savedNumber`), not by shape alone."""
    html = (
        "<html><body><p id='savedMessage'>Order <b id='savedNumber'>A-9</b> was saved.</p>"
        "<dl><dd id='savedTotal'>9.9</dd><dd id='savedPhone'>9 9 9</dd></dl></body></html>"
    )
    assert sp._shown_in(html, "A-9") == "b#savedNumber"  # the smallest element, not its paragraph
    assert sp._shown_in(html, "9.9") == "dd#savedTotal"
    assert sp._shown_in(html, "A9A") is None
