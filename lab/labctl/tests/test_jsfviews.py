"""JSF views: the screen a step was on when the address lags (a postback answered with the next
view), the partial responses of the HAR, and the states inside one view (sub-pages swapped by
ajax, tabs, wizard steps, accordion panels, dialogs, a dialog-framework page, a data table's
page, sort, rows, selection and expansion). No browser: the snapshots are written here."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from labctl import jsfviews, stepfacts
from labctl.behaviour import dom as domlib

VS = '<input type="hidden" name="javax.faces.ViewState" value="1:2">'


def page(view: str, body: str, pf_view: str | None = None) -> str:
    pf = (f"<script>if(window.PrimeFaces){{PrimeFaces.settings.viewId='{pf_view}';}}</script>"
          if pf_view else "")  # fmt: skip
    return (
        f'<html><head>{pf}</head><body><form id="main" method="post" '
        f'action="{view};jsessionid=node01abc.node0">{VS}{body}</form></body></html>'
    )


# --- which view a snapshot shows ------------------------------------------------------------------


def test_the_view_is_where_the_jsf_forms_post_not_the_address():
    assert jsfviews.view_id(page("/workspace.xhtml", "<h2>Cases</h2>")) == "/workspace.xhtml"
    # a frame's document (the recorder writes it in a <tl-frame>) is not the page's view
    framed = page(
        "/workspace.xhtml",
        '<iframe></iframe><tl-frame data-tl-frame="iframe">'
        + page("/picker.xhtml", "")
        + page("/picker.xhtml", "")
        + "</tl-frame>",
    )
    assert jsfviews.view_id(framed) == "/workspace.xhtml"
    # without a form, PrimeFaces' own view id
    bare = (
        f"<html><head><script>PrimeFaces.settings.viewId='/case.xhtml';</script></head>{VS}</html>"
    )
    assert jsfviews.view_id(bare) == "/case.xhtml"
    assert jsfviews.view_id("<html><form method='post' action='/x.xhtml'></form></html>") is None
    assert jsfviews.view_id(None) is None


def test_a_forward_keeps_the_address_and_the_steps_get_the_view():
    t0 = "2026-11-18T10:00:0{}Z"
    steps = [
        {"seq": 1, "action": "goto", "url": "http://h/login.xhtml", "tab": "t1", "ts": t0.format(1)},
        {"seq": 2, "action": "click", "url": "http://h/login.xhtml;jsessionid=x", "tab": "t1",
         "selector": "button#loginForm\\:signIn", "ts": t0.format(2)},
        {"seq": 3, "action": "goto", "url": "http://h/login.xhtml", "tab": "t1",
         "caused_by": 2, "ts": t0.format(3)},
        {"seq": 4, "action": "click", "url": "http://h/login.xhtml", "tab": "t1",
         "selector": "a#main\\:navNew", "ts": t0.format(4)},
        {"seq": 5, "action": "goto", "url": "http://h/workspace.xhtml", "tab": "t1",
         "caused_by": 4, "ts": t0.format(5)},
    ]  # fmt: skip
    doms = {1: page("/login.xhtml", ""), 3: page("/workspace.xhtml", ""),
            5: page("/case.xhtml", "")}  # fmt: skip
    views = jsfviews.step_views(steps, doms)
    # step 4 has no snapshot: it was on the view the last page at that address showed
    assert views == {1: "/login.xhtml", 2: "/login.xhtml", 3: "/workspace.xhtml",
                     4: "/workspace.xhtml", 5: "/case.xhtml"}  # fmt: skip
    assert jsfviews.address_lag(steps, views) == [
        {"step": 3, "address": "/login.xhtml", "view": "/workspace.xhtml"},
        {"step": 5, "address": "/workspace.xhtml", "view": "/case.xhtml"}]  # fmt: skip


def test_step_facts_put_each_step_on_its_view(tmp_path):
    """The step list's screens and the page that followed come from the view, so signing in
    leads to the workspace although the address still says /login.xhtml."""
    (tmp_path / "dom").mkdir()
    rows = [(1, "goto", "/login.xhtml", None, "/login.xhtml"),
            (2, "click", "/login.xhtml", "button#loginForm\\:signIn", None),
            (3, "goto", "/login.xhtml", None, "/workspace.xhtml")]  # fmt: skip
    with (tmp_path / "steps.jsonl").open("w", encoding="utf-8") as f:
        for seq, action, path, sel, view in rows:
            step = {"seq": seq, "action": action, "url": f"http://h{path}",
                    "ts": f"2026-11-18T10:00:0{seq}Z", "tab": "t1"}  # fmt: skip
            if sel:
                step["selector"] = sel
            if seq == 3:
                step["caused_by"] = 2
            if view:
                (tmp_path / "dom" / f"{seq}.html").write_text(page(view, "<h1>x</h1>"), "utf-8")
                step["dom"] = f"dom/{seq}.html"
            f.write(json.dumps(step) + "\n")
    (tmp_path / "recording.har").write_text(json.dumps({"log": {"entries": []}}), "utf-8")
    facts = stepfacts.facts(tmp_path)
    by = {s["n"]: s for s in facts.steps}
    assert by[3]["screen"] == "/login.xhtml" and by[3]["then"] == "/workspace.xhtml"
    click = next(e for e in facts.effects if e["n"] == 2)
    assert click["screen"] == "/login.xhtml" and click["then"] == "/workspace.xhtml"


# --- partial responses ----------------------------------------------------------------------------


PARTIAL = """<?xml version='1.0' encoding='utf-8'?>
<partial-response id="j_id1"><changes><update id="main:content"><![CDATA[<div id="main:content">
<h2 id="subTitle">Cases</h2><div id="main:cases"></div><div id="main:actions"></div></div>]]></update>
<update id="main:growl"><![CDATA[<span id="main:growl"></span>]]></update>
<update id="j_id1:javax.faces.ViewState:0"><![CDATA[1:2]]></update>
<eval><![CDATA[PrimeFaces.openDialog({url:'\\/picker.xhtml',pfdlgcid:'0c00'});PF('noteDlg').show();]]></eval>
<extension ln="primefaces" type="args">{"validationFailed":true}</extension>
</changes></partial-response>"""


def test_a_partial_response_says_what_it_rerendered_and_ran():
    done = jsfviews.parse_partial(PARTIAL)
    assert [u["id"] for u in done["updates"]] == ["main:content", "main:growl"]
    assert done["updates"][0] == {"id": "main:content", "heading": True, "components": 4}
    assert done["ignored"] == ["j_id1:javax.faces.ViewState:0"]
    assert done["evals"] == [["PF('...').show", "PrimeFaces.openDialog"]]
    assert done["validation_failed"] is True and done["callback_args"] == ["validationFailed"]
    red = jsfviews.parse_partial(
        '<partial-response><redirect url="/login.xhtml?x=1"/></partial-response>'
    )
    assert red["redirect"] == "/login.xhtml"
    err = jsfviews.parse_partial("<partial-response><error><error-name>java.lang.NullPointerException"
                                 "</error-name><error-message>m</error-message></error></partial-response>")  # fmt: skip
    assert err["error"] == "java.lang.NullPointerException"
    assert jsfviews.parse_partial("<html></html>") is None


def test_partial_requests_go_to_the_step_before_them_with_ids_only():
    t = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)
    iso = lambda d: d.isoformat().replace("+00:00", "Z")  # noqa: E731
    steps = [{"seq": 4, "ts": iso(t)}, {"seq": 5, "ts": iso(t + timedelta(seconds=5))}]
    har_doc = {"log": {"entries": [{
        "startedDateTime": iso(t + timedelta(seconds=1)),
        "request": {"method": "POST", "url": "http://h/workspace.xhtml",
                    "postData": {"text": "javax.faces.partial.ajax=true&javax.faces.source=main%3AnavNew"
                                 "&javax.faces.partial.execute=main%3AnavNew&javax.faces.partial."
                                 "render=main%3Acontent&main%3Atitle=Typed+secret&main=main"}},
        "response": {"status": 200, "content": {"mimeType": "text/xml", "text": PARTIAL}}}]}}  # fmt: skip
    got = jsfviews.partials(har_doc, steps)
    assert list(got) == [4]
    row = got[4][0]
    assert row["request"] == "POST /workspace.xhtml 200" and row["source"] == "main:navNew"
    assert row["execute"] == ["main:navNew"] and row["render"] == ["main:content"]
    assert "Typed secret" not in json.dumps(got)
    assert jsfviews.regions_of(got) == {"main:content"}


# --- states inside a view -------------------------------------------------------------------------


WORKSPACE = """<div id="main:content"><h2 id="subTitle">{title}</h2>{body}</div>
<div id="main:noteDlg" class="ui-dialog ui-widget" role{dlg_role} aria-hidden="{dlg_hidden}">
<div class="ui-dialog-titlebar"><span id="main:noteDlg_title" class="ui-dialog-title">Quick note</span></div></div>
<div id="main:noteDlg_modal" class="ui-widget-overlay"></div>
<div id="main:fw_dlg" class="ui-dialog" role="dialog" aria-hidden="false"><span id="main:fw_dlg_title"
 class="ui-dialog-title">Pick an owner</span><iframe src="/picker.xhtml?pfdlgcid=0c00"></iframe></div>"""
CASES = """<div id="main:cases" class="ui-datatable"><table><thead><tr>
<th id="main:cases:colNumber" aria-sort="{sort}"><span class="ui-column-title">Number</span></th></tr></thead>
<tbody id="main:cases_data"><tr data-ri="0" role="row" aria-selected="{sel}"><td>C-2001</td></tr>{exp}</tbody></table>
<div class="ui-paginator"><a class="ui-paginator-page ui-state-active" aria-label="Page {page}">{page}</a>
<a class="ui-paginator-page" aria-label="Page 9">9</a></div></div><div id="main:actions"></div>"""
DETAIL = """<div id="main:tabs" class="ui-tabs"><ul role="tablist"><li role="tab" aria-selected="{a}">
<a href="#main:tabs:summary">Summary</a></li><li role="tab" aria-selected="{b}"><a>Notes</a></li></ul></div>
<div id="main:acc" class="ui-accordion" role="tablist"><div id="main:acc:x_header" role="tab"
 aria-expanded="{acc}">By owner</div><div id="main:acc:x" role="tabpanel"></div></div>
