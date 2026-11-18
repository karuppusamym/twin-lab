"""One recorded flow across several web applications, to one brief and build package each.

The acceptance part drives two fixture applications written for these tests
(fixtures/multiapp/apps.py: `orders` and `billing`, on two origins, with a widget on a third) through
a scripted session (fixtures/multiapp/flow.py) while Twin Lab records, then checks, without the
internet: every scripted action captured in order across the tabs; the split proposing exactly
the two applications and the embedded widget; `build-all` giving two evidence sets, draft flows,
briefs and packages with the cross-app section; no typed value in any file written; a second
recording enhancing the orders model; and each application's own recording replaying on its own
application. The rest are small tests of the parts.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, quote_plus

import pytest
import yaml

HERE = Path(__file__).parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE / "fixtures" / "multiapp"))

import apps as fixture_apps  # noqa: E402
import flow as fixture_flow  # noqa: E402

from labctl import multiapp, recorder  # noqa: E402
from labctl import split as sp  # noqa: E402
from labctl.botlog.recording import load as load_rec  # noqa: E402


def make_root(path: Path) -> Path:
    """A repository with what a package needs, and nothing else."""
    for d in ("apps", "automations", "lab", "flows", "docs"):
        (path / d).mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "AGENTS.md", path / "AGENTS.md")
    shutil.copy(REPO / "docs" / "factory-playbook.md", path / "docs" / "factory-playbook.md")
    return path


def steps_of(folder: Path) -> list[dict]:
    return [json.loads(x) for x in (folder / "steps.jsonl").read_text("utf-8").splitlines() if x]


def forms(value: str) -> set[str]:
    return {value.lower(), quote(value).lower(), quote_plus(value).lower()}


# =================================================================================================
# Small tests of the parts (no browser)
# =================================================================================================


def test_storage_keeps_keys_and_masked_shapes_never_values():
    raw = {"localStorage": {"cart": {"items": [{"sku": "A9", "qty": 7, "price": 1.25}], "note": "A A"},
                            "plain": "Bob Smith", "auth_token": "(credential-like key: value not kept)"},
           "sessionStorage": {}, "indexedDB": [{"db": "app", "stores": ["orders", 3]}],
           "cookies": ["sid", "sid", ""], "extra": "dropped"}  # fmt: skip
    out = recorder._storage(raw)
    assert out["localStorage"]["cart"] == {"items": [{"sku": "A9", "qty": 0, "price": 0.5}],
                                           "note": "A A"}  # fmt: skip
    assert out["localStorage"]["plain"] == "A A"  # a value the page failed to mask: masked here
    assert out["indexedDB"] == [{"db": "app", "stores": ["orders"]}]
    assert out["cookies"] == ["sid"] and "extra" not in out and "sessionStorage" not in out
    # har.py reads it as keys and shapes
    from labctl import har

    step = har.Step(1, None, "http://x/", "click", None, None, out)
    facts = har.storage_facts([step])
    assert facts["keys"] == ["localStorage:cart", "localStorage:plain", "localStorage:auth_token",
                             "indexedDB:app"]  # fmt: skip
    assert facts["shape"]["localStorage:cart"] == {
        "items": {"[]": {"sku": "string", "qty": "integer", "price": "number"}},
        "note": "string",
    }


def test_typed_values_leave_addresses_and_storage_keys():
    s = recorder._Secrets()
    s.add("Hartley", "http://a", "t1")
    s.add("new", "http://a", "t2")  # a word that is also part of an address
    step = {
        "url": "http://a/orders/new/customers/Hartley?q=Hartley&x=1#Hartley",
        "storage": {"localStorage": {"draft-Hartley": "A"}},
    }
    out = s.clean_step(step)
    assert out["url"] == "http://a/orders/A/customers/A?q=A&x=1#A"  # whole segments only
    assert list(out["storage"]["localStorage"]) == ["draft-A"]
    assert "Hartley" not in json.dumps(out)


def _fake_steps(tmp_path: Path) -> recorder._Steps:
    (tmp_path / "dom").mkdir(exist_ok=True)
    steps = recorder._Steps(tmp_path, screenshots=False)
    for key, tab in (("page@a", "t1"), ("page@b", "t2")):
        steps.tabs[key] = {"tab": tab, "opener": "t1" if tab == "t2" else None, "har_page": key,
                           "opened": "2026-11-17T10:00:00.000Z", "closed": None, "first_url": ""}  # fmt: skip
    return steps


def _fake_page(guid: str, opener=None):
    impl = SimpleNamespace(_guid=guid, _opener=getattr(opener, "_impl_obj", None))
    return SimpleNamespace(_impl_obj=impl, url="http://x/", on=lambda *a, **k: None)


def test_steps_keep_the_order_they_arrived_in_whatever_comes_late(tmp_path):
    """Each action is numbered when it reaches the recorder (one sequence for every tab); a
    snapshot that arrives after the next tab's actions, or never (the page left), changes
    neither the number nor the order."""
    (tmp_path / "dom").mkdir()
    (tmp_path / "screens").mkdir()
    steps = recorder._Steps(tmp_path, screenshots=False)
    a = _fake_page("page@a")
    b = _fake_page("page@b", opener=a)
    src_a, src_b = {"page": a}, {"page": b}

    def event(src, doc, n, action="click", **kw):
        steps.on_event(src, {"eid": f"{doc}:{n}", "doc": doc, "url": "http://x/", "action": action,
                             "selector": f"#{doc}{n}", "at": f"2026-11-17T10:00:0{n}.000Z", **kw})  # fmt: skip

    event(src_a, "a1", 1, "goto", nav="navigate")
    steps.on_flush(src_a, {"eids": ["a1:1"], "url": "http://x/", "html": "<p>a1</p>"})
    event(src_a, "a1", 2)  # the click that opens tab 2: its snapshot comes late
    event(src_b, "b1", 3, "goto", nav="navigate")  # the new tab's first page: tab-open first
    steps.on_flush(src_b, {"eids": ["b1:3"], "url": "http://x/",
                           "html": "<p>b1</p><img src='screens/0004.c1.png'>"})  # fmt: skip
    (tmp_path / "screens" / "0004.c1.png").write_bytes(b"b1")
    steps.on_flush(src_a, {"eids": ["a1:2"], "url": "http://x/", "html": "<p>late</p>"})
    event(src_b, "b1", 4)
    event(src_b, "b1", 5, "submit", caused=True)  # caused by the click before it
    event(src_a, "a1", 6)  # back in tab 1: never flushed (the recording ended)
    steps.drain(pictures=False)
    steps.close()
    got = steps_of(tmp_path)
    assert [(s["seq"], s["action"], s["tab"]) for s in got] == [
        (1, "goto", "t1"), (2, "click", "t1"), (3, "tab-open", "t2"), (4, "goto", "t2"),
        (5, "click", "t2"), (6, "submit", "t2"), (7, "tab-switch", "t1"), (8, "click", "t1")]  # fmt: skip
    assert [s.get("selector") for s in got] == ["#a11", "#a12", None, "#b13", "#b14", "#b15", None,
                                                "#a16"]  # fmt: skip
    assert got[5]["caused_by"] == 5 and got[6]["from_tab"] == "t2" and got[2]["opener"] == "t1"
    # the late snapshot is its own step's, files follow their step, a step never flushed has none
    assert (tmp_path / got[1]["dom"]).read_text("utf-8") == "<p>late</p>"
    assert "screens/0004.c1.png" in (tmp_path / "dom" / "0004.html").read_text("utf-8")
    assert got[7]["dom"] is None and got[5]["dom"] is None
    rows = json.loads((tmp_path / "tabs.json").read_text("utf-8"))
    assert [(r["tab"], r["opener"]) for r in rows] == [("t1", None), ("t2", "t1")]


def test_a_tab_switch_renumbers_files_and_references(tmp_path):
    steps = _fake_steps(tmp_path)
    (tmp_path / "screens").mkdir()

    def add(seq, tab, action="click", **kw):
        dom = f"dom/{seq:04d}.html"
        (tmp_path / dom).write_text(f"<p>{seq}</p><img src='screens/{seq:04d}.c1.png'>", "utf-8")
        (tmp_path / "screens" / f"{seq:04d}.c1.png").write_bytes(str(seq).encode())
        steps.written.append({"seq": seq, "ts": f"2026-11-17T10:00:{seq:06.3f}Z", "url": "http://x/",
                              "action": action, "selector": f"#s{seq}", "dom": dom,
                              "dom_before": None, "screenshot": None, "tab": tab, **kw})  # fmt: skip

    add(1, "t1")
    add(2, "t2")
    add(3, "t1")  # a switch back to t1 comes before it
    add(4, "t1", caused_by=3, locators=[{"kind": "record", "text_from": 3}])
    steps.close()
    got = steps_of(tmp_path)
    assert [(s["seq"], s["action"]) for s in got] == [(1, "click"), (2, "click"),
                                                      (3, "tab-switch"), (4, "click"), (5, "click")]  # fmt: skip
    assert got[4]["caused_by"] == 4 and got[4]["locators"][0]["text_from"] == 4
    assert (tmp_path / "dom" / "0004.html").read_text("utf-8").startswith("<p>3</p>")
    assert "screens/0004.c1.png" in (tmp_path / "dom" / "0004.html").read_text("utf-8")
    assert (tmp_path / "screens" / "0004.c1.png").read_bytes() == b"3"


def test_one_tab_recordings_keep_their_shape(tmp_path):
    steps = recorder._Steps(tmp_path, screenshots=False)
    steps.tabs["page@a"] = {"tab": "t1", "opener": None, "har_page": "page@a", "opened": "",
                            "closed": None, "first_url": ""}  # fmt: skip
    steps.written = [{"seq": 1, "ts": "2026-11-17T10:00:01.000Z", "action": "goto", "tab": "t1"},
                     {"seq": 2, "ts": "2026-11-17T10:00:02.000Z", "action": "tab-close", "tab": "t1"}]  # fmt: skip
    steps.close()
    assert [s["action"] for s in steps_of(tmp_path)] == ["goto"]
    assert not (tmp_path / "tabs.json").exists()


def _two_tab_recording(tmp_path: Path) -> Path:
    """A recording of two apps in two tabs, written by hand: app A opens B in a new tab."""
    rec = tmp_path / "in" / "two-tabs"
    (rec / "dom").mkdir(parents=True)
    a, b = "http://a.example.test", "http://b.example.test"
    rows = [
        (a + "/home", "goto", None, "t1", {}),  # 1
        (a + "/home", "type", "input#q", "t1", {"typed_shape": "A-9"}),  # 2
        (a + "/home", "click", "a#open", "t1", {}),  # 3
        ("", "tab-open", None, "t2", {"opener": "t1"}),  # 4
        (b + "/start", "goto", None, "t2", {}),  # 5
        (b + "/start", "type", "input[name=\"ref\"]", "t2", {"typed_shape": "A-9"}),  # 6
        (b + "/start", "click", "button.go", "t2", {}),  # 7
        (b + "/done", "goto", None, "t2", {"caused_by": 7, "nav": "navigate"}),  # 8
        (a + "/home", "tab-switch", None, "t1", {"from_tab": "t2"}),  # 9
        (a + "/home", "click", "a#next", "t1", {"locators": [{"kind": "record", "rows": "ul li",
                                                              "control": "a", "count": 1,
                                                              "text_from": 2}]}),  # 10
    ]  # fmt: skip
    lines = []
    for n, (url, action, sel, tab, extra) in enumerate(rows, 1):
        step = {"seq": n, "ts": f"2026-11-17T10:00:{n:02d}.000Z", "url": url, "action": action,
                "selector": sel, "dom": None, "dom_before": None, "screenshot": None, "tab": tab,
                **extra}  # fmt: skip
        if action not in sp.TAB_ACTIONS:
            step["dom"] = f"dom/{n:04d}.html"
            (rec / step["dom"]).write_text(f"<html><body><h1>{n}</h1></body></html>", "utf-8")
        lines.append(json.dumps(step))
    (rec / "steps.jsonl").write_text("\n".join(lines) + "\n", "utf-8")

    def entry(url, sec, page, dest="empty", referer=None):
        headers = [{"name": "Sec-Fetch-Dest", "value": dest}]
        if referer:
            headers.append({"name": "Referer", "value": referer})
        return {"pageref": page, "startedDateTime": f"2026-11-17T10:00:{sec:06.3f}Z", "time": 3,
                "request": {"method": "GET", "url": url, "headers": headers},
                "response": {"status": 200, "content": {"mimeType": "application/json", "text": "{}"}}}  # fmt: skip

    entries = [entry(a + "/home", 0.5, "page@1", "document"),
               entry(b + "/start", 4.5, "page@2", "document"),
               # no Referer (a strict policy): only the tab that made it says whose it is
               entry("http://cdn.example.test/lib.js", 4.8, "page@2"),
               entry(b + "/api/ref", 6.5, "page@2"),
               entry("http://cdn.example.test/lib.js", 9.5, "page@1")]  # fmt: skip
    (rec / "recording.har").write_text(
        json.dumps({"log": {"pages": [], "entries": entries}}), "utf-8"
    )
    (rec / "tabs.json").write_text(json.dumps([{"tab": "t1", "har_page": "page@1"},
                                               {"tab": "t2", "har_page": "page@2", "opener": "t1"}]),
                                   "utf-8")  # fmt: skip
    (rec / "bindings.yaml").write_text(yaml.safe_dump({"version": 1, "recording": "two-tabs", "steps": {
        2: {"action": "type", "field": "input#q", "value": "Syn one"},
        6: {"action": "type", "field": "input[name=\"ref\"]", "value": "Syn two"}}}), "utf-8")  # fmt: skip
    return rec


def test_split_by_tab_with_renumbered_steps_and_bindings(tmp_path):
    rec = _two_tab_recording(tmp_path)
    prop = sp.propose(rec)
    assert [(e.origin, e.tabs) for e in prop.apps] == [("http://a.example.test", ["t1"]),
                                                       ("http://b.example.test", ["t2"])]  # fmt: skip
    root = tmp_path / "repo"
    a, b = sp.apply(rec, prop, root)
    sa, sb = steps_of(a), steps_of(b)
    assert [s["global_seq"] for s in sa] == [1, 2, 3, 10]  # no tab steps in a part
    assert [s["global_seq"] for s in sb] == [5, 6, 7, 8]
    assert sb[3]["caused_by"] == 3  # step 7 of the whole recording is step 3 of billing's
    assert sa[3]["locators"][0]["text_from"] == 2
    ba = yaml.safe_load((a / "bindings.yaml").read_text("utf-8"))
    bb = yaml.safe_load((b / "bindings.yaml").read_text("utf-8"))
    assert ba["steps"] == {2: {"action": "type", "field": "input#q", "value": "Syn one"}}
    assert list(bb["steps"]) == [2] and bb["steps"][2]["value"] == "Syn two"

    # requests by the tab that made them, even without a Referer
    def urls(folder):
        doc = json.loads((folder / "recording.har").read_text("utf-8"))
        return [e["request"]["url"] for e in doc["log"]["entries"]]

    assert urls(a) == ["http://a.example.test/home", "http://cdn.example.test/lib.js"]
    assert urls(b) == ["http://b.example.test/start", "http://cdn.example.test/lib.js",
                       "http://b.example.test/api/ref"]  # fmt: skip
    seq = yaml.safe_load(sp.sequence_path(rec, root).read_text("utf-8"))
    assert [r["do"] for r in seq["steps"] if r.get("tab") and r["do"].startswith(("open", "switch"))
            ][:2] == ["open the page", "open tab t2 from tab t1"]  # fmt: skip
    assert seq["handovers"][0]["how"] == "in a new tab" and seq["handovers"][1]["how"] == (
        "back to a tab already open"
    )
    assert seq["travels"][0]["shape"] == "A-9" and seq["travels"][0]["how"] == "typed"
    assert [t["tab"] for t in seq["tabs"]] == ["t1", "t2"] and seq["tabs"][1]["opener"] == "t1"


def test_build_all_stops_for_review_and_the_command_says_so(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from labctl import repo
    from labctl.evidence_cli import app as cli_app

    rec = _two_tab_recording(tmp_path)
    root = make_root(tmp_path / "repo")
    monkeypatch.setattr(repo, "find_root", lambda: root)
    r = CliRunner().invoke(cli_app, ["build-all", str(rec)])
    assert r.exit_code == 0, r.output
    assert "a-example-test" in r.output and "--proposal" in r.output
    assert multiapp.proposal_file(rec).is_file() and not (root / "evidence").exists()
    r = CliRunner().invoke(cli_app, ["build-all", str(rec), "--proposal", str(tmp_path / "nope")])
    assert r.exit_code == 2


# =================================================================================================
# Acceptance: two applications, one recording
# =================================================================================================

pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def servers():
    run = fixture_apps.serve()
    yield run
    run.stop()


@pytest.fixture(scope="module")
def recorded(servers, tmp_path_factory):
    root = make_root(tmp_path_factory.mktemp("multiapp") / "repo")
    rec = multiapp.recordings_dir(root) / "order-to-invoice"
    seen = {}
    recorder.record(f"{servers.orders}/login", rec, headless=True,
                    script=lambda page: seen.update(fixture_flow.run(page)))  # fmt: skip
    return SimpleNamespace(root=root, rec=rec, number=seen["number"])


def test_every_scripted_action_is_captured_in_order_across_tabs(recorded, servers):
    steps = steps_of(recorded.rec)
    assert len(steps) >= 60
    from labctl.behaviour.fidelity import action_label

    rec = load_rec(recorded.rec)
    got = [(action_label(s), s.tab, s.selector) for s in rec.steps
           if s.action not in ("goto", "submit")]  # fmt: skip
    assert [(a, t) for a, t, _ in got] == [(a, t) for a, t, _ in fixture_flow.EXPECTED]
    for (_, _, sel), (_, _, hint) in zip(got, fixture_flow.EXPECTED, strict=True):
        assert hint is None or hint in (sel or ""), (sel, hint)
    assert [s["seq"] for s in steps] == list(range(1, len(steps) + 1))
    # the timestamps of all tabs are on one sequence
    assert [s["ts"] for s in steps] == sorted(s["ts"] for s in steps)
    # each step says where it happened
    billing = [s for s in steps if s["tab"] == "t2" and s["action"] != "tab-open"]
    assert {s["origin"] for s in billing} == {servers.billing} and all(
        s["opener"] == "t1" for s in billing
    )
    assert {
        s["origin"] for s in steps if s["tab"] == "t1" and s.get("url", "").startswith("http")
    } == {servers.orders}
    assert [s["nav"] for s in steps if s.get("nav") == "back_forward"] == ["back_forward"] * 2
    # one HAR for both tabs, each entry with the tab that made it
    tabs = json.loads((recorded.rec / "tabs.json").read_text("utf-8"))
    assert [(t["tab"], t["opener"]) for t in tabs] == [("t1", None), ("t2", "t1")]
    har = json.loads((recorded.rec / "recording.har").read_text("utf-8"))
    pages = {t["har_page"]: t["tab"] for t in tabs}
    by_tab = {}
    for e in har["log"]["entries"]:
        by_tab.setdefault(pages.get(e.get("pageref")), set()).add(
            sp.normalise_origin(e["request"]["url"])
        )
    assert servers.billing in by_tab["t2"] and servers.widget in by_tab["t2"]
    assert servers.orders in by_tab["t1"] and servers.billing not in by_tab["t1"]
    # browser storage: keys and masked shapes, cookie names; never a value
    stored = [s["storage"] for s in steps if s.get("storage")]
    local = next(x for x in stored if "localStorage" in x)
    assert local["localStorage"]["orders.lastSearch"] == {"by": "A", "page": 0}
    session = next(x for x in stored if "sessionStorage" in x)
    assert session["sessionStorage"]["billing.lastView"] == {"page": "/A/A", "at": 0}
    assert session["origin"] == servers.billing and "osid" in local["cookies"]
    assert any("osid" in names for names in local["cookies_by_domain"].values())
    # how it was recorded, and the rectangles painted over typed values in each picture
    meta = json.loads((recorded.rec / "recording.json").read_text("utf-8"))
    assert meta["viewport"] == {"width": 1280, "height": 720} and meta["device_scale_factor"] == 1
    assert meta["browser"] == "chromium" and meta["browser_version"]
    boxes = [b for s in steps for b in s.get("mask_boxes") or []]
    assert boxes and all(set(b) == {"x", "y", "width", "height"} and b["width"] > 0 for b in boxes)
    assert all(s.get("screenshot") for s in steps if s.get("mask_boxes"))


def test_the_split_proposes_the_two_apps_and_the_embedded_widget(recorded, servers):
    prop = sp.propose(recorded.rec)
    assert [(e.origin, e.kind, e.include, e.tabs) for e in prop.apps] == [
        (servers.orders, "app", True, ["t1"]), (servers.billing, "app", True, ["t2"])]  # fmt: skip
    assert prop.apps[0].embedded == [] and prop.apps[1].embedded == [servers.widget]
    assert not any(e.kind == "sign-on" for e in prop.apps)  # orders' own sign-in page is orders


@pytest.fixture(scope="module")
def built(recorded):
    root, rec = recorded.root, recorded.rec
    first = multiapp.build_all(root, rec, say=lambda m: None)
    assert first.stopped and not (root / "evidence" / sp.SEQUENCES).exists()
    # the person reviews the proposal: names the two applications
    path = multiapp.proposal_file(rec)
    data = yaml.safe_load(path.read_text("utf-8"))
    data["apps"][0]["id"], data["apps"][1]["id"] = "orders", "billing"
    path.write_text(yaml.safe_dump(data, sort_keys=False), "utf-8")
    lines: list[str] = []
    summary = multiapp.build_all(root, rec, proposal=path, say=lines.append,
                                 package_dir=root / "packages")  # fmt: skip
    return SimpleNamespace(summary=summary, output="\n".join(lines), proposal=path)


def _zip_texts(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n).decode("utf-8", "replace") for n in zf.namelist()
                if not n.endswith((".png", ".jpg"))}  # fmt: skip


def test_build_all_gives_each_app_its_evidence_flow_brief_and_package(recorded, built):
    root, s = recorded.root, built.summary
    assert [a.id for a in s.apps] == ["orders", "billing"] and not any(a.errors for a in s.apps)
    orders, billing = s.apps
    for a in s.apps:
        assert (root / a.evidence / "recording.har").is_file()
        assert not any(x["action"].startswith("tab-") for x in steps_of(root / a.evidence))
        flow = yaml.safe_load((root / a.flow).read_text("utf-8"))
        assert (
            flow["app"] == a.id
            and flow["requested_by"].startswith("labctl")
            and "draft" in (root / a.flow).read_text("utf-8").lower()
        )
        assert a.package and (root / a.package).is_file()
        assert "server database" in a.data
        assert any("bindings.yaml" in n for n in a.needs) and any(
            "draft flow" in n for n in a.needs
        )
        assert any("choose the database" in n for n in a.needs)
    # what each brief names, its own and nothing of the other's
    brief_o = (root / orders.brief).read_text("utf-8")
    brief_b = (root / billing.brief).read_text("utf-8")
    for path in ("/login", "/orders/search", "/orders/new/customer", "/orders/new/items",
                 "/orders/saved"):  # fmt: skip
        assert f"**{path}**" in brief_o, path
    own_o = brief_o.split("## Part of a flow")[0]  # the cross-app section names where links go
    for path in ("/billing/start", "/billing/lookup", "/billing/account", "/billing/invoice"):
        assert f"**{path}**" in brief_b and path not in own_o, path
    assert "/orders/" not in brief_b.split("## Part of a flow")[0]
    for sel in ("input#customerName", "select#product", "input#giftWrap", "textarea#notes",
                "input#deliveryDate", "button#saveOrder", "a#openBilling"):  # fmt: skip
        assert f"`{sel}`" in brief_o, sel
    for sel in ('input[name="orderRef"]', 'select[name="terms"]', 'input[name="vat"]',
                'textarea[name="memo"]'):  # fmt: skip
        assert sel in brief_b, sel
    assert "`GET /api/orders`" in brief_o and "`GET /billing/api/invoices`" in brief_b
    assert "POST /orders/new/items" in brief_o and "POST /billing/account" in brief_b
    assert orders.tables == ["orders"] and billing.tables == ["invoices"]
    schema_o = (root / "apps/orders/db/schema.sql").read_text("utf-8").lower()
    schema_b = (root / "apps/billing/db/schema.sql").read_text("utf-8").lower()
    for col in ("number", "customer", "order_date", "status", "total"):
        assert col in schema_o, col
    for col in ("payer", "terms", "amount", "vat", "memo", "total"):
        assert col in schema_b, col
    assert orders.screens >= 9 and billing.screens == 5  # 4 and the widget's stand-in
    # the cross-app section, in the brief and in the build task
    for app, brief, other in (("orders", brief_o, "billing"), ("billing", brief_b, "orders")):
        part = brief.split("## Part of a flow across applications", 1)[1].split("\n## ", 1)[0]
        assert f"`{other}`" in part and "Hand-overs" in part and "a guess" in part
        assert "orders-saved" in part and "billing-start" in part and "in a new tab" in part
        # the order number: shown in orders' #savedNumber, typed into billing (element, not shape)
        assert "`b#savedNumber` on `/orders/saved`" in part and "shape `" not in part
        texts = _zip_texts(root / s.apps[[a.id for a in s.apps].index(app)].package)
        assert "## Part of a flow across applications" in texts["BUILD_TASK.md"]
        assert f"apps/{app}/BRIEF.md" in texts
    assert "Comes before: `billing`" in brief_o and "Comes after: `orders`" in brief_b
    seq = yaml.safe_load((root / s.sequence).read_text("utf-8"))
    assert seq["apps"] == ["orders", "billing"] and s.handovers == 2
    travels = {(t["shape"], t["from"]["app"], t["to"]["field"]) for t in seq["travels"]}
    assert ("A-9", "orders", 'input[name="orderRef"]') in travels  # the order number
    assert ("A A-A", "orders", 'input[name="payer"]') in travels  # the customer's name
    assert ("9.9", "orders", 'input[name="amount"]') in travels  # the order's total
    assert ("A-9", "billing", "input#q") in travels  # and back: the number searched for
    summary = json.loads(multiapp.summary_file(recorded.rec).read_text("utf-8"))
    assert [a["id"] for a in summary["apps"]] == ["orders", "billing"]
    assert "orders" in built.output and "TO DO" in built.output


def test_the_packages_say_what_a_builder_needs(recorded, built):
    """The trial review's gaps, on the packages of the fixture flow (items 1 to 11)."""
    import sqlite3

    root, s = recorded.root, built.summary
    orders, billing = s.apps
    t_o, t_b = _zip_texts(root / orders.package), _zip_texts(root / billing.package)
    brief_o, brief_b = t_o["apps/orders/BRIEF.md"], t_b["apps/billing/BRIEF.md"]
    task_o, task_b = t_o["BUILD_TASK.md"], t_b["BUILD_TASK.md"]
    # 1. no behaviour rule with an empty or meaningless shape
    for brief in (brief_o, brief_b):
        assert "formatted as  " not in brief and "formatted as  or" not in brief
        assert not re.search(r"is set to '(A |9)", brief)  # a table is rows, not one long shape
    # 2. the embedded widget: in scope as a stand-in at this app's address, not out of scope
    out_b = brief_b.split("## Out of scope", 1)[1].split("\n## ", 1)[0]
    assert "**/widget/rates**" in brief_b and "built as a stand-in" in brief_b
    assert "`iframe#fxWidget`" in brief_b and "/widget/rates" not in out_b
    assert "rates_json" not in brief_b
    # 3. pages are not listed as tables
    assert "`start`" not in out_b and "`account`" not in out_b
    out_o = brief_o.split("## Out of scope", 1)[1].split("\n## ", 1)[0]
    assert "Tables: `customer`" not in out_o and "only guesses from a screen's layout" in out_o
    # 4. SQLite by default, said to be one; reserved words quoted; the schema loads
    for task, app in ((task_o, "orders"), (task_b, "billing")):
        assert "**SQLite**" in task and "**a default, not evidence**" in task, app
        assert "postgres assumed" not in task
    schema_b = t_b["apps/billing/db/schema.sql"]
    assert '"order" TEXT' in schema_b and '"number" TEXT' in schema_b
    for schema in (schema_b, t_o["apps/orders/db/schema.sql"]):
        sqlite3.connect(":memory:").executescript(schema)
    # 5. identifiers: pattern, highest seen, new ones, seed guidance
    assert "`ORD-` + 4 digits" in brief_o and "highest existing number + 1" in brief_o
    assert "`b#savedNumber`" in brief_o.split("## Identifiers", 1)[1]
    assert "`INV-5001`" in brief_b and "start with no seeded rows" in brief_b
    # 6. the application's own words, verbatim, in the pages and the brief
    acct = t_b["apps/billing/pages/billing-account.html"]
    for words in ("New invoice", "Payer", "Payment terms", "Add VAT", "Save invoice", "30 days"):
        assert words in acct, words
    assert "The amount is a number" in brief_b and "An order reference looks like" in brief_b
    saved = t_o["apps/orders/pages/orders-saved.html"]
    for words in ("<h1>Order saved</h1>", ">Delivery date<", ">Open in billing<", "was saved."):
        assert words in saved, words
    assert 'data-testid="nav-lookup"' in t_b["apps/billing/pages/billing-start.html"]
    # 7. no sentence about pictures the package does not have
    assert "evidence/billing/screenshots" not in task_b and not any(
        n.startswith("evidence/") for n in t_b)  # fmt: skip
    # 8. the other application's address, by variable, and what links to it
    assert "`BILLING_URL`" in brief_o and "`a#openBilling` on `/orders/saved`" in brief_o
    assert "`ORDERS_URL`" in brief_b and "When a variable is unset" in brief_b
    # 9. the step lists and the flow travel with the package; the brief cites them
    assert "apps/orders/steps/order-to-invoice.json" in t_o
    assert "apps/orders/steps/order-to-invoice.sequence.yaml" in t_o
    assert "flows/orders-order-to-invoice.yaml" in t_o and "evidence/_sequences" not in brief_o
    steps = json.loads(t_o["apps/orders/steps/order-to-invoice.json"])["steps"]
    assert all({"n", "action", "screen"} <= set(x) for x in steps)
    assert any(x.get("global") for x in steps) and not any("value" in x for x in steps)
    assert (
        "## What each recorded action did" in brief_o and "`POST /orders/new/items 303`" in brief_o
    )
    # 10. what applies from the zip alone
    for task in (task_o, task_b):
        assert "## Working from this package alone" in task and "without labctl" in task
    # 11. values travel field to field, with steps
    part = brief_b.split("## Part of a flow across applications", 1)[1]
    assert 'is typed into `input[name="orderRef"]`' in part


