"""R09: the coverage matrix from synthetic models, declarations and reports (no browser, no lab)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from labctl import conformance
from labctl import coverage as cov
from labctl.behaviour import acceptance as acc
from labctl.behaviour import durable, fidelity
from labctl.botlog.recording import load as load_recording
from labctl.cli import app as cli_app

APP = "demo"
REC = "rec1"
HAR = {"log": {"version": "1.2", "entries": []}}


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    monkeypatch.setattr(conformance, "image_id", lambda app_id: None)


def _model() -> dict:
    flow = ["har:rec", "flow:demo"]
    return {
        "app": APP,
        "evidence": [
            {"id": "har:rec", "kind": "har", "complete": []},
            {"id": "flow:demo", "kind": "flow", "complete": []},
        ],
        "stack": {
            "frontend": {"framework": "react", "marker": "react-dom"},
            "data": {
                "kind": "server",
                "persistence": "server",
                "label": "server",
                "storage": [],
                "keys": [],
                "evidence": ["1 write"],
            },
        },
        "screens": [
            {
                "name": "orders",
                "path": "/orders",
                "basis": "observed",
                "from": flow,
                "snapshots": 3,
                "calls": [
                    {"method": "GET", "path": "/api/orders"},
                    {"method": "POST", "path": "/api/orders"},
                    {"method": "GET", "path": "/api/orders/{id}"},
                ],
                "elements": [
                    {
                        "selector": "#search",
                        "tag": "input",
                        "type": "text",
                        "actions": ["type"],
                        "basis": "observed",
                        "from": flow,
                    },
                    {
                        "selector": "button.save",
                        "tag": "button",
                        "actions": ["click"],
                        "basis": "observed",
                        "from": flow,
                    },
                    {
                        "selector": "#ghost",
                        "tag": "button",
                        "actions": ["click"],
                        "basis": "assumed",
                        "from": [],
                    },
                ],
            },
        ],
        "routes": [
            {
                "method": "GET",
                "path": "/api/orders",
                "basis": "observed",
                "from": flow,
                "responses": {"200": {"items[]": {"id": "int"}}},
            },
            {
                "method": "POST",
                "path": "/api/orders",
                "basis": "observed",
                "from": flow,
                "responses": {"201": {"id": "int"}, "422": {"detail": "string"}},
            },
            {
                "method": "GET",
                "path": "/api/orders/{id}",
                "basis": "observed",
                "from": flow,
                "tables": ["orders", "guess"],
                "responses": {"200": {"id": "int"}},
            },
        ],
        "tables": [
            {
                "name": "orders",
                "basis": "observed",
                "from": ["ddl:s"],
                "columns": [{"name": "id", "type": "integer", "primary_key": True}],
            },
            {
                "name": "guess",
                "basis": "assumed",
                "from": [],
                "columns": [{"name": "x", "type": "text"}],
            },
        ],
        "rules": [],
    }


def _app_yaml(rules=None) -> dict:
    return {
        "id": APP,
        "name": "Demo",
        "copies": {"system": "Demo system", "ref": "sha256:0123456789abcdef"},
        "stack": {},
        "run": {"web": {"port": 8000}},
        "lab": {"port": 8801},
        "rules": rules
        if rules is not None
        else [
            {
                "id": "R1",
                "where": "POST /api/orders",
                "rule": "Only a clerk role may edit an order",
                "verification": "hypothesis",
            },
            {
                "id": "R2",
                "where": "orders",
                "rule": "Two users cannot save the same order at the same time",
                "verification": "hypothesis",
            },
        ],
    }


def make_world(tmp_path: Path, model: dict | None = None, rules=None) -> Path:
    root = tmp_path / "repo"
    folder = root / "apps" / APP
    (folder / "db").mkdir(parents=True)
    (folder / "model.yaml").write_text(yaml.safe_dump(model or _model()), encoding="utf-8")
    (folder / "app.yaml").write_text(yaml.safe_dump(_app_yaml(rules)), encoding="utf-8")
    (folder / "BRIEF.md").write_text("# brief\n", encoding="utf-8")
    (folder / "db" / "schema.sql").write_text(
        "CREATE TABLE orders (id integer);\n", encoding="utf-8"
    )
    (root / "flows").mkdir()
    (root / "flows" / "demo.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "demo",
                "app": APP,
                "requested_by": "t",
                "steps": [{"screen": "orders", "path": "/orders"}],
            }
        ),
        encoding="utf-8",
    )
    rec = root / "evidence" / APP / REC
    (rec / "dom").mkdir(parents=True)
    (rec / "recording.har").write_text(json.dumps(HAR), encoding="utf-8")
    steps = [
        {"seq": 1, "ts": "2026-11-18T10:00:01Z", "url": "https://x.test/orders", "action": "goto"},
        {
            "seq": 2,
            "ts": "2026-11-18T10:00:02Z",
            "url": "https://x.test/orders",
            "action": "type",
            "selector": "#search",
            "dom": "dom/0002.html",
        },
        {
            "seq": 3,
            "ts": "2026-11-18T10:00:03Z",
            "url": "https://x.test/orders",
            "action": "goto",
            "nav": "reload",
            "dom": "dom/0003.html",
        },
        {
            "seq": 4,
            "ts": "2026-11-18T10:00:04Z",
            "url": "https://x.test/orders",
            "action": "drag",
            "selector": "#file",
        },
    ]
    (rec / "steps.jsonl").write_text(
        "\n".join(json.dumps(s) for s in steps) + "\n", encoding="utf-8"
    )
    for n in (2, 3):
        (rec / "dom" / f"{n:04d}.html").write_text(
            "<html><body><input id=search></body></html>", encoding="utf-8"
        )
    return root


def write_cases(root: Path, cases: list[dict], **extra) -> Path:
    path = root / "evidence" / APP / REC / "acceptance.yaml"
    path.write_text(
        yaml.safe_dump({"version": 1, "recording": REC, "cases": cases, **extra}), encoding="utf-8"
    )
    return path


def keep_report(
    root: Path, results: dict[str, str], steps=None, verdict="PASS WITHIN DECLARED SCOPE"
):
    """A kept replay report whose fingerprint matches what is on disk now."""
    rec_path = root / "evidence" / APP / REC
    rec = load_recording(rec_path)
    used = {n: rec_path / f"{n}.yaml" for n in durable.FILES}
    fp = durable.for_run(rec, used, {"name": "chromium", "version": "1"},
                         {"width": 1280, "height": 720})  # fmt: skip
    report = fidelity.FidelityReport(
        recording=REC, status="pass", verdict=verdict, steps=steps or [],
        cases=[acc.CaseResult(id=i, check="count", category="behaviour", status=s)
               for i, s in results.items()],
        fingerprint=fp)  # fmt: skip
    fidelity.save(root, APP, report)
    return report


def by_id(report: cov.CoverageReport) -> dict[str, cov.Row]:
    return {r.id: r for r in report.rows}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("TWINLAB_STATE", str(tmp_path / "state"))
    return make_world(tmp_path)


# --- row kinds and statuses -----------------------------------------------------------------------


def test_every_kind_of_row_is_listed_with_evidence_and_no_assertion(world):
    r = cov.build(world, APP)
    rows = by_id(r)
    assert rows["screen:/orders"].status == "EVIDENCED"
    assert rows["screen:/orders"].evidence[:2] == ["har:rec", "flow:demo"]
    assert rows["action:/orders:#search"].status == "EVIDENCED"
    assert rows["route:GET /api/orders"].status == "EVIDENCED"
    assert rows["status:POST /api/orders:422"].status == "EVIDENCED"
    assert rows["table:orders"].status == "EVIDENCED"
    assert rows["column:orders.id"].kind == "column"
    assert rows["rule:R1"].status == "ASSUMED"  # no model rule carries it: nothing shows it
    assert {
        "screens",
        "actions",
        "apis",
        "data",
        "rules",
        "roles",
        "boundaries",
        "negative",
        "errors",
        "persistence",
        "concurrency",
    } <= set(r.by_category)
    assert sum(r.counts.values()) == r.denominator == len(r.rows)
    assert r.counts["VERIFIED"] == 0


def test_assumed_unobserved_unsupported_and_blocked_are_kept_apart(world):
    (world / "apps" / APP / "BUILD-NOTES.md").write_text(
        "# Notes\n\n## Assumptions\n\n- The currency is euro\n- Dates are UTC\n\n## Other\n- x\n",
        encoding="utf-8")  # fmt: skip
    r = cov.build(world, APP)
    rows = by_id(r)
    assert rows["action:/orders:#ghost"].status == "ASSUMED"  # no evidence names it
    assert rows["table:guess"].status == "ASSUMED"
    assert (
        rows["assumption:notes-1"].status == "ASSUMED"
        and "euro" in rows["assumption:notes-1"].label
    )
    assert rows["assumption:notes-2"].status == "ASSUMED"
    for rid in ("roles:permissions", "persistence:new-session", "negative:unauthorised",
                "boundary:/orders", "errors:GET /api/orders", "negative:not-found:GET /api/orders/{id}"):  # fmt: skip
        assert rows[rid].status in ("UNOBSERVED", "EVIDENCED"), rid
    assert rows["negative:validation:POST /api/orders"].status == "EVIDENCED"  # 422 was seen
    assert rows["errors:POST /api/orders"].status == "EVIDENCED"
    assert rows["errors:GET /api/orders"].status == "UNOBSERVED"
    assert rows["negative:not-found:GET /api/orders/{id}"].status == "UNOBSERVED"
    assert rows["unsupported:drag"].status == "UNSUPPORTED"
    assert rows["concurrency:R2"].kind == "concurrency"
    assert "concurrency:R1" not in rows  # only a rule that mentions it


def test_a_passing_case_verifies_only_what_has_evidence(world):
    write_cases(world, [
        {"id": "c-search", "check": "value", "locator": "#search", "equals": "synthetic",
         "after": 2},
        {"id": "c-reload", "check": "count", "locator": "#list li", "equals": 1, "reload": True},
        {"id": "c-roles", "check": "text", "locator": "#who", "equals": "clerk",
         "covers": ["roles:permissions"]},
        {"id": "c-unobserved", "check": "text", "locator": "#e", "equals": "x",
         "covers": ["errors:GET /api/orders"]},
    ])  # fmt: skip
    keep_report(world, {"c-search": "PASS", "c-reload": "PASS", "c-roles": "PASS",
                        "c-unobserved": "PASS"})  # fmt: skip
    rows = by_id(cov.build(world, APP))
    assert rows["action:/orders:#search"].status == "VERIFIED"
    assert "acceptance case c-search" in rows["action:/orders:#search"].assertion
    assert rows["persistence:reload"].status == "VERIFIED"  # reload recorded + a reload case passes
    # a pointer to roles is not an answer: no cited source, so not verified
    assert rows["roles:permissions"].status == "EVIDENCED"
    assert "cite" in rows["roles:permissions"].note
    # nothing recorded and no cited source: a passing case never turns UNOBSERVED into verified
    assert rows["errors:GET /api/orders"].status == "UNOBSERVED"
    assert "cites no evidence" in rows["errors:GET /api/orders"].note


def test_a_cited_source_lets_a_category_row_be_verified(world):
    write_cases(world, [
        {"id": "c-roles", "check": "text", "locator": "#who", "equals": "clerk",
         "covers": ["roles:permissions"], "evidence": "owner's reply of 12 November"},
        {"id": "c-err", "check": "text", "locator": "#e", "equals": "x",
         "covers": ["errors:GET /api/orders"], "evidence": "API owner's error sample",
         "basis": "assumed"},
    ])  # fmt: skip
    keep_report(world, {"c-roles": "PASS", "c-err": "PASS"})
    rows = by_id(cov.build(world, APP))
    assert rows["roles:permissions"].status == "VERIFIED"
    assert "owner's reply" in " ".join(rows["roles:permissions"].evidence)
    assert (
        rows["errors:GET /api/orders"].status == "UNOBSERVED"
    )  # an assumed expectation is not evidence


def test_failing_blocked_skipped_and_not_run_assertions(world):
    write_cases(world, [
        {"id": "f", "check": "value", "locator": "#search", "equals": "a"},
        {"id": "b", "check": "text", "locator": "#x", "equals": "a",
         "covers": ["route:GET /api/orders"]},
        {"id": "s", "check": "text", "locator": "#y", "equals": "a", "required": False,
         "covers": ["table:orders"]},
        {"id": "n", "check": "text", "locator": "#z", "equals": "a",
         "covers": ["route:POST /api/orders"]},
    ])  # fmt: skip
    keep_report(world, {"f": "FAIL", "b": "BLOCKED", "s": "SKIPPED"})
    r = cov.build(world, APP)
    rows = by_id(r)
    assert rows["action:/orders:#search"].status == "BLOCKED"
    assert rows["action:/orders:#search"].assertion_result == "FAIL"
    assert rows["route:GET /api/orders"].status == "BLOCKED"
    assert rows["table:orders"].status == "EVIDENCED"
    assert "skipped" in rows["table:orders"].note
    assert rows["route:POST /api/orders"].status == "EVIDENCED"
    assert "not run in a current report" in rows["route:POST /api/orders"].note
    assert any("failing" in s for s in r.shortfall) and any("skipped" in s for s in r.shortfall)


def test_a_stale_report_gives_no_verification(world):
    write_cases(world, [{"id": "c", "check": "value", "locator": "#search", "equals": "a"}])
    keep_report(world, {"c": "PASS"})
    assert by_id(cov.build(world, APP))["action:/orders:#search"].status == "VERIFIED"
    (world / "evidence" / APP / REC / "dom" / "0002.html").write_text(
        "<html>changed</html>", encoding="utf-8"
    )
    r = cov.build(world, APP)
    row = by_id(r)["action:/orders:#search"]
    assert row.status == "EVIDENCED" and "not run in a current report" in row.note
    assert any("cannot be used" in n and "recording changed" in n for n in r.notes)


def test_exclusions_carry_their_declaration_and_unmatched_ones_stay_visible(world):
    write_cases(world, [], excluded=["#ghost", "banner styling"],
                unsupported=["drag and drop"], unobserved=["server-side validation"])  # fmt: skip
    (world / "evidence" / APP / REC / "scope.yaml").write_text(
        yaml.safe_dump({"excluded": [{"id": "table:guess", "reason": "legacy"}]}), encoding="utf-8"
    )
    rows = by_id(cov.build(world, APP))
    ghost = rows["action:/orders:#ghost"]
    assert ghost.status == "EXCLUDED" and "'#ghost'" in ghost.declaration
    assert "acceptance.yaml of rec1" in ghost.declaration
    assert (
        rows["table:guess"].status == "EXCLUDED" and "scope.yaml" in rows["table:guess"].declaration
    )
    assert rows["excluded:banner-styling"].status == "EXCLUDED"  # matched no row: still listed
    assert rows["unsupported:drag-and-drop"].status == "UNSUPPORTED"
    assert rows["unobserved:server-side-validation"].status == "UNOBSERVED"


def test_out_of_scope_items_of_the_brief_are_excluded_with_its_declaration(tmp_path, monkeypatch):
    monkeypatch.setenv("TWINLAB_STATE", str(tmp_path / "state"))
    model = _model()
    model["screens"].append(
        {
            "name": "admin",
            "path": "/admin",
            "basis": "observed",
            "from": ["har:rec"],
            "elements": [],
        }
    )  # no flow or bot needs it
    root = make_world(tmp_path, model)
    row = by_id(cov.build(root, APP))["screen:/admin"]
    assert row.status == "EXCLUDED" and "Out of scope" in row.declaration


def test_data_location_row_follows_the_persistence_finding(tmp_path, monkeypatch):
    monkeypatch.setenv("TWINLAB_STATE", str(tmp_path / "state"))
    model = _model()
    model["stack"]["data"] = {
        "kind": "browser",
        "persistence": "transient",
        "label": "browser (transient or unknown persistence)",
        "storage": [],
        "keys": [],
        "evidence": [],
    }
    root = make_world(tmp_path, model)
    row = by_id(cov.build(root, APP))["persistence:location"]
    assert row.status == "UNOBSERVED" and "transient or unknown" in row.label
    assert "not evidence of browser storage" in row.note


# --- the claim --------------------------------------------------------------------------------


def _row(rid, status, **kw):
    return cov.Row(id=rid, kind="x", category="screens", label=rid, status=status, **kw)


def test_the_claim_needs_every_required_row_verified_or_excluded():
    rows = [_row("a", "VERIFIED"), _row("b", "EXCLUDED"), _row("c", "VERIFIED")]
    ok, text, short = cov.claim_of(rows, [], ["PASS WITHIN DECLARED SCOPE"])
    assert ok and text.startswith(cov.CLAIM) and "2 required row(s) VERIFIED" in text
    assert "1 EXCLUDED" in text and "not a claim of complete equivalence" in text and short == []
    for bad in ("EVIDENCED", "ASSUMED", "UNOBSERVED", "UNSUPPORTED", "BLOCKED"):
        ok, text, short = cov.claim_of([*rows, _row("d", bad)], [], [])
        assert not ok and not text.startswith(cov.CLAIM)
        assert text.startswith("Not claimable") and bad in text and "3 VERIFIED" not in text
        assert short and "short by" in text


def test_skipped_failing_and_empty_never_claim():
    base = [_row("a", "VERIFIED", assertion_result="PASS")]
    assert cov.claim_of(base, [], ["PASS WITHIN DECLARED SCOPE"])[0]
    skipped = [*base, _row("b", "VERIFIED", assertion_result="SKIPPED")]
    ok, text, _ = cov.claim_of(skipped, [], [])
    assert not ok and "1 skipped" in text
    failing = [*base, _row("b", "VERIFIED", assertion_result="FAIL")]
    ok, text, _ = cov.claim_of(failing, [], [])
    assert not ok and "1 failing" in text
    assert not cov.claim_of([], [], [])[0]  # zero rows: nothing to claim
    only_excluded = [_row("a", "EXCLUDED")]
    assert not cov.claim_of(only_excluded, [], [])[0]  # nothing was verified
    ok, text, _ = cov.claim_of(base, [], ["FAIL"])
    assert not ok and "FAIL" in text  # a failing replay verdict blocks it


def test_the_claim_is_printed_for_a_fully_verified_small_app(tmp_path, monkeypatch):
    monkeypatch.setenv("TWINLAB_STATE", str(tmp_path / "state"))
    model = {
        "app": APP,
        "evidence": [
            {"id": "har:rec", "kind": "har", "complete": []},
            {"id": "flow:demo", "kind": "flow", "complete": []},
        ],
        "stack": {
            "frontend": {"framework": "react", "marker": "react-dom"},
            "db": {"engine": "postgres"},
            "data": {
                "kind": "server",
                "persistence": "server",
                "label": "server",
                "evidence": ["w"],
            },
        },
        "screens": [
            {
                "name": "orders",
                "path": "/orders",
                "basis": "observed",
                "from": ["har:rec", "flow:demo"],
            }
        ],
    }
    root = make_world(tmp_path, model, rules=[])
    cases = [
        {"id": "s", "check": "text", "locator": "#t", "equals": "a", "covers": ["screen:/orders"]},
        {
            "id": "loc",
            "check": "text",
            "locator": "#t",
            "equals": "a",
            "covers": ["persistence:location"],
        },
        {"id": "rel", "check": "count", "locator": "li", "equals": 1, "reload": True},
        {
            "id": "new",
            "check": "count",
            "locator": "li",
            "equals": 0,
            "fresh_context": True,
            "evidence": "owner: data is per user",
        },
        {
            "id": "roles",
            "check": "text",
            "locator": "#r",
            "equals": "a",
            "covers": ["roles:permissions"],
            "evidence": "owner reply",
        },
        {
            "id": "neg",
            "check": "text",
            "locator": "#n",
            "equals": "a",
            "covers": ["negative:unauthorised"],
            "evidence": "owner reply",
        },
    ]
    steps = root / "evidence" / APP / REC / "steps.jsonl"
    steps.write_text(
        steps.read_text(encoding="utf-8").replace('"drag"', '"click"'), encoding="utf-8"
    )  # nothing the tooling cannot replay
    write_cases(root, cases, excluded=["input boundaries", "validation error shown"])
    keep_report(root, {c["id"]: "PASS" for c in cases})
    r = cov.build(root, APP)
    bad = [(x.id, x.status) for x in r.rows if x.status not in ("VERIFIED", "EXCLUDED")]
    assert bad == []
    assert r.claim, r.claim_text
    assert r.claim_text.startswith(cov.CLAIM) and r.shortfall == []
    assert "Evidence requests" in r.markdown() and cov.CLAIM in r.markdown()


# --- evidence requests ---------------------------------------------------------------------------


def test_evidence_requests_are_plain_words_for_the_owner(world):
    r = cov.build(world, APP)
    owner = [q.text for q in r.requests if q.audience == "owner"]
    text = "\n".join(owner)
    assert "Record a failed sign-in" in text
    assert "Which roles may view, create, edit or delete" in text
    assert "error body" in text and "GET /api/orders" in text
    assert "unknown id" in text
    assert "limits (required, length, range, format)" in text
    assert "Does the data survive a new session" in text
    assert "Record a reload of the page" not in text  # a reload was recorded: it is evidenced
    assert "at once" in text  # the concurrency rule
    assert any(q.audience == "lab" and "acceptance cases" in q.text for q in r.requests)
    assert len({q.text for q in r.requests}) == len(r.requests)  # no duplicates
    assert "Ask the application owner:" in r.markdown()


def test_no_request_for_what_is_verified_or_excluded():
    rows = [_row("roles:permissions", "VERIFIED"), _row("negative:unauthorised", "EXCLUDED")]
    assert cov.requests_for(rows) == []


# --- freshness and files --------------------------------------------------------------------------


def test_the_kept_matrix_is_stale_when_the_app_the_evidence_or_the_cases_change(world, tmp_path):
    r = cov.build(world, APP)
    path = cov.save(world, r)
    assert path.name == f"{APP}.json" and path.with_suffix(".md").is_file()
    saved = cov.load_saved(world, APP)
    assert saved.claim_text == r.claim_text
    assert cov.freshness(world, APP, saved) == ("current", [])

    (world / "apps" / APP / "BRIEF.md").write_text("# changed brief\n", encoding="utf-8")
    state, why = cov.freshness(world, APP, saved)
    assert state == "stale" and any("brief" in w for w in why)
    cov.save(world, cov.build(world, APP))
    saved = cov.load_saved(world, APP)

    write_cases(world, [{"id": "c", "check": "value", "locator": "#search", "equals": "a"}])
    state, why = cov.freshness(world, APP, saved)
    assert state == "stale" and any(
        "declared cases changed" in w and "acceptance.yaml" in w for w in why
    )
    cov.save(world, cov.build(world, APP))
    saved = cov.load_saved(world, APP)

    (world / "evidence" / APP / REC / "dom" / "0002.html").write_text("<p>x</p>", encoding="utf-8")
    state, why = cov.freshness(world, APP, saved)
    assert state == "stale" and any("evidence changed" in w for w in why)
    cov.save(world, cov.build(world, APP))
    saved = cov.load_saved(world, APP)

    keep_report(world, {"c": "PASS"})
    state, why = cov.freshness(world, APP, saved)
    assert state == "stale" and any("report" in w for w in why)


def test_a_missing_model_is_an_error_not_an_empty_matrix(tmp_path, monkeypatch):
    monkeypatch.setenv("TWINLAB_STATE", str(tmp_path / "state"))
    (tmp_path / "apps" / "x").mkdir(parents=True)
    with pytest.raises(cov.CoverageError, match="model.yaml"):
        cov.build(tmp_path, "x")


def test_the_cli_prints_the_shortfall_and_exits_one(world, monkeypatch):
    monkeypatch.setenv("LABCTL_ROOT", str(world))
    result = CliRunner().invoke(cli_app, ["coverage", APP])
    assert result.exit_code == 1
    assert "Not claimable" in result.output and "Evidence requests" in result.output
    assert (Path(cov._state(world)) / "coverage" / f"{APP}.json").is_file()
    as_json = CliRunner().invoke(cli_app, ["coverage", APP, "--json", "--no-save"])
    data = json.loads(as_json.output)
    assert data["claim"] is False and data["counts"]["VERIFIED"] == 0
    missing = CliRunner().invoke(cli_app, ["coverage", "nope"])
    assert missing.exit_code == 2


def test_nothing_recorded_or_typed_appears_in_the_matrix(world):
    (world / "evidence" / APP / REC / "steps.jsonl").write_text(
        json.dumps({"seq": 1, "url": "https://x.test/orders", "action": "type",
                    "selector": "#search", "value": "SECRET-TYPED-VALUE"}) + "\n",
        encoding="utf-8")  # fmt: skip
    r = cov.build(world, APP)
    assert "SECRET-TYPED-VALUE" not in r.model_dump_json() + r.markdown()
