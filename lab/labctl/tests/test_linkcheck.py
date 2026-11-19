"""The link check (`labctl evidence check-links`): where the links a recording saw but never
followed lead in the real application, asked safely, and what the navigation map says then.

Units first (no browser, no network beyond 127.0.0.1). Then the order-desk fixture
(fixtures/linkcheck/shop_site.py) is recorded once with the real recorder, built into a brief,
checked, and built again. The fixture server logs every request it receives, so the tests prove
what was never asked for.
"""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE / "fixtures" / "linkcheck"))

import shop_site  # noqa: E402

from labctl import linkcheck  # noqa: E402

TYPED = {"user": "kim.tester", "password": "Pebble-Fern-31"}
NEVER_ASKED = ("/logout", "/delete", "/export", "/statements", "/orders/bulk", "/reports")


# =================================================================================================
# Units
# =================================================================================================


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://a.test/logout", "`logout`"),
        ("https://a.test/account/sign-out", "`signout`"),
        ("https://a.test/SignOut", "`signout`"),
        ("https://a.test/orders/1007/delete", "`delete`"),
        ("https://a.test/items/remove-all", "`remove`"),
        ("https://a.test/booking/cancel", "`cancel`"),
        ("https://a.test/claims/9/approve", "`approve`"),
        ("https://a.test/claims/9/reject", "`reject`"),
        ("https://a.test/form/submit", "`submit`"),
        ("https://a.test/confirm", "`confirm`"),
        ("https://a.test/unsubscribe", "`unsubscribe`"),
        ("https://a.test/password/reset", "`reset`"),
        ("https://a.test/ticket/4/close", "`close`"),
        ("https://a.test/archive", "`archive`"),
        ("https://a.test/invoice/pay", "`pay`"),
        ("https://a.test/transfer", "`transfer`"),
        ("https://a.test/items?action=view", "action"),
        ("https://a.test/items?do=x", "action"),
        ("https://a.test/items?op=deleteItem", "action"),
        ("https://a.test/items?status=open&update=1", "`update`"),
        ("https://a.test/x?token=abc", "token or session"),
        ("https://a.test/x?sid=1", "token or session"),
        ("https://a.test/x?ref=Zx81Kq7Lm2Np9Rt4Vw6Yb3Hc", "token or session"),
        ("https://a.test/x;jsessionid=ABC123", "session id"),
        ("mailto:someone@a.test", "mailto:"),
        ("tel:+100", "tel:"),
        ("javascript:void(0)", "javascript:"),
        ("data:text/html,hi", "data:"),
        ("https://a.test/report.pdf", "download"),
        ("https://a.test/list.xlsx", "download"),
        ("https://a.test/a.docx", "download"),
        ("https://a.test/a.csv", "download"),
        ("https://a.test/a.zip", "download"),
        ("https://a.test/setup.exe", "download"),
        ("https://a.test/setup.msi", "download"),
    ],
)
def test_state_changing_and_unsafe_addresses_are_never_asked(url, reason):
    why = linkcheck.unsafe(url)
    assert why and reason in why, (url, why)


@pytest.mark.parametrize("url", ["https://a.test/orders", "https://a.test/orders/1007",
                                 "https://a.test/payments", "https://a.test/cancellations/list",
                                 "https://a.test/x?status=closed&page=2&sort=title",
                                 "https://a.test/help/popup"])  # fmt: skip
def test_reading_addresses_are_allowed(url):
    assert linkcheck.unsafe(url) is None


def test_a_download_attribute_is_a_download():
    assert "download" in linkcheck.unsafe("https://a.test/x", {"download": ""})


def test_the_fetcher_sends_only_get_or_head_to_its_own_origins():
    fetch = linkcheck.SafeFetcher(["http://127.0.0.1:9"], delay_ms=0)
    for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        with pytest.raises(linkcheck.Refused, match="only GET and HEAD"):
            fetch.fetch("http://127.0.0.1:9/x", method)
    with pytest.raises(linkcheck.Refused, match="origin"):
        fetch.fetch("http://127.0.0.2:9/x", "GET")
    with pytest.raises(linkcheck.Refused, match="origin"):
        fetch.fetch("https://partner.example.test/x", "HEAD")
    assert fetch.count == 0