def test_build_all_again_repeats_nothing_and_keeps_filled_bindings(recorded, built):
    root = recorded.root
    part = root / "evidence" / "orders" / recorded.rec.name
    doc = yaml.safe_load((part / "bindings.yaml").read_text("utf-8"))
    first = next(iter(doc["steps"]))
    doc["steps"][first]["value"] = "clerk.syn"
    (part / "bindings.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")
    stamp = (part / "steps.jsonl").stat().st_mtime_ns
    lines: list[str] = []
    again = multiapp.build_all(root, recorded.rec, proposal=built.proposal, say=lines.append,
                               package_dir=root / "packages")  # fmt: skip
    assert "split: already done" in "\n".join(lines)
    assert (part / "steps.jsonl").stat().st_mtime_ns == stamp
    assert [a.package for a in again.apps] == [a.package for a in built.summary.apps]
    assert yaml.safe_load((part / "bindings.yaml").read_text("utf-8"))["steps"][first]["value"] == (
        "clerk.syn"
    )
    assert [a.flow_note for a in again.apps] == [
        "draft kept as it is (it may have been edited)"
    ] * 2


def test_no_typed_value_is_in_any_written_artifact(recorded, built):
    values = [*fixture_flow.TYPED.values(), recorded.number]
    texts: dict[str, str] = {}
    for p in recorded.root.rglob("*"):
        if p.is_file() and p.suffix.lower() not in (".png", ".zip"):
            texts[p.relative_to(recorded.root).as_posix()] = p.read_text("utf-8", "replace").lower()
        elif p.suffix == ".zip":
            for name, text in _zip_texts(p).items():
                texts[f"{p.name}!{name}"] = text.lower()
    assert len(texts) > 100
    leaks = [(name, v) for name, text in texts.items() for v in values
             if any(f in text for f in forms(v))]  # fmt: skip
    assert leaks == []
    # ... with the interface-text rule in force: the pages keep the application's own words
    pages = recorded.root / "apps" / "billing" / "pages"
    assert "save invoice" in texts["apps/billing/pages/billing-account.html"]
    # and in strict mode every text is masked, still with no typed value
    from labctl import factory, sanitize

    try:
        with sanitize.page_text("strict"):
            factory.generate(recorded.root, "billing", force=True)
        strict = {p.name: p.read_text("utf-8").lower() for p in pages.glob("*.html")}
        assert "save invoice" not in strict["billing-account.html"]
        assert "xxxx xxxxxxx" in strict["billing-account.html"]
        assert not [
            (n, v) for n, t in strict.items() for v in values if any(f in t for f in forms(v))
        ]
    finally:
        factory.generate(recorded.root, "billing", force=True)


