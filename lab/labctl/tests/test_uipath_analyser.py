from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from labctl import demand as demand_report
from labctl.analyse import sql, uipath, vb
from labctl.cli import app
from labctl.models import Automation, Gap

ROOT = Path(__file__).resolve().parents[3]
SAMPLE = ROOT / "automations" / "uipath-case-update"
BINDINGS = {"in_Url": "{{demo.web}}", "in_Api": "{{demo.api}}", "in_Db": "{{demo.db}}"}
runner = CliRunner()

HEAD = (
    '<Activity xmlns="http://schemas.microsoft.com/netfx/2009/xaml/activities" '
    'xmlns:ui="http://schemas.uipath.com/workflow/activities" '
    'xmlns:uix="http://schemas.uipath.com/workflow/activities/uix" '
    'xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"><Sequence DisplayName="Main">'
)
TAIL = "</Sequence></Activity>"


def project(tmp_path, body, name="Main.xaml", head=""):
    (tmp_path / name).write_text(head + HEAD + body + TAIL, encoding="utf-8")
    return tmp_path


def browser(inner, url='[in_Url + "/home"]'):
    return (
        f"<ui:OpenBrowser Url='{url}' DisplayName=\"Open\"><ui:OpenBrowser.Body>"
        f"<ActivityAction><Sequence>{inner}</Sequence></ActivityAction>"
        "</ui:OpenBrowser.Body></ui:OpenBrowser>"
    )


def target(activity, selector, attrs=""):
    escaped = selector.replace("<", "&lt;").replace(">", "&gt;")
    return (
        f'<ui:{activity} DisplayName="{activity} it" {attrs}><ui:{activity}.Target>'
        f'<ui:Target Selector="{escaped}" /></ui:{activity}.Target></ui:{activity}>'
    )


def uses(result, path):
    web = next(t for t in result.touches if t["via"] == "web")
    return next(s for s in web["screens"] if s["path"] == path)["uses"]


# --- the sample project written against the reference app (T-03 DoD) ---------------------------


@pytest.fixture(scope="module")
def sample():
    data = yaml.safe_load((SAMPLE / "automation.yaml").read_text(encoding="utf-8"))
    return uipath.analyse(SAMPLE / "project", "Main.xaml", data["bindings"])


def test_sample_yields_every_screen_and_selector_it_uses(sample):
    web = next(t for t in sample.touches if t["via"] == "web")
    assert web["app"] == "showcase"
    found = {s["path"]: [(u["action"], u["selector"]) for u in s["uses"]] for s in web["screens"]}
    assert found == {
        "/login": [
            ("type", "input#username"),
            ("type", "input#password"),
            ("click", "button#btnLogin"),
        ],
        "/cases": [
            ("type", "input#caseNo"),
            ("click", "button#btnSearch"),
            ("wait", "table#results"),
            ("read", "table#results td.status"),
        ],
        "/cases/{in_CaseNo}": [
            ("select", "select#category"),
            ("select", "select#subcategory"),
            ("select", "select#caseStatus"),
            ("click", "button#btnSave"),
            ("read", "#message"),
        ],
    }


def test_sample_yields_the_routes_called_from_an_invoked_workflow(sample):
    api = next(t for t in sample.touches if t["via"] == "api")
    assert api["routes"] == [
        {"method": "POST", "path": "/api/login", "sends": ["username", "password"], "reads": ["token"]},
        {"method": "POST", "path": "/api/cases/{in_CaseNo}/notes", "sends": ["text"]},
    ]  # fmt: skip
    assert sample.files == ["Main.xaml", "AddNoteViaApi.xaml"]


def test_sample_yields_the_tables_in_its_sql(sample):
    db = next(t for t in sample.touches if t["via"] == "db")
    assert {t["table"]: t["access"] for t in db["tables"]} == {
        "case_audit": "read",
        "cases": "read",
    }
    audit = next(t for t in db["tables"] if t["table"] == "case_audit")
    assert set(audit["columns"]) == {"event", "actor", "created_ts", "case_id"}


def test_sample_has_no_gaps_and_records_the_carried_variable_not_a_value(sample):
    assert sample.gaps == []
    assert uses(sample, "/login")[0]["field"] == "in_User"
    status = next(u for u in uses(sample, "/cases/{in_CaseNo}") if "caseStatus" in u["selector"])
    assert "field" not in status  # the literal "RESOLVED" is data and is not kept