def test_the_fetcher_waits_between_requests_and_stops_on_429():
    run = shop_site.serve()
    sleeps: list[float] = []
    try:
        fetch = linkcheck.SafeFetcher([run.url], delay_ms=1000, clock=lambda: 50.0,
                                      sleep=sleeps.append)  # fmt: skip
        assert fetch.fetch(f"{run.url}/history", "HEAD").status == 200
        assert fetch.fetch(f"{run.url}/gone", "GET").status == 404
        assert sleeps == [1.0]  # one request a second
        with pytest.raises(linkcheck.Throttled):
            fetch.fetch(f"{run.url}/busy", "HEAD")
        with pytest.raises(linkcheck.Refused, match="stopped"):
            fetch.fetch(f"{run.url}/history", "HEAD")
        assert [(r["method"], r["path"]) for r in run.log] == [
            ("HEAD", "/history"), ("GET", "/gone"), ("HEAD", "/busy")]  # fmt: skip
        assert all(r["body"] == 0 for r in run.log)
    finally:
        run.stop()


def test_the_storage_state_cookie_goes_only_to_its_host():
    cookies = [{"name": "s", "value": "v1", "domain": "127.0.0.1", "path": "/"},
               {"name": "t", "value": "v2", "domain": ".other.test", "path": "/"},
               {"name": "u", "value": "v3", "domain": "127.0.0.1", "path": "/", "secure": True},
               {"name": "w", "value": "v4", "domain": "127.0.0.1", "path": "/", "expires": 1}]  # fmt: skip
    fetch = linkcheck.SafeFetcher(["http://127.0.0.1:1"], cookies=cookies)
    assert fetch._cookie_header("http://127.0.0.1:1/a") == "s=v1"


def test_a_planted_data_value_is_caught_by_the_data_check():
    from labctl.lookfeel import Data

    data = Data(names={"solvang", "ingrid"}, tokens={"ORD-5521"})
    clean = "<h1>Order history</h1><label>Customer</label><table><td>Xxxxxx 9999</td></table>"
    assert linkcheck.data_findings(clean, data) == []
    planted = clean.replace("Customer", "Customer Ingrid Solvang")
    assert linkcheck.data_findings(planted, data) == ["A"]  # the shape, never the value
    assert linkcheck.data_findings("<p>see ORD-5521</p>", data) == ["A-9"]


def test_only_the_command_line_ever_runs_the_check():
    """The portal and build-all never call it on their own."""
    pkg = REPO / "lab" / "labctl" / "labctl"
    callers = [p.relative_to(pkg).as_posix() for p in pkg.rglob("*.py")
               if "linkcheck.check(" in p.read_text(encoding="utf-8")]  # fmt: skip
    assert callers == ["evidence_cli.py"]


# =================================================================================================
# The order desk, recorded, checked, built
# =================================================================================================


def _flow(page) -> None:
    page.wait_for_timeout(500)
    page.fill("#username", TYPED["user"])
    page.fill("#password", TYPED["password"])
    page.click("#signIn")
    page.wait_for_timeout(800)
    page.click("#orders tbody tr:first-child a.order-link")
    page.wait_for_timeout(800)