def test_a_second_recording_of_another_flow_enhances_orders(recorded, built, servers, tmp_path):
    root = recorded.root
    model_path = root / "apps" / "orders" / "model.yaml"
    before = yaml.safe_load(model_path.read_text("utf-8"))
    rec2 = multiapp.recordings_dir(root) / "customers-and-chair"
    recorder.record(f"{servers.orders}/login", rec2, headless=True, script=fixture_flow.run_second)
    prop = sp.propose(rec2)
    assert len(prop.apps) == 1
    prop.apps[0].id = "orders"
    path = sp.save(prop, tmp_path / "second.split.yaml")
    done = multiapp.build_all(root, rec2, proposal=path, say=lambda m: None,
                              package_dir=root / "packages")  # fmt: skip
    assert [a.id for a in done.apps] == ["orders"] and not done.apps[0].errors
    after = yaml.safe_load(model_path.read_text("utf-8"))

    def screens(m):
        return {s["path"]: s for s in m["screens"] if s.get("path")}

    old, new = screens(before), screens(after)
    assert set(old) <= set(new) and "/customers" in new  # nothing lost, one screen more
    for path, s in old.items():  # every element kept (a described one may now have its selector)
        have = new[path].get("elements") or []
        assert {e["selector"] for e in s.get("elements") or [] if e.get("selector")} <= {
            e.get("selector") for e in have
        }, path
        assert {e["label"] for e in s.get("elements") or [] if not e.get("selector")} <= {
            e.get("label") for e in have
        }, path
    keys = lambda m: {f"{r['method']} {r['path']}" for r in m["routes"]}  # noqa: E731
    assert keys(before) <= keys(after) and "GET /api/customers" in keys(after)
    tables = lambda m: {t["name"] for t in m["tables"]}  # noqa: E731
    assert tables(before) <= tables(after) and {"orders", "customers"} <= tables(after)
    assert {e["id"] for e in before["evidence"]} < {e["id"] for e in after["evidence"]}
    brief = (root / "apps/orders/BRIEF.md").read_text("utf-8")
    assert "**/customers**" in brief and "**/orders/search**" in brief
    assert "## Part of a flow across applications" in brief  # still part of the first flow
    assert done.apps[0].flow == "flows/orders-customers-and-chair.yaml"
    values = [*fixture_flow.SECOND_TYPED, "Second Flow Person"]
    for p in (root / "evidence" / "orders" / rec2.name).rglob("*"):
        if p.is_file() and p.suffix != ".png":
            text = p.read_text("utf-8", "replace").lower()
            assert not any(f in text for v in values for f in forms(v)), p