def test_registered_sample_file_matches_a_fresh_analysis(sample):
    saved = Automation.model_validate(
        yaml.safe_load((SAMPLE / "automation.yaml").read_text(encoding="utf-8"))
    )
    fresh = Automation.model_validate(
        {**saved.model_dump(mode="json", exclude_none=True), "touches": sample.touches}
    )  # both through the model, so defaults (`reads: []`) compare equal
    assert saved.touches == fresh.touches


# --- gaps and refusals ---------------------------------------------------------------------------


def test_unsupported_activity_is_a_gap_and_its_children_are_still_read(tmp_path):
    body = browser(
        '<ui:ExcelApplicationScope DisplayName="Excel"><ui:ExcelApplicationScope.Body>'
        "<ActivityAction><Sequence>"
        + target("Click", "<webctrl id='inside' />")
        + "</Sequence></ActivityAction></ui:ExcelApplicationScope.Body></ui:ExcelApplicationScope>"
    )
    result = uipath.analyse(project(tmp_path, body), bindings=BINDINGS)
    assert [g.where for g in result.gaps] == ["Main.xaml: ExcelApplicationScope 'Excel'"]
    assert "not interpreted" in result.gaps[0].what
    assert uses(result, "/home")[0]["selector"] == "#inside"


def test_modern_design_activity_is_named_as_such(tmp_path):
    result = uipath.analyse(
        project(tmp_path, '<uix:NTypeInto DisplayName="Type" Text="[x]" />'), bindings=BINDINGS
    )
    assert "modern-design activity NTypeInto" in result.gaps[0].what


def test_file_with_a_dtd_is_rejected(tmp_path):
    head = '<?xml version="1.0"?><!DOCTYPE a [<!ENTITY e "boom">]>'
    with pytest.raises(uipath.AnalysisError, match="DTD"):
        uipath.analyse(project(tmp_path, "", head=head), bindings=BINDINGS)


def test_utf16_file_with_a_dtd_is_rejected(tmp_path):
    text = '<?xml version="1.0" encoding="utf-16"?><!DOCTYPE a [<!ENTITY e "x">]>' + HEAD + TAIL
    (tmp_path / "Main.xaml").write_bytes(text.encode("utf-16"))
    with pytest.raises(uipath.AnalysisError, match="DTD"):
        uipath.analyse(tmp_path, bindings=BINDINGS)


def test_invoked_workflow_outside_the_project_is_refused(tmp_path):
    inner = tmp_path / "proj"
    inner.mkdir()
    (tmp_path / "Secret.xaml").write_text(HEAD + TAIL, encoding="utf-8")
    body = '<ui:InvokeWorkflowFile WorkflowFileName="..\\Secret.xaml" />'
    with pytest.raises(uipath.AnalysisError, match="escapes"):
        uipath.analyse(project(inner, body), bindings=BINDINGS)


def test_missing_invoked_workflow_is_a_gap(tmp_path):
    body = '<ui:InvokeWorkflowFile DisplayName="Call" WorkflowFileName="Gone.xaml" />'
    result = uipath.analyse(project(tmp_path, body), bindings=BINDINGS)
    assert "Gone.xaml is not in the project folder" in result.gaps[0].what


def test_hard_coded_address_is_a_gap(tmp_path):
    body = browser(target("Click", "<webctrl id='go' />"), url="https://intranet.example/app")
    result = uipath.analyse(project(tmp_path, body), bindings=BINDINGS)
    assert any("hard-coded" in g.what for g in result.gaps)
    assert result.touches == []


def test_text_only_selector_is_kept_and_flagged(tmp_path):
    body = browser(target("Click", "<webctrl aaname='Submit' tag='BUTTON' />"))
    result = uipath.analyse(project(tmp_path, body), bindings=BINDINGS)
    assert uses(result, "/home")[0]["selector"].startswith("uipath:<webctrl aaname")
    assert "cannot be checked as CSS" in result.gaps[0].what