def _build(root: Path, app: str):
    import warnings

    from labctl import factory
    from labctl.builder import package as pkg

    factory.generate(root, app, force=True)
    nav_file = root / "apps" / app / "navigation.json"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pkg.PackageWarning)
        path = pkg.build(root, app, root / "packages")
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
    return (nav_file.read_text("utf-8"), (root / "apps" / app / "BRIEF.md").read_text("utf-8"),
            names)  # fmt: skip


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    from labctl import recorder

    root = tmp_path_factory.mktemp("linkcheck") / "repo"
    for d in ("apps", "automations", "lab", "flows", "docs"):
        (root / d).mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "AGENTS.md", root / "AGENTS.md")
    shutil.copy(REPO / "docs" / "factory-playbook.md", root / "docs" / "factory-playbook.md")
    rec = root / "evidence" / "shop" / "run"
    run = shop_site.serve()
    try:
        recorder.record(f"{run.url}/orders", rec, headless=True, script=_flow, screenshots=False)
        nav_before, brief_before, _ = _build(root, "shop")
        run.log.clear()
        said: list[str] = []
        dry = linkcheck.check(rec, say=said.append)
        dry_log = list(run.log)
        dry_file = (rec / linkcheck.FILE).exists()
        doc = linkcheck.check(rec, yes=True, delay_ms=0, say=lambda m: None)
        log = list(run.log)
        nav_after, brief_after, packaged = _build(root, "shop")
        copied = root / "apps" / "shop" / "unrecorded" / "history.html"
        copied_text = copied.read_text("utf-8") if copied.is_file() else None
        # with the recording's session: a copy, so the anonymous result stays
        signed = root / "evidence" / "signed" / "run"
        shutil.copytree(rec, signed)
        state = root / "state.json"
        state.write_text(json.dumps({"cookies": [
            {"name": shop_site.COOKIE, "value": shop_site.GOOD_SESSION, "domain": "127.0.0.1",
             "path": "/", "expires": -1, "httpOnly": True, "secure": False}], "origins": []}),
            "utf-8")  # fmt: skip
        run.log.clear()
        doc_signed = linkcheck.check(signed, yes=True, delay_ms=0, max_links=3,
                                     storage_state=state, say=lambda m: None)  # fmt: skip
        log_signed = list(run.log)
        # the file taken away: the next build is the one before the check
        moved = rec.parent / "moved.json"
        (rec / linkcheck.FILE).rename(moved)
        nav_again, brief_again, _ = _build(root, "shop")
        unrecorded_again = (root / "apps" / "shop" / "unrecorded").exists()
        moved.rename(rec / linkcheck.FILE)
    finally:
        run.stop()
    return SimpleNamespace(
        root=root, rec=rec, url=run.url, said=said, dry=dry, dry_log=dry_log, dry_file=dry_file,
        doc=doc, log=log, by={r["route"]: r for r in doc["links"]},
        nav_before=nav_before, brief_before=brief_before, nav=json.loads(nav_after),
        brief=brief_after, packaged=packaged, signed=signed, doc_signed=doc_signed,
        log_signed=log_signed, nav_again=nav_again, brief_again=brief_again,
        unrecorded_again=unrecorded_again, copied=copied_text)  # fmt: skip


def test_without_yes_nothing_is_asked_and_the_plan_is_listed(shop):
    assert shop.dry_log == [] and not shop.dry_file
    assert shop.dry["dry_run"] is True
    text = "\n".join(shop.said)
    assert "nothing was requested" in text and "Run again with --yes" in text
    assert f"would link        HEAD {shop.url}/history" in text
    assert "skip  link        mailto:: a mailto: address" in text
    assert "statements?session=...:" in text  # a query's values are never printed
    assert shop_site.CUSTOMERS[0] not in text


def test_links_are_collected_resolved_and_one_per_pattern(shop):
    by = shop.by
    assert {"/history", "/account", "/old-orders", "/gone", "/broken", "/logout", "/offers",
            "/export/orders.pdf", "mailto:", "/statements", "/search", "/orders/bulk",
            "/help/popup", "/map/north", "/orders/{id}/delete", "/busy", "/reports"} == set(by)  # fmt: skip
    # visited patterns are not links to check: /orders/1008 is /orders/{id}, recorded
    assert "/orders/{id}" not in by and "/orders" not in by and "/login" not in by
    assert by["/search"]["kind"] == "form (GET)"  # a relative action, resolved on the page
    assert by["/help/popup"]["kind"] == "script" and by["/map/north"]["kind"] == "area"
    delete = by["/orders/{id}/delete"]
    assert delete["seen"][0]["screen"] == "/orders"
    assert delete["seen"][0]["selector"] == "a.delete-link"
    # the link text as the sanitiser has it: "Delete" repeats on every data row, so it is masked
    assert delete["seen"][0]["text"] in ("Delete", "Xxxxxx")
    assert by["/history"]["seen"][0] == {"screen": "/orders", "selector": "a#navHistory",
                                         "text": "Order history"}  # fmt: skip


def test_the_server_got_only_get_and_head_and_never_a_skipped_address(shop):
    assert shop.log, "the check asked for nothing"
    assert {r["method"] for r in shop.log} <= {"GET", "HEAD"}
    assert all(r["body"] == 0 for r in shop.log)
    assert all(r["ua"] == linkcheck.USER_AGENT and r["cookie"] == "" for r in shop.log)
    for r in shop.log:
        assert not any(bad in r["path"] for bad in NEVER_ASKED), r
    by = shop.by
    for route, reason in (("/logout", "`logout`"), ("/orders/{id}/delete", "`delete`"),
                          ("/offers", "another site"), ("/export/orders.pdf", "download"),
                          ("mailto:", "mailto:"), ("/statements", "token or session"),
                          ("/orders/bulk", "POST form")):  # fmt: skip
        assert by[route]["state"] == "skipped" and reason in by[route]["reason"], route
    assert ("HEAD", "/help/popup") in [(r["method"], r["path"]) for r in shop.log]
    assert by["/help/popup"]["methods"] == ["HEAD", "GET"]  # HEAD refused: GET


