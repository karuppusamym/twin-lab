"""Record a person (or a script) clicking through a web application, in the recording format.

A recording is a folder holding:

- `recording.har`: HAR 1.2 with content embedded, written by the browser;
- `dom/<seq>.html`: the page's outerHTML after a step (password field values blanked);
- `steps.jsonl`: one JSON object per step:
  `{"seq", "ts", "url", "action": "goto|click|type|select|submit", "selector", "dom"}`.

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
    if (pending.length && !joins(action)) flush();
    pending.push({action, selector});
    if (!first) first = Date.now();
    lastAt = Date.now();
    quietAgain();
  }
  window.__tlFlush = flush;

  document.addEventListener("click", (e) => note("click", selectorOf(target(e.target))), true);
  document.addEventListener("change", (e) => {
    const el = e.target, tag = el.tagName ? el.tagName.toLowerCase() : "";
    note(tag === "select" ? "select" : "type", selectorOf(el));
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
    """Receives captures from the page and writes `steps.jsonl` and `dom/`. No browser calls."""

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.seq = 0
        self.file = (out_dir / "steps.jsonl").open("w", encoding="utf-8")

    def on_capture(self, source, data: dict) -> None:
        events = data.get("events") or []
        for n, event in enumerate(events):
            self.seq += 1
            dom = None
            if n == len(events) - 1:  # one snapshot, after the last event of the batch
                dom = f"dom/{self.seq:04d}.html"
                (self.out_dir / dom).write_text(data.get("html", ""), encoding="utf-8")
            step = {"seq": self.seq, "ts": data.get("ts"), "url": data.get("url"),
                    "action": event.get("action"), "selector": event.get("selector"), "dom": dom}  # fmt: skip
            self.file.write(json.dumps(step) + "\n")
        self.file.flush()

    def close(self) -> None:
        self.file.close()


def record(
    url: str,
    out_dir: Path,
    script: Callable | None = None,
    headless: bool = False,
    *,
    settle_ms: int = 400,
) -> Path:
    """Open `url` in Chromium and record until the page is closed (or `script(page)` returns).

    With `script`, the callback drives the page (for tests and scripted evidence runs); otherwise a
    person clicks through the flow in a headed browser and closes the window to finish. Returns
    the recording folder.
    """
    from playwright.sync_api import sync_playwright

    out_dir = Path(out_dir)
    (out_dir / "dom").mkdir(parents=True, exist_ok=True)
    steps = _Steps(out_dir)
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
                script(page)
                page.wait_for_timeout(settle_ms + 200)
                if not page.is_closed():
                    page.evaluate("() => window.__tlFlush && window.__tlFlush()")
            else:
                page.wait_for_event("close", timeout=0)
            context.close()  # writes the HAR
            browser.close()
    finally:
        steps.close()
    return out_dir
