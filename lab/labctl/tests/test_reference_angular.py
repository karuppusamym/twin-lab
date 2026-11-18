"""The Angular reference application (reference/web/caseboard-angular) through the three
certification gates, on its committed production build (`site/`): no npm, only Python and
Chromium.

- DETECT: the lab's detection names Angular, the version the build was made with, and its marker.
- CAPTURE: every action of the hand-written `expected-actions.yaml` is in a recording of
  `flow.py`.
- SELF-REPLAY: the recording replays against a fresh instance of the same build, every step
  passing.

One recording and one replay serve all the tests (about half a minute). What this does not
prove is listed in the application's README.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import pytest

APP = Path(__file__).resolve().parents[3] / "reference" / "web" / "caseboard-angular"


def _load(name: str):
    full = f"caseboard_angular_{name}"
    if full not in sys.modules:
        spec = importlib.util.spec_from_file_location(full, APP / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        spec.loader.exec_module(module)
    return sys.modules[full]


serve = _load("serve")


def _get(url: str) -> tuple[int, str, str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 - 127.0.0.1 only
            return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, "", ""


def test_the_committed_build_is_complete_and_served_as_a_single_page_application():
    """Every file index.html names is in `site/`, a route's address is answered with the shell,
    scripts are served as JavaScript, and a missing file is a 404 (not the shell)."""
    index = (APP / "site" / "index.html").read_text("utf-8")
    assert "<app-root>" in index and "http://" not in index and "https://" not in index
    assert (APP / "site" / "3rdpartylicenses.txt").is_file()
    run = serve.serve()
    try:
        status, kind, body = _get(f"{run.base}/cases/CB-1007")
        assert status == 200 and kind.startswith("text/html") and body == index
        import re

        named = re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"', index)
        assert any(n.endswith(".js") for n in named), index
        for name in named:
            status, kind, _ = _get(f"{run.base}/{name}")
            want = "text/javascript" if name.endswith(".js") else "text/css"
            assert status == 200 and kind.startswith(want), (name, status, kind)
        assert _get(f"{run.base}/no-such-file.js")[0] == 404
    finally:
        run.stop()


pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    return _load("certify_local").certify(tmp_path_factory.mktemp("caseboard-angular"))


def _steps(rec: Path) -> list[dict]:
    return [json.loads(x) for x in (rec / "steps.jsonl").read_text("utf-8").splitlines() if x]


def test_detect_names_angular_its_version_and_the_marker(result):
    certify = _load("certify_local")
    front = result.frontend
    version = certify.angular_version()
    assert front.get("framework") == "angular", front
    assert front.get("version") == version, front
    assert front.get("marker") == f'ng-version="{version}"', front
    assert front.get("rendering") == "client", front
    assert result.detect_ok


def test_capture_has_every_expected_action(result):
    expected = _load("certify_local").expected_actions()
    assert result.expected == len(expected) >= 30
    assert result.missing == [], result.missing
    assert {"goto", "type", "click", "submit", "select", "check", "keypress:Escape"} <= set(
        expected
    )
    steps = _steps(result.recording)
    # Client-side routing: one page load (at the address the guard led to), then history changes
    # only: to the list, to the lazy-loaded edit form, back to the list, to sign-in.
    gotos = [s for s in steps if s["action"] == "goto" and s["url"] != "about:blank"]
    assert [s.get("nav") for s in gotos] == ["navigate", "push", "push", "push", "push"]
    paths = [urlsplit(s["url"]).path for s in gotos]
    assert paths == ["/sign-in", "/cases", "/cases/CB-1007", "/cases", "/sign-in"], paths
    assert all(isinstance(s.get("caused_by"), int) for s in gotos[1:])
    assert result.capture_ok


def test_the_recording_keeps_no_typed_value_and_asked_nothing_of_another_host(result):
    flow = _load("flow")
    rec = result.recording
    kept = [p for p in rec.rglob("*") if p.is_file() and p.suffix != ".png"
            and p.name != "bindings.yaml"]  # fmt: skip
    text = "".join(p.read_text("utf-8", "replace") for p in kept)
    for value in (flow.WRONG_PASSWORD, flow.TITLE, flow.NOTES):
        assert value not in text, value
    har = json.loads((rec / "recording.har").read_text("utf-8"))
    hosts = {urlsplit(e["request"]["url"]).hostname for e in har["log"]["entries"]}
    assert hosts == {"127.0.0.1"}, hosts


def test_self_replay_passes_every_step(result):
    report = result.report
    cov = report.coverage
    assert report.status == "pass", "\n".join(result.lines())
    assert [s.seq for s in report.steps if s.status != "pass"] == [], "\n".join(result.lines())
    assert cov.failed == 0 and cov.skipped == 0 and cov.replayed == cov.steps, cov
    assert cov.compared >= 20, cov  # snapshots were compared, not only actions performed
    assert report.blocked_requests == []
    assert result.replay_ok and result.ok