def test_each_kind_of_answer_is_recorded(shop):
    by = shop.by
    h = by["/history"]
    assert (h["finding"], h["status"], h["content_type"]) == ("unrecorded page", 200, "text/html")
    assert h["title"] == "Order history" and h["heading"] == "Order history"
    assert h["ends_on_sign_in"] is False and "matches" not in h
    acc = by["/account"]
    assert acc["finding"] == "sign-in" and acc["ends_on_sign_in"] is True
    assert acc["redirects"] == [{"status": 302, "to": "/login", "origin": "same"}]
    old = by["/old-orders"]
    assert old["finding"] == "recorded screen"
    assert old["matches"] == {"screen": "/orders/{id}", "by": "the same address pattern"}
    assert old["redirects"] == [{"status": 301, "to": "/orders/{id}", "origin": "same"}]
    assert (by["/gone"]["finding"], by["/gone"]["status"]) == ("missing", 404)
    assert (by["/broken"]["finding"], by["/broken"]["status"]) == ("error", 500)
    assert by["/map/north"]["finding"] == "missing"
    # the first 429 stops the run: nothing after it is asked for
    assert by["/busy"]["state"] == "stopped here" and by["/busy"]["status"] == 429
    assert by["/reports"]["state"] == "not checked"
    assert shop.doc["stopped"]["status"] == 429 and shop.doc["stopped"]["route"] == "/busy"
    assert shop.log[-1]["path"] == "/busy"
    assert shop.doc["session"] == "anonymous" and shop.doc["sign_in"] == ["/login"]


def test_the_output_holds_no_body_cookie_token_or_data(shop):
    text = (shop.rec / linkcheck.FILE).read_text("utf-8")
    snaps = "\n".join(p.read_text("utf-8") for p in (shop.rec / linkcheck.PAGES).glob("*.html"))
    for value in (*shop_site.CUSTOMERS, "Ingrid", "Solvang", "Torvald", "ORD-5521", "1,234.50",
                  "88.20", "Zx81Kq7Lm2Np9Rt4Vw6Yb3Hc", shop_site.GOOD_SESSION, "rec-1",
                  "help@shop-fixture.test", *TYPED.values()):  # fmt: skip
        assert value not in text, value
        assert value not in snaps, value
    for body_text in ("How to use the order desk", "Something went wrong", "<html", "<td"):
        assert body_text not in text, body_text
    signed = (shop.signed / linkcheck.FILE).read_text("utf-8")
    assert shop.doc_signed["session"].startswith("the recording's saved browser state")
    assert shop_site.GOOD_SESSION not in signed and shop_site.COOKIE not in signed


def test_an_unvisited_page_is_kept_as_a_sanitised_snapshot(shop):
    page = (shop.rec / shop.by["/history"]["snapshot"]).read_text("utf-8")
    assert shop.by["/history"]["snapshot"] == "link-check/pages/history.html"
    for kept in ("<title>Order history</title>", "<h1>Order history</h1>", 'id="historyFilter"',
                 ">From date</label>", ">Customer</label>", 'name="customer"',
                 ">Show</button>", "<th>Total</th>", 'id="historyTable"'):  # fmt: skip
        assert kept in page, kept
    assert "<td>XXX-9999</td>" in page and "Xxxxxx Xxxxxxx" in page
    # a sign-in page, a recorded screen and an error are no unrecorded page
    for route in ("/account", "/old-orders", "/broken", "/gone"):
        assert "snapshot" not in shop.by[route], route


def test_with_the_recordings_session_its_cookie_is_sent_and_never_kept(shop):
    assert shop.log_signed and all(f"{shop_site.COOKIE}={shop_site.GOOD_SESSION}" == r["cookie"]
                                   for r in shop.log_signed)  # fmt: skip
    by = {r["route"]: r for r in shop.doc_signed["links"]}
    assert (
        by["/account"]["finding"] == "unrecorded page" and by["/account"]["title"] == "My account"
    )
    assert sum(1 for r in shop.doc_signed["links"] if r["state"] == "checked") == 3  # --max 3
    assert by["/broken"]["state"] == "not checked" and "--max 3" in by["/broken"]["reason"]


