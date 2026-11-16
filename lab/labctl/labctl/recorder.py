"""Record a person (or a script) clicking through a web application, in the recording format.

A recording is a folder holding:

- `recording.har`: HAR 1.2 with content embedded, written by the browser;
- `dom/<seq>.html`: the page's outerHTML after a step (password field values blanked);
- `dom/<seq>.before.html`: the page as the action met it (clicks, typing, selects, submits);
- `screens/<seq>.png`: what the window showed after the step;
- `steps.jsonl`: one JSON object per step: `{"seq", "ts", "url", "action":
  "goto|click|type|select|submit", "selector", "dom", "dom_before", "screenshot"}`.

`record()` opens its own browser. `attach()` records a browser that is already running, such as
the one a robot drives, over the Chrome DevTools protocol.

The DOM is captured inside the page, after each navigation (full load or history change) and after
each click, change or submit, once the page has been quiet for `settle_ms`. Values typed are never
written to `steps.jsonl`. The raw recording is evidence: it holds whatever the application showed,
so it belongs in `evidence/`, which Git ignores.

Playwright is needed (`pip install playwright` and `python -m playwright install chromium`).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

LISTENER_JS = r"""
(() => {
  if (window.top !== window || window.__tlInstalled) return;
  window.__tlInstalled = true;
  const SETTLE = __SETTLE__, MAX_WAIT = 3000, JOIN = 150, NAV_JOIN = 1500;
  let pending = [], timer = null, first = 0, lastAt = 0;

  function selectorOf(el) {
    if (!el || el.nodeType !== 1) return null;
    const tag = el.tagName.toLowerCase();
    if (el.id) return tag + "#" + CSS.escape(el.id);
    const name = el.getAttribute("name");
    if (name) return tag + '[name="' + name + '"]';
    const classes = Array.from(el.classList).slice(0, 2).map((c) => "." + CSS.escape(c)).join("");
    return tag + classes;
  }
  function target(el) {
    const hit = el && el.closest && el.closest(
      "button, a, input, select, textarea, label, summary, [role=button], [role=link], [role=tab], [onclick]");
    return hit || el;
  }
  function snapshot() {
    const copy = document.documentElement.cloneNode(true);
    copy.querySelectorAll("input[type=password]").forEach((i) => i.removeAttribute("value"));
    return "<!DOCTYPE html>\n" + copy.outerHTML;
  }
  function flush() {
    clearTimeout(timer); timer = null;
    if (!pending.length) return Promise.resolve();
    const events = pending; pending = []; first = 0;
    return window.__tlStep({events, url: location.href, ts: new Date().toISOString(),
                            html: snapshot()});
  }
  function quietAgain() {
    if (!pending.length) return;
    clearTimeout(timer);
    if (Date.now() - first > MAX_WAIT) { flush(); return; }
    timer = setTimeout(flush, SETTLE);
  }
  function joins(action) {
    // A click and the submit or navigation it causes are one step; anything else starts a new
    // one, so what the page shows now is captured as the result of the previous step.
    const last = pending[pending.length - 1];
    if (!last) return false;
    const gap = Date.now() - lastAt;
    if (action === "submit") return last.action === "click" && gap < JOIN;
    if (action === "goto") return (last.action === "click" || last.action === "submit") && gap < NAV_JOIN;
    return false;
  }
  function note(action, selector) {
    const at = new Date().toISOString();  // before any work, so the action's requests come after
    if (pending.length && !joins(action)) flush();
    // In the capture phase nothing has handled the event yet: this is the page the action met.
    const before = action === "goto" ? null : snapshot();
    pending.push({action, selector, before, at});
    if (!first) first = Date.now();
    lastAt = Date.now();
    quietAgain();
  }
  window.__tlFlush = flush;

  document.addEventListener("click", (e) => note("click", selectorOf(target(e.target))), true);
  // What a person typed, kept only as its shape (letters A, digits 9, runs collapsed), never the
  // value: a field that shows another shape than was typed has an input mask.
  const typed = new WeakMap();
  function shapeOf(text) {
    let out = "";
    for (const ch of text) {
      const c = /\p{L}/u.test(ch) ? "A" : /\d/.test(ch) ? "9" : ch;
      if (!out || out[out.length - 1] !== c || (c !== "A" && c !== "9")) out += c;
    }
    return out;
  }
  document.addEventListener("input", (e) => {
    const el = e.target;
    if (!el || el.type === "password") return;
    if (e.inputType && e.inputType.startsWith("delete")) { typed.delete(el); return; }
    if (typeof e.data === "string") typed.set(el, (typed.get(el) || "") + e.data);
  }, true);
  document.addEventListener("change", (e) => {
    const el = e.target, tag = el.tagName ? el.tagName.toLowerCase() : "";
    note(tag === "select" ? "select" : "type", selectorOf(el));
    const raw = typed.get(el);
    if (raw && tag !== "select") pending[pending.length - 1].typed_shape = shapeOf(raw);
    typed.delete(el);
  }, true);
  document.addEventListener("submit", (e) => note("submit", selectorOf(e.target)), true);

  for (const name of ["pushState", "replaceState"]) {
    const orig = history[name];
    history[name] = function (...args) {
      // The old address still shows the old page: capture it before the address changes.
      if (pending.length && !joins("goto")) flush();
      const r = orig.apply(this, args);
      note("goto", null);
      return r;
    };
  }
  window.addEventListener("popstate", () => note("goto", null));
  new MutationObserver(quietAgain).observe(document, {subtree: true, childList: true,
                                                      attributes: true, characterData: true});
  if (document.readyState === "complete") note("goto", null);
  else window.addEventListener("load", () => note("goto", null));
})();
"""


class _Steps:
    """Receives captures from the page and writes `steps.jsonl`, `dom/` and `screens/`."""

    def __init__(self, out_dir: Path, screenshots: bool = True):
        self.out_dir = out_dir
        self.seq = 0
        self.screenshots = screenshots
        self.file = (out_dir / "steps.jsonl").open("w", encoding="utf-8")
        # Steps wait here for their picture: Playwright cannot be called from inside the binding
        # callback, so `drain()` takes the pictures when the recording loop gets control back.
        self.waiting: list[tuple[dict, object]] = []

    def drain(self, pictures: bool = True) -> None:
        waiting, self.waiting = self.waiting, []
        for step, page in waiting:
            if pictures and self.screenshots and page is not None and step["dom"]:
                rel = f"screens/{step['seq']:04d}.png"
                try:
                    (self.out_dir / "screens").mkdir(exist_ok=True)
                    page.screenshot(path=str(self.out_dir / rel), timeout=5000)
                    step["screenshot"] = rel
                except Exception:  # noqa: BLE001 - a missing picture never loses the step
                    pass
            self.file.write(json.dumps(step) + "\n")
        self.file.flush()

    def on_capture(self, source, data: dict) -> None:
        # Playwright passes the binding's source as a dict: {"context", "page", "frame"}.
        page = source.get("page") if isinstance(source, dict) else None
        events = data.get("events") or []
        for n, event in enumerate(events):
            self.seq += 1
            dom = before = None
            if event.get("before"):
                before = f"dom/{self.seq:04d}.before.html"
                (self.out_dir / before).write_text(event["before"], encoding="utf-8")
            if n == len(events) - 1:  # one snapshot, after the last event of the batch
                dom = f"dom/{self.seq:04d}.html"
                (self.out_dir / dom).write_text(data.get("html", ""), encoding="utf-8")
            # ts is when the action happened; the snapshot is taken once the page is quiet.
            step = {"seq": self.seq, "ts": event.get("at") or data.get("ts"), "url": data.get("url"),
                    "action": event.get("action"), "selector": event.get("selector"), "dom": dom,
                    "dom_before": before, "screenshot": None}  # fmt: skip
            if event.get("typed_shape"):
                step["typed_shape"] = str(event["typed_shape"])[:64]
            self.waiting.append((step, page))

    def close(self) -> None:
        self.drain(pictures=False)
        self.file.close()


class _Har:
    """A HAR 1.2 log built from network events, for a browser Twin Lab did not start (an
    existing context cannot be told to record a HAR). Bodies are embedded as text or base64."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def on_finished(self, request) -> None:
        from datetime import UTC, datetime

        try:
            response = request.response()
        except Exception:  # noqa: BLE001
            response = None
        timing = request.timing or {}
        total = max(0.0, float(timing.get("responseEnd", 0) or 0))
        start_ms = timing.get("startTime") or 0
        started = (
            datetime.fromtimestamp(start_ms / 1000, UTC) if start_ms > 0 else datetime.now(UTC)
        )
        entry = {
            "startedDateTime": started.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "time": total,
            "request": {
                "method": request.method,
                "url": request.url,
                "httpVersion": "HTTP/1.1",
                "headers": [{"name": k, "value": v} for k, v in request.headers.items()],
                "queryString": [],
                "cookies": [],
                "headersSize": -1,
                "bodySize": len(request.post_data or ""),
                **(
                    {
                        "postData": {
                            "mimeType": request.headers.get("content-type", ""),
                            "text": request.post_data,
                        }
                    }  # fmt: skip
                    if request.post_data
                    else {}
                ),
            },
            "response": {
                "status": response.status if response else 0,
                "statusText": response.status_text if response else "",
                "httpVersion": "HTTP/1.1",
                "headers": [
                    {"name": k, "value": v}
                    for k, v in (response.headers if response else {}).items()
                ],
                "cookies": [],
                "content": self._content(response),
                "redirectURL": "",
                "headersSize": -1,
                "bodySize": -1,
            },
            "cache": {},
            "timings": {"send": 0, "wait": total, "receive": 0},
            "_resourceType": request.resource_type,
        }
        self.entries.append(entry)

    @staticmethod
    def _content(response) -> dict:
        import base64

        if response is None:
            return {"size": 0, "mimeType": ""}
        mime = response.headers.get("content-type", "")
        try:
            body = response.body()
        except Exception:  # noqa: BLE001 - redirects and aborted loads have no body
            return {"size": 0, "mimeType": mime}
        try:
            return {"size": len(body), "mimeType": mime, "text": body.decode("utf-8")}
        except UnicodeDecodeError:
            return {"size": len(body), "mimeType": mime, "text": base64.b64encode(body).decode(),
                    "encoding": "base64"}  # fmt: skip

    def write(self, path: Path) -> None:
        doc = {"log": {"version": "1.2", "creator": {"name": "twinlab-attach", "version": "1"},
                       "pages": [], "entries": self.entries}}  # fmt: skip
        path.write_text(json.dumps(doc), encoding="utf-8")


