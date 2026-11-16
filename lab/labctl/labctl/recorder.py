"""Record a person (or a script) clicking through a web application, in the recording format.

A recording is a folder holding:

- `recording.har`: HAR 1.2 with content embedded, written by the browser;
- `dom/<seq>.html`: the page after a step, with the documents of its frames (`<tl-frame>`), its
  shadow roots open and closed (`<tl-shadow>`), and the OCR text of its canvases
  (`<tl-canvas-text>`) written in (password field values blanked);
- `dom/<seq>.before.html`: the page as the action met it (for an action inside a frame, that
  frame's document: `before_scope: "frame"`);
- `screens/<seq>.png`: what the window showed after the step; `screens/<seq>.cN.png`: each canvas;
- `docs/`: PDFs the pages showed, with their text and `documents.json` (form field names only);
- `steps.jsonl`: one JSON object per step: `{"seq", "ts", "url", "action":
  "goto|click|type|select|submit", "selector", "dom", "dom_before", "screenshot"}`, plus `frame`
  for an action inside a frame (its selector is then `iframe#x >>> button#y`, see labctl.locate).

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
  if (window.__tlInstalled) return;
  window.__tlInstalled = true;
  // Runs in every frame. The top page keeps the steps and takes the snapshots; a frame reports
  // its actions and changes to its parent with postMessage (which works across origins), each
  // parent adds the frame's selector, and the top page gets "iframe#x >>> button#save".
  const TOKEN = "__TOKEN__", TOP = window.top === window;
  const SETTLE = __SETTLE__, MAX_WAIT = 3000, JOIN = 150, NAV_JOIN = 1500;
  const SERIALIZE = __SERIALIZE__;
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
  // Events from inside a shadow root reach the document retargeted to the host: the first
  // element of the composed path is the one actually used.
  function realTarget(e) {
    const path = e.composedPath ? e.composedPath() : [];
    return path.length && path[0].nodeType === 1 ? path[0] : e.target;
  }
  function snapshot() { return SERIALIZE({values: false}); }
  function up(msg) {
    try { window.parent.postMessage(Object.assign({__tl: TOKEN}, msg), "*"); } catch (e) {}
  }
  function frameOf(win) {
    for (const f of document.querySelectorAll("iframe, frame")) {
      try { if (f.contentWindow === win) return selectorOf(f); } catch (e) {}
    }
    return "iframe";
  }
  function flush() {
    clearTimeout(timer); timer = null;
    if (!TOP || !pending.length) return Promise.resolve();
    const events = pending; pending = []; first = 0;
    return window.__tlStep({events, url: location.href, ts: new Date().toISOString(),
                            html: snapshot()});
  }
  function quietAgain() {
    if (!TOP) { changed(); return; }
    if (!pending.length) return;
    clearTimeout(timer);
    if (Date.now() - first > MAX_WAIT) { flush(); return; }
    timer = setTimeout(flush, SETTLE);
  }
  let lastPing = 0;
  function changed() {  // a frame's page changed: the top page waits until it settles too
    if (Date.now() - lastPing > 100) { lastPing = Date.now(); up({kind: "mut"}); }
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
  function record(ev) {
    if (pending.length && !joins(ev.action)) flush();
    pending.push(ev);
    if (!first) first = Date.now();
    lastAt = Date.now();
    quietAgain();
  }
  function note(action, selector, typedShape) {
    const at = new Date().toISOString();  // before any work, so the action's requests come after
    // In the capture phase nothing has handled the event yet: this is the page the action met.
    // In a frame, the page it met is the frame's own document.
    const before = action === "goto" ? null : snapshot();
    if (!TOP) {
      if (action !== "goto") up({kind: "note", action, selector, before, at, typed_shape: typedShape, frames: []});
      return;
    }
    record({action, selector, before, at, typed_shape: typedShape});
  }
  window.addEventListener("message", (e) => {
    const d = e.data;
    if (!d || d.__tl !== TOKEN || !e.source || e.source === window) return;
    if (d.kind === "mut") { quietAgain(); return; }
    if (d.kind !== "note") return;
    const frames = [frameOf(e.source)].concat(d.frames || []);
    if (!TOP) { up(Object.assign({}, d, {frames})); return; }
    record({action: d.action, selector: frames.join(" >>> ") + " >>> " + d.selector,
            frame: frames.join(" >>> "), before: d.before, before_scope: "frame",
            at: d.at, typed_shape: d.typed_shape});
  });
  window.__tlFlush = flush;

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
  function onChange(e) {
    const el = realTarget(e), tag = el.tagName ? el.tagName.toLowerCase() : "";
    const raw = typed.get(el);
    typed.delete(el);
    note(tag === "select" ? "select" : "type", selectorOf(el),
         raw && tag !== "select" ? shapeOf(raw) : undefined);
  }
  function onSubmit(e) { note("submit", selectorOf(realTarget(e))); }
  // A closed root hides its inside even from the event path: such a click reaches the document
  // as a click on the host, and the listener inside the root (below) records the real element.
  function closedHost(el) {
    return !!(el && el.nodeType === 1 && !el.shadowRoot && window.__tlShadows
              && window.__tlShadows.get(el));
  }
  document.addEventListener("click", (e) => {
    const el = realTarget(e);
    if (closedHost(el)) return;
    note("click", selectorOf(target(el)));
  }, true);
  document.addEventListener("input", (e) => {
    const el = realTarget(e);
    if (!el || el.type === "password") return;
    if (e.inputType && e.inputType.startsWith("delete")) { typed.delete(el); return; }
    if (typeof e.data === "string") typed.set(el, (typed.get(el) || "") + e.data);
  }, true);
  document.addEventListener("change", onChange, true);
  document.addEventListener("submit", onSubmit, true);
  // change and submit do not leave a shadow root: listen inside every root (closed ones too, as
  // the shadow hook hands them over), and watch them for changes.
  const watched = new WeakSet();
  function watchRoot(root) {
    if (!root || watched.has(root)) return;
    watched.add(root);
    root.addEventListener("change", onChange, true);
    root.addEventListener("submit", onSubmit, true);
    if (root.mode === "closed") {
      root.addEventListener("click", (e) => note("click", selectorOf(target(e.target))), true);
      root.addEventListener("input", (e) => {
        const el = e.target;
        if (!el || el.type === "password") return;
        if (e.inputType && e.inputType.startsWith("delete")) { typed.delete(el); return; }
        if (typeof e.data === "string") typed.set(el, (typed.get(el) || "") + e.data);
      }, true);
    }
    new MutationObserver(quietAgain).observe(root, {subtree: true, childList: true,
                                                    attributes: true, characterData: true});
  }
  window.__tlOnShadow = watchRoot;
  (window.__tlShadowList || []).forEach(watchRoot);
  for (const el of document.querySelectorAll("*")) if (el.shadowRoot) watchRoot(el.shadowRoot);

  if (TOP) {
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
  }
  new MutationObserver(quietAgain).observe(document, {subtree: true, childList: true,
                                                      attributes: true, characterData: true});
  if (TOP) {
    if (document.readyState === "complete") note("goto", null);
    else window.addEventListener("load", () => note("goto", null));
  } else {
    changed();  // a frame that loads is a change of the page
  }
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
        self.fetched: dict[str, bytes] = {}  # PDFs the pages embed: url -> bytes

    def drain(self, pictures: bool = True) -> None:
        """Finish the waiting steps: frames the page could not read, canvases, the picture.
        Called when the recording loop has control (never from inside the binding)."""
        from labctl.behaviour.dom import fill_frames

        waiting, self.waiting = self.waiting, []
        for step, page in waiting:
            html = step.pop("_html", None)
            live = pictures and page is not None
            if html is not None:
                if live:
                    try:
                        html = fill_frames(page, html, values=False)
                    except Exception:  # noqa: BLE001 - the page may have gone
                        pass
                    if self.screenshots:
                        html = self._canvases(page, html, step["seq"])
                    self._documents(page, html, step.get("url") or "")
                if "data-tl-pending=" in html:
                    step["frames_unread"] = html.count("data-tl-pending=")
                (self.out_dir / step["dom"]).write_text(html, encoding="utf-8")
            if live and self.screenshots and step["dom"]:
                rel = f"screens/{step['seq']:04d}.png"
                try:
                    (self.out_dir / "screens").mkdir(exist_ok=True)
                    page.screenshot(path=str(self.out_dir / rel), timeout=5000)
                    step["screenshot"] = rel
                except Exception:  # noqa: BLE001 - a missing picture never loses the step
                    pass
            self.file.write(json.dumps(step) + "\n")
        self.file.flush()

    def _documents(self, page, html: str, base: str) -> None:
        """PDFs the page embeds, fetched with the page's own sign-in (a browser without a PDF
        viewer, such as a headless one, never loads them itself)."""
        import re
        from urllib.parse import urljoin

        for tag in re.findall(r'<(?:embed|object|iframe)\b[^>]*data-tl-document="pdf"[^>]*>', html):
            m = re.search(r'\b(?:src|data)="([^"]+)"', tag)
            if not m:
                continue
            url = urljoin(base, m.group(1).replace("&amp;", "&"))
            if url in self.fetched:
                continue
            try:
                r = page.request.get(url, timeout=15000)
                if r.ok and r.body().startswith(b"%PDF"):
                    self.fetched[url] = r.body()
            except Exception:  # noqa: BLE001 - kept as a marked element without its document
                pass

    def _canvases(self, page, html: str, seq: int) -> str:
        """A picture of each canvas on the page, and the text OCR reads in it, written into the
        snapshot as <tl-canvas-text> after the canvas: drawn screens have no HTML of their own."""
        import re

        from labctl import ocr

        for cid in dict.fromkeys(re.findall(r'data-tl-canvas="(c\d+)"', html)):
            rel = f"screens/{seq:04d}.{cid}.png"
            try:
                (self.out_dir / "screens").mkdir(exist_ok=True)
                page.locator(f'[data-tl-canvas-id="{cid}"]').first.screenshot(
                    path=str(self.out_dir / rel), timeout=3000
                )
            except Exception:  # noqa: BLE001 - a canvas in a frame, or one that went away
                continue
            words = ocr.text(self.out_dir / rel) or ""
            from html import escape

            note = (f'<tl-canvas-text data-tl-for="{cid}" data-tl-image="{rel}" '
                    f'data-tl-ocr="{ocr.available() or "none"}">{escape(words)}</tl-canvas-text>')  # fmt: skip
            html = re.sub(rf'(<canvas[^>]*data-tl-canvas="{cid}"[^>]*>.*?</canvas>)',
                          lambda m, note=note: m.group(1) + note, html, count=1, flags=re.S)  # fmt: skip
        return html

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
            html = None
            if n == len(events) - 1:  # one snapshot, after the last event of the batch
                dom = f"dom/{self.seq:04d}.html"
                html = data.get("html", "")  # written by drain(), once frames are filled in
            # ts is when the action happened; the snapshot is taken once the page is quiet.
            step = {"seq": self.seq, "ts": event.get("at") or data.get("ts"), "url": data.get("url"),
                    "action": event.get("action"), "selector": event.get("selector"), "dom": dom,
                    "dom_before": before, "screenshot": None}  # fmt: skip
            if event.get("frame"):
                step["frame"] = str(event["frame"])  # the action happened inside this frame
                step["before_scope"] = "frame"  # its dom_before is that frame's document
            if event.get("typed_shape"):
                step["typed_shape"] = str(event["typed_shape"])[:64]
            if html is not None:
                step["_html"] = html
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


def _init_scripts(settle_ms: int) -> list[str]:
    """The shadow hook first (before the page's own code makes any shadow root), then the
    listener, with a fresh token so a page's own messages are never taken for a frame's."""
    import secrets

    from labctl.behaviour.dom import SERIALIZE_FN, SHADOW_HOOK_JS

    listener = (LISTENER_JS.replace("__SETTLE__", str(int(settle_ms)))
                .replace("__TOKEN__", secrets.token_hex(8))
                .replace("__SERIALIZE__", SERIALIZE_FN))  # fmt: skip
    return [SHADOW_HOOK_JS, listener]


class _Draining:
    """The page handed to a recording script: before each call, the steps recorded so far are
    finished (pictures, frames, canvases), so each step's picture shows the page as it settled,
    not the end of the script."""

    def __init__(self, page, steps: _Steps) -> None:
        self._page, self._steps = page, steps

    def __getattr__(self, name):
        attr = getattr(self._page, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            self._steps.drain()
            return attr(*args, **kwargs)

        return call


PDF_TYPES = ("application/pdf", "application/x-pdf")


def save_documents(out_dir: Path, fetched: dict[str, bytes] | None = None) -> list[dict]:
    """PDFs the application showed, from the HAR (and any fetched while recording): each is kept
    as docs/NN.pdf with its text in docs/NN.txt, and docs/documents.json lists, per document, its
    address, page count and form field names (never the values typed into them)."""
    import base64

    found: dict[str, bytes] = dict(fetched or {})
    har_file = out_dir / "recording.har"
    try:
        entries = json.loads(har_file.read_text(encoding="utf-8"))["log"]["entries"]
    except (OSError, ValueError, KeyError, TypeError):
        entries = []
    for e in entries:
        content = (e.get("response") or {}).get("content") or {}
        url = (e.get("request") or {}).get("url") or ""
        mime = (content.get("mimeType") or "").split(";")[0].strip().lower()
        if mime not in PDF_TYPES and not url.lower().split("?")[0].endswith(".pdf"):
            continue
        text = content.get("text")
        if not text:
            continue
        body = (
            base64.b64decode(text)
            if content.get("encoding") == "base64"
            else text.encode("latin-1", "replace")
        )
        if body.startswith(b"%PDF"):
            found.setdefault(url, body)
    out = []
    for n, (url, body) in enumerate(found.items(), start=1):
        folder = out_dir / "docs"
        folder.mkdir(exist_ok=True)
        (folder / f"{n:02d}.pdf").write_bytes(body)
        info = {
            "url": url,
            "file": f"docs/{n:02d}.pdf",
            "pages": None,
            "fields": [],
            "text_chars": 0,
        }
        try:
            import io

            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(body))
            info["pages"] = len(reader.pages)
            words = "\n".join((p.extract_text() or "") for p in reader.pages)
            (folder / f"{n:02d}.txt").write_text(words, encoding="utf-8")
            info["text_chars"] = len(words)
            fields = reader.get_fields() or {}
            info["fields"] = [{"name": k, "type": str(v.get("/FT", "")).lstrip("/")}
                              for k, v in fields.items()]  # fmt: skip
        except Exception as exc:  # noqa: BLE001 - an unreadable PDF is kept, and said so
            info["problem"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        out.append(info)
    if out:
        (out_dir / "docs" / "documents.json").write_text(
            json.dumps(out, indent=1), encoding="utf-8"
        )
    return out


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
    scripts = _init_scripts(settle_ms)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(cdp_url)
            if not browser.contexts:
                raise RuntimeError(f"the browser at {cdp_url} has no open window")
            context = browser.contexts[0]
            context.expose_binding("__tlStep", steps.on_capture)
            for s in scripts:
                context.add_init_script(s)
            context.on("requestfinished", har.on_finished)
            context.on("requestfailed", har.on_finished)
            # Pages already open get the listener now, in every frame. Closed shadow roots they
            # made before this moment cannot be reached; new ones are.
            for page in context.pages:
                for frame in page.frames:
                    for s in scripts:
                        try:
                            frame.evaluate(s)
                        except Exception:  # noqa: BLE001 - navigating: the init script runs
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
        save_documents(out_dir, steps.fetched)
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
            for s in _init_scripts(settle_ms):
                context.add_init_script(s)
            page = context.new_page()
            page.goto(url)
            if script is not None:
                # Each call the script makes first finishes the steps before it, so pictures show
                # each step as it settled, not the end of the script.
                script(_Draining(page, steps))
                page.wait_for_timeout(settle_ms + 200)
                if not page.is_closed():
                    page.evaluate("() => window.__tlFlush && window.__tlFlush()")
                steps.drain()
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
    save_documents(out_dir, steps.fetched)
    return out_dir