def fill_bindings(part: Path, values: dict[str, list[str]]) -> None:
    """What a person does: give each step that needs one a synthetic value, field by field."""
    doc = yaml.safe_load((part / "bindings.yaml").read_text("utf-8"))
    used: dict[str, int] = {}
    for entry in doc["steps"].values():
        if "value" not in entry:
            continue
        key = max((k for k in values if k in (entry.get("field") or "")), key=len)
        n = used[key] = used.get(key, -1) + 1
        entry["value"] = values[key][n]
    (part / "bindings.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")


def test_each_application_replays_its_own_part_of_the_flow(recorded, built):
    from labctl.behaviour import fidelity

    fresh = fixture_apps.serve()  # each replay on an application with its own fresh data
    try:
        for app, base in (("orders", fresh.orders), ("billing", fresh.billing)):
            part = recorded.root / "evidence" / app / recorded.rec.name
            fill_bindings(part, fixture_flow.SYNTHETIC[app])
            tab = "t1" if app == "orders" else "t2"
            expected = [a for a, t, _ in fixture_flow.EXPECTED
                        if t == tab and not a.startswith("tab-")]  # fmt: skip
            report = fidelity.replay(part, base, expected=expected, resolve_timeout_ms=4000)
            assert report.status == "pass", report.markdown()
            c = report.coverage
            assert c.replayed == c.steps == len(steps_of(part)) and c.missing_expected == []
        # the replays did what the session did, with the synthetic values
        assert any(i.payer == "Syn Payer" for i in fresh.apps["billing"].invoices.values())
        assert any(o.customer == "Syn Customer" for o in fresh.apps["orders"].orders.values())
    finally:
        fresh.stop()