def attach(
    cdp_url: str,
    out_dir: Path,
    *,
    settle_ms: int = 400,
    max_seconds: float = 1800,
    screenshots: bool = True,
    stop: Callable[[], bool] | None = None,
) -> Path:
    """Record a browser that is already running (a robot's Chrome or Edge started with
    `--remote-debugging-port=9222`): pass its address, for example `http://127.0.0.1:9222`.

    Every page of its first context is recorded until the browser closes, `stop()` returns
    true, `max_seconds` pass, or Ctrl+C. Nothing is changed in the browser except the listener
    added to its pages; it is left running.
    """
    from playwright.sync_api import sync_playwright

    out_dir = Path(out_dir)
    (out_dir / "dom").mkdir(parents=True, exist_ok=True)
    steps = _Steps(out_dir, screenshots)
    har = _Har()
    script = LISTENER_JS.replace("__SETTLE__", str(int(settle_ms)))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(cdp_url)
            if not browser.contexts:
                raise RuntimeError(f"the browser at {cdp_url} has no open window")
            context = browser.contexts[0]
            context.expose_binding("__tlStep", steps.on_capture)
            context.add_init_script(script)
            context.on("requestfinished", har.on_finished)
            context.on("requestfailed", har.on_finished)
            for page in context.pages:  # pages already open get the listener now
                try:
                    page.evaluate(script)
                except Exception:  # noqa: BLE001 - a page that is navigating gets it from init
                    pass
            import time

            started = time.monotonic()
            try:
                while browser.is_connected() and time.monotonic() - started < max_seconds:
                    if stop is not None and stop():
                        break
                    pages = [p for p in context.pages if not p.is_closed()]
                    if not pages:
                        break
                    steps.drain()
                    pages[0].wait_for_timeout(500)
            except KeyboardInterrupt:
                pass
            for page in context.pages:
                try:
                    page.evaluate("() => window.__tlFlush && window.__tlFlush()")
                except Exception:  # noqa: BLE001
                    pass
            steps.drain()
            # Disconnect only: the robot's browser keeps running.
    finally:
        steps.close()
        har.write(out_dir / "recording.har")
    return out_dir


