import json
from pathlib import Path

from typer.testing import CliRunner

from labctl.botlog import robotlog
from labctl.botlog.cli import app as botlog_app

FIX = Path(__file__).parent / "fixtures" / "botlog"
INPUTS = json.loads((FIX / "inputs.json").read_text(encoding="utf-8"))
runner = CliRunner()


def line(message, state=None, activity="UiPath.Core.Activities.TypeInto", job="job-1", **info):
    out = {
        "message": message,
        "level": "Verbose",
        "timeStamp": info.pop("ts", "2026-11-10T14:01:40.2104567Z"),
        "processName": "loan_payoff_v3",
        "jobId": job,
        "robotName": "robot07",
    }
    if state:
        out["activityInfo"] = {
            "Activity": activity,
            "DisplayName": info.pop("display", "Type customer name"),
            "State": state,
            **info,
        }
    return json.dumps(out)


# --- the harness bot's log ---------------------------------------------------------------------


def test_harness_log_parses_into_one_trace_per_job():
    runs = robotlog.parse(FIX / "robot.log.jsonl")
    assert len(runs) == 3
    assert len({r.job_id for r in runs}) == 3
    for r in runs:
        assert r.process == "CaseUpdate"
        assert r.bad_lines == 0
        assert r.unmapped_fields == []
        assert all(s.state == "Closed" and s.duration_ms is not None for s in r.steps)


def test_typed_values_match_the_bot_inputs():
    for run, inputs in zip(robotlog.parse(FIX / "robot.log.jsonl"), INPUTS, strict=True):
        typed = {s.display: s.values.in_.get("Text") for s in run.steps if s.activity == "TypeInto"}
        selected = [s.values.in_["Item"] for s in run.steps if s.activity == "SelectItem"]
        assert typed["Type into in_CaseNo"] == inputs["case_no"]
        assert typed["Type into in_User"] == "demo.user"
        assert selected == [inputs["category"], inputs["subcategory"], inputs["select#caseStatus"]]
        read = next(s for s in run.steps if s.display == "Get text from out_Message")
        assert read.values.out["Value"]


def test_private_activity_is_redacted_with_no_values():
    run = robotlog.parse(FIX / "robot.log.jsonl")[0]
    pw = next(s for s in run.steps if s.display == "Type into in_Password")
    assert pw.redacted is True
    assert pw.values.in_ == {} and pw.values.out == {} and pw.variables == {}
    assert pw.selector is None
    text = (FIX / "robot.log.jsonl").read_text(encoding="utf-8")
    assert "workbench-demo" not in text


def test_selector_is_read_and_turned_into_css():
    run = robotlog.parse(FIX / "robot.log.jsonl")[0]
    step = next(s for s in run.steps if s.display == "Type into in_CaseNo")
    assert step.selector["css"] == "input#caseNo"
    assert step.selector["id"] == "caseNo"
    assert "Selector" not in step.values.in_


# --- shapes and robustness ---------------------------------------------------------------------


def test_executing_and_closed_pair_into_one_step_with_duration():
    text = "\n".join(
        [
            line(
                "started",
                "Executing",
                Arguments={"Text": "JOHN SMITH"},
                Variables={"custName": "JOHN SMITH"},
            ),
            line(
                "closed",
                "Closed",
                ts="2026-11-10T14:01:41.7104567Z",
                Arguments={"Text": "JOHN SMITH"},
            ),
        ]
    )
    [run] = robotlog.parse_text(text).runs
    [step] = run.steps
    assert step.duration_ms == 1500
    assert step.values.in_ == {"Text": "JOHN SMITH"}
    assert step.variables == {"custName": "JOHN SMITH"}
    assert step.activity == "TypeInto"


def test_faulted_activity_keeps_its_message():
    text = "\n".join(
        [
            line("x", "Executing", activity="UiPath.Core.Activities.Click", display="Click it"),
            line(
                "Click it: element not found",
                "Faulted",
                activity="UiPath.Core.Activities.Click",
                display="Click it",
            ),
        ]
    )
    [step] = robotlog.parse_text(text).runs[0].steps
    assert step.state == "Faulted"
    assert "not found" in step.message