def test_the_portal_shows_the_recording_its_split_and_where_each_app_came_from(recorded, built,
                                                                                monkeypatch):  # fmt: skip
    from fastapi.testclient import TestClient

    from labctl.portal.app import create_app

    monkeypatch.setenv("LABCTL_ROOT", str(recorded.root))
    monkeypatch.setenv("TWINLAB_STATE", str(recorded.root / ".lab-state"))
    client = TestClient(create_app(recorded.root))
    r = client.get("/recordings")
    assert (
        r.status_code == 200 and "order-to-invoice" in r.text and "applications created" in r.text
    )
    r = client.get("/recordings/order-to-invoice")
    assert r.status_code == 200
    for text in ('value="orders"', 'value="billing"', "embedded", "Create applications",
                 "/apps/orders", "in a new tab", "t2"):  # fmt: skip
        assert text in r.text, text
    r = client.get("/apps/orders")
    assert r.status_code == 200 and "Part of a flow across applications" in r.text
    assert 'href="/apps/billing"' in r.text and 'href="/recordings/order-to-invoice"' in r.text
    # rename through the form: refused when it is not a valid name, saved when it is
    form = {"id_0": "Bad Name", "include_0": "1", "id_1": "billing", "include_1": "1"}
    r = client.post("/recordings/order-to-invoice/proposal", data=form, follow_redirects=False)
    assert r.status_code == 303 and "Not+saved" in r.headers["location"]
    form["id_0"] = "orders"
    form["separate"] = fixture_apps_widget(recorded)
    r = client.post("/recordings/order-to-invoice/proposal", data=form, follow_redirects=False)
    assert r.status_code == 303 and "kind=error" not in r.headers["location"]
    saved = sp.load(multiapp.proposal_file(recorded.rec))
    assert [e.id for e in saved.apps][:2] == ["orders", "billing"] and len(saved.apps) == 3
    # put the reviewed proposal back as it was, for any test after this one
    sp.save(sp.load(built.proposal), multiapp.proposal_file(recorded.rec))


def fixture_apps_widget(recorded) -> str:
    prop = sp.load(multiapp.proposal_file(recorded.rec))
    return next(o for e in prop.apps for o in e.embedded)