def test_a_planted_data_value_keeps_only_title_and_heading(shop, tmp_path, monkeypatch):
    from labctl import sanitize

    real = sanitize.page

    def leaky(html, *a, **k):  # a sanitiser that missed a recorded name on the page
        info = real(html, *a, **k)
        if "historyTable" in html:
            info.html = info.html.replace("<h1>Order history</h1>",
                                          "<h1>Order history</h1><p>Ingrid Solvang</p>")  # fmt: skip
        return info

    rec = tmp_path / "run"
    shutil.copytree(shop.rec, rec)
    run = shop_site.serve()
    try:
        # the recorded origin is the first server's: point the copy at this one
        old = shop.url.encode()
        for path in [rec / "steps.jsonl", *rec.glob("dom/*.html"), rec / "recording.har"]:
            path.write_bytes(path.read_bytes().replace(old, run.url.encode()))
        monkeypatch.setattr(sanitize, "page", leaky)
        doc = linkcheck.check(rec, yes=True, delay_ms=0, max_links=1, say=lambda m: None)
    finally:
        run.stop()
    row = next(r for r in doc["links"] if r["route"] == "/history")
    assert "snapshot" not in row and not (rec / linkcheck.PAGES / "history.html").exists()
    assert "data check found" in row["snapshot_refused"] and "Ingrid" not in row["snapshot_refused"]
    assert row["title"] == "Order history" and row["heading"] == "Order history"


def test_the_navigation_map_says_what_the_check_found(shop):
    screens = {s["path"]: s for s in shop.nav["screens"]}
    assert "/history" not in screens  # never mixed with recorded screens
    loose = {x["selector"]: x for x in screens["/orders"]["links_seen_not_visited"]}
    hist = loose["a#navHistory"]
    assert hist["status"] == "exists, not recorded"
    assert hist["answer"].startswith("leads to a page *Order history* not recorded: out of scope, "
                                     "answer `GET /history` with a redirect (303) to")  # fmt: skip
    assert loose["a#navAccount"]["status"] == "sign-in"
    assert "leads to the sign-in page (`/login`)" in loose["a#navAccount"]["answer"]
    assert loose["a#navOld"]["status"] == "recorded screen"
    assert "`/orders/{id}`" in loose["a#navOld"]["answer"]
    assert loose["a#navGone"]["answer"] == ("missing (404) in the real application: keep the "
                                            "link, answer 404")  # fmt: skip
    assert loose["a#navBroken"]["status"] == "error"
    out = loose["a#signOut"]
    assert out["status"] == "out of scope" and "not asked for by the link check (looks like it "\
        "changes something (`logout` in the address))" in out["answer"]  # fmt: skip
    assert loose["a#reports"]["answer"].endswith("(the run stopped before it)")
    pages = {p["path"]: p for p in shop.nav["unrecorded_pages"]}
    assert set(pages) == {"/history", "/search", "/help/popup"}
    assert pages["/history"]["page"] == "apps/shop/unrecorded/history.html"
    assert pages["/history"]["in_scope"] is False and pages["/history"]["linked_from"] == [
        "/orders"
    ]
    assert shop.copied == (shop.rec / linkcheck.PAGES / "history.html").read_text("utf-8")
    assert "apps/shop/unrecorded/history.html" in shop.packaged
    assert not any(n.startswith("apps/shop/pages/history") for n in shop.packaged)
    assert shop.nav["link_check"][0]["session"] == "anonymous"
    brief = shop.brief
    assert "**Pages that exist but were not recorded**" in brief
    assert (
        '- `/history` "Order history", linked from `/orders`: reference snapshot '
        "`apps/shop/unrecorded/history.html`."
    ) in brief
    assert "leads to a page *Order history* not recorded" in brief
    assert "missing (404) in the real application: keep the link, answer 404" in brief


def test_without_the_file_nothing_changes(shop):
    nav = json.loads(shop.nav_before)
    assert "unrecorded_pages" not in nav and "link_check" not in nav
    assert "Pages that exist but were not recorded" not in shop.brief_before
    loose = {x["selector"]: x for s in nav["screens"] for x in s.get("links_seen_not_visited", [])}
    assert loose["a#navHistory"]["status"] == "out of scope"
    assert "link check" not in shop.nav_before
    assert shop.nav_again == shop.nav_before and not shop.unrecorded_again
    assert shop.brief_again == shop.brief_before


def test_the_command_line_lists_and_asks_nothing_without_yes(shop):
    from typer.testing import CliRunner

    from labctl.cli import app

    result = CliRunner().invoke(app, ["evidence", "check-links", str(shop.rec), "--max", "2"])
    assert result.exit_code == 0, result.output
    assert "2 link(s) would be asked for" in result.output
    assert "over the limit (--max 2)" in result.output