def test_sql_built_at_run_time_is_a_gap(tmp_path):
    body = '<ui:ExecuteQuery ConnectionString="[in_Db]" Sql="[&quot;SELECT * FROM &quot; + t]" />'
    result = uipath.analyse(project(tmp_path, body), bindings=BINDINGS)
    assert "built at run time" in result.gaps[0].what


def test_disabled_activities_are_ignored(tmp_path):
    body = browser("<ui:CommentOut>" + target("Click", "<webctrl id='old' />") + "</ui:CommentOut>")
    result = uipath.analyse(project(tmp_path, body), bindings=BINDINGS)
    assert result.touches == [] and result.gaps == []


def test_click_followed_by_more_elements_leaves_a_note_not_a_gap(tmp_path):
    body = browser(
        target("Click", "<webctrl id='next' />") + target("GetText", "<webctrl id='x' />")
    )
    result = uipath.analyse(project(tmp_path, body), bindings=BINDINGS)
    assert result.gaps == []
    assert result.notes[0].where == "Main.xaml: Click 'Click it'"


def test_address_built_through_an_assigned_variable_is_followed(tmp_path):
    body = (
        "<Assign><Assign.To><OutArgument>[u]</OutArgument></Assign.To><Assign.Value>"
        '<InArgument>[in_Api + "/v1/items/" + itemId + "?full=1"]</InArgument></Assign.Value>'
        '</Assign><ui:HttpClient Method="GET" EndPoint="[u]" />'
    )
    result = uipath.analyse(project(tmp_path, body), bindings=BINDINGS)
    assert result.touches == [
        {"app": "demo", "via": "api", "routes": [{"method": "GET", "path": "/v1/items/{itemId}"}]}
    ]


# --- helpers -------------------------------------------------------------------------------------


def test_vb_parse_splits_literals_variables_and_the_rest():
    parts = vb.parse('[in_Url & "/a""b/" + id.ToString + Config("x").ToString]')
    assert [(p.kind, p.value) for p in parts] == [
        ("var", "in_Url"),
        ("lit", '/a"b/'),
        ("var", "id"),
        ("expr", 'Config("x").ToString'),
    ]
    assert vb.parse("plain text") == [vb.Part("lit", "plain text")]


@pytest.mark.parametrize(
    ("selector", "css"),
    [
        ("<html title='App' /><webctrl id='a' tag='INPUT' />", "input#a"),
        ("<webctrl tag='TD' class='status big' />", "td.status.big"),
        ("<webctrl id='row-*' />", "[id^='row-']"),
        ("<webctrl name='q' tag='INPUT' type='text' />", "input[name='q'][type='text']"),
        ("<webctrl css-selector='ul#notes &gt; li' />".replace("&gt;", ">"), "ul#notes > li"),
        ("<webctrl parentid='form1' tag='BUTTON' />", "#form1 button"),
        ("<webctrl aaname='Save' />", None),
        ("<webctrl id='a?c' />", None),
    ],
)
def test_selector_to_css(selector, css):
    assert uipath.to_css(selector) == css


def test_sql_scan_reads_and_writes():
    found, problem = sql.tables(
        "INSERT INTO dbo.[Case Note] (case_id) SELECT c.id FROM cases AS c WHERE c.status = 'FROM x'"
    )
    assert problem is None
    assert {t["table"]: t["access"] for t in found} == {"cases": "read", "Case Note": "write"}
    assert sql.tables("UPDATE cases SET status = 'X' WHERE id = 1")[0][0]["access"] == "write"
    assert sql.tables("DELETE FROM case_note WHERE id = 1")[0] == [
        {"table": "case_note", "columns": [], "access": "write"}
    ]
    assert sql.tables("SELECT case_no, title FROM cases")[0][0]["columns"] == ["case_no", "title"]
    assert "stored procedure" in sql.tables("EXEC dbo.CloseCase @id")[1]


# --- demand report (T-04 DoD) ----------------------------------------------------------------------


