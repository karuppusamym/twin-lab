"""What a single-page application with framework-managed forms does to a recording, on a small
page that needs no framework (found with the Angular reference application, see
test_reference_angular.py, and reduced to this):

- the application gives itself its first address while it starts (a router's first navigation,
  a route guard's redirect), before the document's load event;
- a field is marked as edited from its first character, and as visited when it is left; the
  mark is rendered a moment after the event (a framework's change detection), and a required
  field shows its message then.

One recording and one replay serve the tests. Everything is served from 127.0.0.1.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

pytest.importorskip("playwright.sync_api")

from labctl import recorder  # noqa: E402
from labctl.behaviour import dom, fidelity  # noqa: E402
from labctl.botlog.recording import load  # noqa: E402

PAGE = b"""<!DOCTYPE html><html><body>
<h1 id="where"></h1>
<input id="first" class="pristine untouched"><p id="firstError" hidden>Required</p>
<input id="second" class="pristine untouched">
<button id="go" type="button">Go</button>
<script>
// As a router does while the application starts: nobody is signed in, the guard redirects.
if (location.pathname === "/") history.replaceState(null, "", "/start");
const show = () => { document.getElementById("where").textContent = location.pathname; };
show();
for (const el of document.querySelectorAll("input")) {
  // Rendered a moment later, as a framework does at its next change detection.
  el.addEventListener("input", () => setTimeout(() => el.classList.replace("pristine", "dirty")));
  el.addEventListener("blur", () => setTimeout(() => {
    el.classList.replace("untouched", "visited");
    if (el.id === "first") document.getElementById("firstError").hidden = el.value !== "";
  }));
}
document.getElementById("go").addEventListener("click", () => {
  history.pushState(null, "", "/next");
  show();
});
</script></body></html>"""


def serve():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - every address is the application (client-side routes)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def flow(page):
    page.wait_for_timeout(600)
    page.fill("#first", "alpha")
    page.fill("#second", "beta")  # no pause: the first field's step is still open
    page.click("#go", delay=80)  # a person's click: pressed, then released
    page.wait_for_url("**/next")
    page.wait_for_timeout(600)
    page.fill("#second", "gamma")  # the recording ends with the focus still in the field
    page.wait_for_timeout(600)


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    server, base = serve()
    out = tmp_path_factory.mktemp("spa-forms") / "rec"
    try:
        recorder.record(f"{base}/", out, headless=True, script=flow)
    finally:
        server.shutdown()
    steps = [json.loads(x) for x in (out / "steps.jsonl").read_text("utf-8").splitlines() if x]
    typed = [s["seq"] for s in steps if s["action"] == "type"]
    values = dict(zip(typed, ["Syn one", "Syn two", "Syn three"], strict=True))
    (out / "bindings.yaml").write_text(
        yaml.safe_dump({"version": 1, "steps": {k: {"value": v} for k, v in values.items()}}),
        "utf-8",
    )
    return out, base, steps


def signatures(rec: Path, step: dict) -> list[str]:
    return dom.snapshot((rec / step["dom"]).read_text("utf-8")).signatures


def test_an_address_the_page_gives_itself_before_its_load_is_part_of_the_load(recorded):
    """The redirect made while the page starts was written as a `replace` step BEFORE the load
    step of its own document: a replay waited for that address on a page not opened yet."""
    _, base, steps = recorded
    gotos = [(s.get("nav"), s["url"].replace(base, "")) for s in steps if s["action"] == "goto"]
    assert gotos == [("navigate", "about:blank"), ("navigate", "/start"), ("push", "/next")]
    push = next(s for s in steps if s.get("nav") == "push")
    click = next(s for s in steps if s["action"] == "click")
    assert push["caused_by"] == click["seq"]  # a history change after the load is still a step


def test_the_step_before_typing_is_snapshotted_before_the_typing_shows(recorded):
    """Typing is written as a step when it ends. The step before it used to be snapshotted then
    too, with the next field already marked as edited, which no replay shows at that step."""
    rec, _, steps = recorded
    first = next(s for s in steps if s["action"] == "type")
    assert first["selector"] == "input#first"
    assert "input#second.pristine.untouched" in signatures(rec, first)


def test_a_type_step_says_when_its_field_was_left_and_the_replay_leaves_it_too(recorded):
    rec, base, steps = recorded
    first, second, last = [s for s in steps if s["action"] == "type"]
    # The focus had gone on (to the next field, to the button) when the snapshot was taken.
    # A step says its field was left exactly when its snapshot shows what leaving did. The
    # second field is left by a person's click (held for 80 ms): what leaving does is rendered
    # by then. The first is left by typing into the next field at once: on a fast machine its
    # snapshot shows it left, on a busy one not yet, and the step says so either way.
    first_left = "input#first.dirty.visited" in signatures(rec, first)
    assert bool(first.get("left")) == first_left
    assert first_left or "input#first.dirty.untouched" in signatures(rec, first)
    assert second.get("left") is True
    assert "input#second.dirty.visited" in signatures(rec, second)
    assert last.get("left") is None  # the recording ended with the focus in the field
    assert [s.left for s in load(rec).steps if s.action == "type"] == [first_left, True, False]

    server, fresh = serve()
    try:
        report = fidelity.replay(rec, origins={base: fresh}, resolve_timeout_ms=3000)
    finally:
        server.shutdown()
    diffs = [d.text() for s in report.steps for d in s.differences]
    assert report.status == "pass" and diffs == [], report.markdown()
    assert report.coverage.failed == 0 and report.coverage.compared >= 5, report.coverage


def test_a_slow_browser_never_says_left_without_showing_it(tmp_path):
    """On a busy machine the next action reaches the page before its own timers have run. A step
    that says its field was left must show what leaving did, or a replay that leaves the field
    differs from the recording (this failed one run in CI and five of six with the CPU slowed)."""

    def slow(page):
        # the recorder hands a script a wrapper around the page; the browser wants the page
        cdp = page.context.new_cdp_session(getattr(page, "_page", page))
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": 20})
        flow(page)

    server, base = serve()
    out = tmp_path / "rec"
    try:
        recorder.record(f"{base}/", out, headless=True, script=slow)
    finally:
        server.shutdown()
    steps = [json.loads(x) for x in (out / "steps.jsonl").read_text("utf-8").splitlines() if x]
    typed = [s for s in steps if s["action"] == "type"]
    assert len(typed) == 3
    for step in typed:
        own = [x for x in signatures(out, step) if x.startswith(step["selector"])]
        assert own, step
        if step.get("left"):
            assert any(".visited" in x for x in own), (step["seq"], own)
    # the first field had never been left before: shown as visited only when the step says so
    first = typed[0]
    shown = any(".visited" in x for x in signatures(out, first) if x.startswith("input#first"))
    assert shown == bool(first.get("left"))