def test_explicit_private_flag_and_missing_arguments_both_redact():
    text = "\n".join(
        [
            line("a", "Executing", display="Type SSN"),  # TypeInto with no arguments at all
            line("b", "Closed", display="Type SSN"),
            line(
                "c",
                "Executing",
                activity="UiPath.Core.Activities.Click",
                display="Go",
                Private=True,
            ),
        ]
    )
    steps = robotlog.parse_text(text).runs[0].steps
    assert [s.redacted for s in steps] == [True, True]
    assert steps[1].redaction_reason == "Private"


def test_bad_lines_are_counted_never_raised():
    text = "\n".join([line("ok", "Executing"), "{not json", "[1, 2", line("ok", "Closed"), "42"])
    result = robotlog.parse_text(text)
    assert result.bad_lines == 3
    assert result.runs[0].bad_lines == 3
    assert len(result.runs[0].steps) == 1


def test_unknown_keys_are_listed_not_dropped():
    d = json.loads(line("x", "Executing", Tag="custom", Arguments={"Text": "A"}))
    d["queueName"] = "Q1"
    [run] = robotlog.parse_text(json.dumps(d)).runs
    assert run.unmapped_fields == ["activityInfo.Tag", "queueName"]


def test_missing_activity_info_and_fields_are_tolerated():
    text = "\n".join(
        [json.dumps({"message": "hello"}), json.dumps({"level": "Info"}), json.dumps({})]
    )
    result = robotlog.parse_text(text)
    assert result.bad_lines == 0
    assert result.runs[0].steps == []
    assert result.runs[0].log_lines == 3


def test_run_boundary_on_job_change_and_on_execution_messages():
    by_job = "\n".join([line("a", "Executing", job="j1"), line("b", "Executing", job="j2")])
    assert [r.job_id for r in robotlog.parse_text(by_job).runs] == ["j1", "j2"]
    no_job = [
        {"message": "P execution started", "timeStamp": "2026-11-10T14:00:00Z"},
        {
            "message": "x",
            "activityInfo": {"Activity": "Click", "DisplayName": "c", "State": "Executing"},
        },
        {"message": "P execution ended", "timeStamp": "2026-11-10T14:00:05Z"},
        {"message": "P execution started", "timeStamp": "2026-11-10T14:01:00Z"},
        {
            "message": "y",
            "activityInfo": {"Activity": "Click", "DisplayName": "c", "State": "Executing"},
        },
    ]
    runs = robotlog.parse_text("\n".join(json.dumps(r) for r in no_job)).runs
    assert len(runs) == 2
    assert runs[0].run_id != runs[1].run_id


def test_orchestrator_export_array_with_raw_message():
    raw = json.loads(line("x", "Executing", Arguments={"Text": "JOHN SMITH"}))
    export = [
        {
            "Level": "Trace",
            "TimeStamp": "2026-11-10T14:01:40.21Z",
            "RawMessage": json.dumps(raw),
            "Id": 1,
        },
        {
            "Level": "Trace",
            "TimeStamp": "2026-11-10T14:01:41.21Z",
            "RawMessage": "not json",
            "Id": 2,
        },
    ]
    result = robotlog.parse_text(json.dumps(export))
    [run] = result.runs
    assert run.job_id == "job-1"
    assert run.steps[0].values.in_ == {"Text": "JOHN SMITH"}
    assert run.unmapped_fields == ["RawMessage"]  # the unreadable one is listed, not dropped


def test_odata_page_and_nlog_lines():
    page = {"@odata.context": "x", "value": [json.loads(line("x", "Executing"))]}
    assert len(robotlog.parse_text(json.dumps(page)).runs[0].steps) == 1
    nlog = json.dumps(
        {
            "time": "2026-11-10 14:01:40.2104",
            "level": "Trace",
            "message": "Type Executing",
            "properties": {
                "jobId": "n1",
                "activityInfo": {"Activity": "TypeInto", "State": "Executing"},
            },
        }
    )
    [run] = robotlog.parse_text(nlog).runs
    assert run.job_id == "n1"
    assert run.steps[0].ts.year == 2026


def test_cli_parse_writes_traces(tmp_path):
    out = tmp_path / "runs.json"
    result = runner.invoke(botlog_app, ["parse", str(FIX / "robot.log.jsonl"), "--out", str(out)])
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data) == 3
    assert data[0]["steps"][1]["values"]["in"]["Text"] == "demo.user"