def _auto(auto_id, selector, table_access="read"):
    return Automation.model_validate(
        {
            "id": auto_id,
            "name": auto_id,
            "type": "python",
            "surface": "combo",
            "source": {"path": "."},
            "touches": [
                {
                    "app": "demo",
                    "via": "web",
                    "screens": [
                        {
                            "name": "cases",
                            "path": "/cases",
                            "uses": [
                                {"action": "type", "selector": "#caseNo"},
                                {"action": "click", "selector": selector},
                            ],
                        }
                    ],
                },
                {"app": "demo", "via": "api", "routes": [{"method": "GET", "path": "/api/x"}]},
                {
                    "app": "demo",
                    "via": "db",
                    "tables": [
                        {"table": "cases", "columns": [auto_id[-1]], "access": table_access}
                    ],
                },
                {"app": "other", "via": "api", "routes": [{"method": "GET", "path": "/elsewhere"}]},
            ],
        }
    )


def test_demand_lists_a_shared_selector_once_with_both_automations():
    report = demand_report.demand(
        "demo", [_auto("bot-a", "#go"), _auto("bot-b", "#other", "write")]
    )
    elements = report["screens"][0]["elements"]
    shared = [e for e in elements if e["selector"] == "#caseNo"]
    assert len(shared) == 1
    assert shared[0]["automations"] == ["bot-a", "bot-b"]
    assert [e["automations"] for e in elements if e["selector"] == "#go"] == [["bot-a"]]
    assert report["routes"] == [
        {"method": "GET", "path": "/api/x", "automations": ["bot-a", "bot-b"]}
    ]
    assert report["tables"][0]["columns"] == ["a", "b"]
    assert report["tables"][0]["access"] == ["read", "write"]
    assert "/elsewhere" not in demand_report.render(report)


def test_demand_for_an_app_nobody_touches_says_so():
    report = demand_report.demand("ghost", [_auto("bot-a", "#go")])
    assert report["needed_by"] == []
    assert "No registered automation" in demand_report.render(report)


def test_demand_flags_automations_with_open_gaps():
    auto = _auto("bot-a", "#go")
    auto.analysis.gaps.append(Gap(where="x", what="y"))
    auto = Automation.model_validate(auto.model_dump(mode="json"))
    assert "INCOMPLETE: bot-a has 1 unresolved gap(s)" in demand_report.render(
        demand_report.demand("demo", [auto])
    )


# --- command line ------------------------------------------------------------------------------


def _repo(tmp_path, reviewed_by=None):
    for folder in ("automations/bot/project", "apps", "lab"):
        (tmp_path / folder).mkdir(parents=True)
    project(tmp_path / "automations/bot/project", browser(target("Click", "<webctrl id='go' />")))
    data = {
        "id": "bot",
        "name": "Bot",
        "type": "uipath",
        "surface": "frontend",
        "source": {"path": "./project"},
        "bindings": BINDINGS,
        "analysis": {"reviewed_by": reviewed_by},
    }
    path = tmp_path / "automations/bot/automation.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_analyse_command_writes_touches_only_when_asked(tmp_path, monkeypatch):
    path = _repo(tmp_path)
    monkeypatch.setenv("LABCTL_ROOT", str(tmp_path))
    assert runner.invoke(app, ["automation", "analyse", "bot"]).exit_code == 0
    assert "touches" not in yaml.safe_load(path.read_text(encoding="utf-8"))
    result = runner.invoke(app, ["automation", "analyse", "bot", "--write"])
    assert result.exit_code == 0, result.output
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["touches"][0]["screens"][0]["uses"][0]["selector"] == "#go"
    assert saved["analysis"]["by"] == uipath.VERSION
    assert "#go" in runner.invoke(app, ["demand", "demo"]).output


def test_analyse_command_will_not_overwrite_a_reviewed_file(tmp_path, monkeypatch):
    path = _repo(tmp_path, reviewed_by="a.person")
    monkeypatch.setenv("LABCTL_ROOT", str(tmp_path))
    before = path.read_text(encoding="utf-8")
    assert runner.invoke(app, ["automation", "analyse", "bot", "--write"]).exit_code == 1
    assert path.read_text(encoding="utf-8") == before
    assert runner.invoke(app, ["automation", "analyse", "bot", "--write", "--force"]).exit_code == 0


def test_analysis_that_contradicts_the_declared_surface_is_reported(tmp_path, monkeypatch):
    path = _repo(tmp_path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) | {"surface": "backend"}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("LABCTL_ROOT", str(tmp_path))
    result = runner.invoke(app, ["automation", "analyse", "bot"])
    assert result.exit_code == 1
    assert "backend" in result.output