<div id="main:wiz" class="ui-wizard"><ul><li class="ui-wizard-step-title">Basics</li>
<li class="ui-wizard-step-title ui-state-highlight">Owner</li></ul></div>"""


def tree(html: str):
    t = domlib.parse_html(html)
    return t, t  # the "sanitised" tree is the same here: no data in these snapshots


def test_every_primefaces_part_of_a_view_is_read_with_its_state():
    html = WORKSPACE.format(title="Cases", dlg_role="", dlg_hidden="true",
                            body=CASES.format(sort="descending", sel="true", page=2,
                                              exp='<tr class="ui-expanded-row-content"></tr>'))  # fmt: skip
    st = jsfviews.states(*tree(html), regions={"main:content"})
    # a PrimeFaces dialog is hidden by aria-hidden (its role emptied), not by an inline style
    assert st["dialog div#main:noteDlg"]["value"] is False
    assert st["dialog div#main:noteDlg"]["title"] == "Quick note"
    assert "dialog div#main:noteDlg_modal" not in st
    fw = st["dialog div#main:fw_dlg"]
    assert fw["kind"] == "dialog-framework page (iframe)" and fw["shows"] == "/picker.xhtml"
    assert fw["value"] is True and fw["title"] == "Pick an owner"
    region = st["region div#main:content"]
    assert region["title"] == "Cases"
    assert region["value"] == ("main:actions", "main:cases", "subTitle")
    assert st["table-page div#main:cases"]["value"] == "Page 2"
    assert st["table-sort div#main:cases"]["value"] == "sorted by Number descending"
    assert st["table-rows div#main:cases"]["value"] == 2
    assert st["table-expand div#main:cases"]["value"] is True
    assert st["table-select div#main:cases"]["value"] is True
    st = jsfviews.states(*tree(DETAIL.format(a="false", b="true", acc="true")), regions=set())
    assert (st["tabs div#main:tabs"]["value"], st["tabs div#main:tabs"]["title"]) == (1, "Notes")
    assert st["panel div#main:acc 0"]["value"] is True
    assert st["panel div#main:acc 0"]["kind"] == "accordion panel"
    assert (st["wizard div#main:wiz"]["title"], st["wizard div#main:wiz"]["step"]) == (
        "Owner",
        "2 of 2",
    )


class FakePages:
    def __init__(self, snaps):
        self.snaps = snaps

    def of(self, screen):
        return [x for x in self.snaps if x[2] == screen]


def test_states_get_the_step_and_element_that_opened_and_closed_them():
    def cases(**k) -> str:
        return page(
            "/workspace.xhtml",
            WORKSPACE.format(
                title=k.get("title", "Cases"),
                dlg_role="",
                dlg_hidden=k.get("hidden", "true"),
                body=k.get("body") or CASES.format(sort="other", sel="false", page=1, exp=""),
            ),
        )

    snaps_html = {
        1: cases(),
        2: cases(body=CASES.format(sort="other", sel="false", page=2, exp="")),
        3: cases(hidden="false", body=CASES.format(sort="other", sel="false", page=2, exp="")),
        4: cases(body=CASES.format(sort="other", sel="false", page=2, exp="")),
        5: cases(title="New case", body='<div id="main:wiz" class="ui-wizard"></div>'),
    }  # fmt: skip
    raw = [{"seq": 1, "action": "goto", "url": "http://h/login.xhtml"}]
    raw += [{"seq": n, "action": "click", "url": "http://h/login.xhtml"} for n in (2, 3, 4, 5)]
    facts = {1: {"n": 1, "action": "goto"},
             2: {"n": 2, "action": "click", "selector": "a.ui-paginator-next"},
             3: {"n": 3, "action": "click", "selector": "button#main\\:quickNote"},
             4: {"n": 4, "action": "click", "selector": "button#main\\:saveNote"},
             5: {"n": 5, "action": "click", "selector": "a#main\\:navNew"}}  # fmt: skip
    t0 = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)
    for s in raw:
        s["ts"] = (t0 + timedelta(seconds=s["seq"])).isoformat().replace("+00:00", "Z")
    part = SimpleNamespace(raw=raw, doms=snaps_html, path=None)
    ctx = SimpleNamespace(part=part, known=set(), vocab=set(), root_step=lambda n: facts.get(n))
    jsfviews._CACHE.clear()
    calls = {5: [{"source": "main:navNew", "updates": [{"id": "main:content", "heading": True,
                                                        "components": 3}]}]}  # fmt: skip
    orig = jsfviews.calls_of
    jsfviews.calls_of = lambda p: calls
    try:
        snaps = [(n, "after", "/workspace.xhtml", *tree(h)) for n, h in snaps_html.items()]
        rows = jsfviews.subviews(ctx, "/workspace.xhtml", FakePages(snaps))
    finally:
        jsfviews.calls_of = orig
    by = {(r["kind"], r["title"]): r for r in rows}
    first = by[("sub-page (ajax region)", "Cases")]
    assert first["opened_by"] == [] and first["first_seen"] == "when the page loads"
    new = by[("sub-page (ajax region)", "New case")]
    assert new["opened_by"] == [{"step": 5, "action": "click", "selector": "a#main\\:navNew"}]
    assert new["rerendered"] == ["main:content"]
    assert by[("table page", "Page 2")]["opened_by"][0]["step"] == 2
    note = by[("dialog", "Quick note")]
    assert [w["step"] for w in note["opened_by"]] == [3]
    assert [w["step"] for w in note["closed_by"]] == [4]
    assert all("_html" in r for r in rows)  # each state's own snapshot, sanitised
    lines = jsfviews.doc_sections(rows)
    assert '### sub-page (ajax region): "New case"' in lines
    assert any(x.startswith("- Opened by: click `a#main\\:navNew` (step 5)") for x in lines)


def test_jsf_states_replace_the_generic_dialogs_and_lend_the_frame_its_opener():
    generic = [
        {"kind": "dialog", "selector": "div#main:noteDlg", "opened_by": []},
        {"kind": "tab panel", "selector": "div#main:tabs:summary", "opened_by": []},
    ]
    frame = {
        "kind": "iframe (a page within the page)",
        "selector": "iframe",
        "shows": "/picker.xhtml",
    }
    jsf = [{"kind": "dialog-framework page (iframe)", "state_of": "/w", "shows": "/picker.xhtml",
            "opened_by": [{"step": 3, "action": "click", "selector": "b"}], "closed_by": []}]  # fmt: skip
    out = jsfviews.merge(generic, jsf, [frame])
    assert [v["kind"] for v in out] == ["dialog-framework page (iframe)",
                                        "iframe (a page within the page)"]  # fmt: skip
    assert out[1]["opened_by"] == jsf[0]["opened_by"]
    assert jsfviews.merge(generic, [], [frame]) == [*generic, frame]


# --- PrimeFaces widgets -----------------------------------------------------------------------------


def test_widgets_are_named_by_type_variable_and_client_id_never_by_value(tmp_path):
    doc_html = ('<script id="main:cases_s">$(function(){PrimeFaces.cw("DataTable","casesTable",'
                '{id:"main:cases",selectionMode:"single",paginator:{id:["main:cases_paginator"],'
                'rows:5},behaviors:{rowSelect:function(e){}}});});</script>')  # fmt: skip
    upd = ('<partial-response><changes><update id="main:wiz"><![CDATA[<script>PrimeFaces.cw('
           '"Wizard","widget_main_wiz",{id:"main:wiz",steps:["basics"]});</script>]]></update>'
           '</changes></partial-response>')  # fmt: skip
    har_doc = {"log": {"entries": [
        {"request": {"method": "GET", "url": "http://h/login.xhtml"},
         "response": {"status": 200, "content": {"mimeType": "text/html", "text": doc_html}}},
        {"request": {"method": "POST", "url": "http://h/workspace.xhtml"},
         "response": {"status": 200, "content": {"mimeType": "text/xml", "text": upd}}}]}}  # fmt: skip
    (tmp_path / "recording.har").write_text(json.dumps(har_doc), "utf-8")
    got = jsfviews.widgets([tmp_path])
    by = {w["type"]: w for w in got}
    assert by["DataTable"] == {"type": "DataTable", "widget_var": "casesTable", "id": "main:cases",
                               "options": ["selectionMode", "paginator", "behaviors"],
                               "pages": ["/login.xhtml"], "via": ["page"]}  # fmt: skip
    assert by["Wizard"]["via"] == ["partial response"] and by["Wizard"]["options"] == ["steps"]