def record(
    url: str,
    out_dir: Path,
    script: Callable | None = None,
    headless: bool = False,
    *,
    settle_ms: int = 400,
    max_seconds: float = 1800,
    screenshots: bool = True,
) -> Path:
    """Open `url` in Chromium and record until the page is closed (or `script(page)` returns).
    A window nobody closes is closed after `max_seconds`; what was recorded until then is kept.

    With `script`, the callback drives the page (for tests and scripted evidence runs); otherwise a
    person clicks through the flow in a headed browser and closes the window to finish. Returns
    the recording folder.
    """
    from playwright.sync_api import sync_playwright

    out_dir = Path(out_dir)
    (out_dir / "dom").mkdir(parents=True, exist_ok=True)
    steps = _Steps(out_dir, screenshots)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            context = browser.new_context(
                record_har_path=str(out_dir / "recording.har"),
                record_har_content="embed",
                record_har_mode="full",
            )
            context.expose_binding("__tlStep", steps.on_capture)
            context.add_init_script(LISTENER_JS.replace("__SETTLE__", str(int(settle_ms))))
            page = context.new_page()
            page.goto(url)
            if script is not None:
                # A script runs without pauses, so a picture taken afterwards would show the end
                # state for every step: scripted recordings have no pictures.
                script(page)
                page.wait_for_timeout(settle_ms + 200)
                if not page.is_closed():
                    page.evaluate("() => window.__tlFlush && window.__tlFlush()")
                steps.drain(pictures=False)
            else:
                import time

                started = time.monotonic()
                while not page.is_closed():
                    steps.drain()  # the picture of each step, at most half a second late
                    if time.monotonic() - started > max_seconds:
                        page.evaluate("() => window.__tlFlush && window.__tlFlush()")
                        steps.drain()
                        print(
                            f"No one closed the window within {max_seconds / 60:.0f} min; stopped."
                        )
                        break
                    try:
                        page.wait_for_timeout(300)
                    except Exception:  # noqa: BLE001 - the window was closed while waiting
                        break
            context.close()  # writes the HAR
            browser.close()
    finally:
        steps.close()
    return out_dir
