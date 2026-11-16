import json
from pathlib import Path

from typer.testing import CliRunner

from labctl.botlog import netlog
from labctl.botlog.cli import app as botlog_app

# Hand-written, following the documented NetLog format (constants + events, integer types).
FIXTURE = Path(__file__).parent / "fixtures" / "botlog" / "netlog.json"
runner = CliRunner()


def test_requests_with_method_status_and_timing():
    reqs = {r.source_id: r for r in netlog.parse(FIXTURE)}
    assert sorted(reqs) == [10, 11, 12, 13]
    doc = reqs[10]
    assert (doc.method, doc.url, doc.status, doc.complete) == (
        "GET",
        "http://localhost:18720/cases",
        200,
        True,
    )
    assert int((doc.end - doc.start).total_seconds() * 1000) == 42
    # time is relative to timeTickOffset (ms since the epoch)
    assert doc.start.timestamp() * 1000 == 1794796800000 + 1000
    assert reqs[12].method == "PUT" and reqs[12].status == 422


def test_initiator_and_owning_document():
    reqs = {r.source_id: r for r in netlog.parse(FIXTURE)}
    assert reqs[11].initiator == "http://localhost:18720"
    assert reqs[11].initiator_url == "http://localhost:18720/cases"  # via source_dependency
    assert reqs[12].initiator_url is None


def test_request_without_end_is_incomplete():
    r = {r.source_id: r for r in netlog.parse(FIXTURE)}[13]
    assert r.complete is False and r.end is None and r.status is None


def test_truncated_file_from_a_killed_browser():
    text = FIXTURE.read_text(encoding="utf-8")
    no_close = text.rstrip().removesuffix("]}").rstrip()
    assert [r.source_id for r in netlog.parse_text(no_close)] == [10, 11, 12, 13]
    # cut in the middle of the last event, and again with a trailing comma
    mid = text[: text.rindex('"url": "http://localhost:18720/api/cases/C-1001/notes"')]
    reqs = netlog.parse_text(mid)
    assert [r.source_id for r in reqs] == [10, 11, 12, 13]
    assert reqs[-1].url is None  # its start-job event was lost
    assert len(netlog.parse_text(text[: text.index("1000") + 40])) == 0


def test_headers_values_never_reach_the_output(tmp_path):
    out = netlog.write_jsonl(netlog.parse(FIXTURE), tmp_path / "requests.jsonl")
    text = out.read_text(encoding="utf-8")
    assert "Authorization" not in text and "Cookie" not in text
    assert len(text.splitlines()) == 4
    assert json.loads(text.splitlines()[0])["source_id"] == 10


def test_cli_netlog(tmp_path):
    out = tmp_path / "r.jsonl"
    result = runner.invoke(botlog_app, ["netlog", str(FIXTURE), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "4 request(s)" in result.output
