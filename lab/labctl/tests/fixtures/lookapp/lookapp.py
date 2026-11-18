"""A small styled application for the look-and-feel tests: Parts desk.

Server-rendered, two screens (`/parts`, `/parts/<number>`), synthetic data only. What matters is
its look, which is distinctive on purpose so a test can find it again in a build package:

- two style sheets (`/static/css/app.css` imports `base.css`) with unusual colours, a web font
  declared with `@font-face` (the file is a stand-in, not a real font), a sprite, an icon-font
  style glyph, a worded `content:` string, a comment with an e-mail address and a selector that
  holds a record number;
- a logo (SVG), a page icon and a sprite (PNG) under `/static/img/`: the interface's images;
- two pictures under `/uploads/avatars/`: user content, which must never be carried;
- every kind of interface text: labels, placeholders, button texts, headings, menu entries,
  table headers, option texts, radio labels, a validation message, a status message, tooltips
  (`title`) and `aria-label`;
- data: part numbers, names, e-mail addresses, dates and amounts in table cells and fields.

`serve()` starts it on a free port of 127.0.0.1; `flow(page, base)` is the scripted use.
"""

from __future__ import annotations

import struct
import threading
import zlib
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

PRIMARY = "#b4126e"  # the primary button
HEADER = "#0f4c3a"  # the header band
PAGE = "#fbf6ec"  # the page background
TEXT = "#2a2140"
ERROR = "#c2410c"
SUCCESS_BG = "#d9f2e3"
FONT = "Lab Grotesk"

PARTS = [
    ("PRT-20417", "Hydraulic coupling", "Margit Hartley", "m.hartley@northwind-tools.com",
     "2026-03-14", "1,284.50", "Open"),
    ("PRT-20418", "Pressure regulator", "Tobias Lindqvist", "t.lindqvist@northwind-tools.com",
     "2026-04-02", "318.00", "Closed"),
    ("PRT-20419", "Bearing housing", "Ines Okonkwo", "i.okonkwo@northwind-tools.com",
     "2026-04-19", "2,045.75", "Open"),
]  # fmt: skip
DATA_VALUES = [v for row in PARTS for v in row[:6]] + [
    "Hartley", "Lindqvist", "Okonkwo", "Margit", "Tobias", "Ines"]  # fmt: skip


def png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A valid one-colour PNG."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = b"\x00" + bytes(rgb) * width
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b""))  # fmt: skip


LOGO = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="32" viewBox="0 0 120 32">'
    '<rect width="120" height="32" rx="6" fill="#f2b705"/>'
    '<circle cx="16" cy="16" r="9" fill="#0f4c3a"/></svg>'
).encode()
FONT_FILE = b"wOF2" + b"\x00" * 60  # a stand-in: the browser refuses it and uses the fallback

BASE_CSS = f"""/* Parts desk base styles. Questions: styles@northwind-tools.com */
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: {PAGE}; color: {TEXT};
  font-family: "{FONT}", "Trebuchet MS", sans-serif; font-size: 16px; }}
h1 {{ font-family: Georgia, serif; font-size: 28px; font-weight: 400; margin: 0 0 18px; }}
a {{ color: #1d5fd1; }}
"""

APP_CSS = f"""@import "base.css";
@font-face {{
  font-family: "{FONT}";
  src: url("../fonts/labgrotesk-regular.woff2") format("woff2"),
       url(../fonts/labgrotesk-regular.woff) format("woff");
  font-weight: 400; font-style: normal;
}}
#topbar {{ display: flex; align-items: center; gap: 18px; padding: 12px 28px;
  background: {HEADER}; color: #ffffff; }}
#topbar .menu a {{ color: #ffe9a8; margin-right: 14px; text-decoration: none; }}
main {{ max-width: 1040px; margin: 0 auto; padding: 26px; }}
.btn {{ font: inherit; padding: 9px 22px; border: 2px solid #6b5b95; border-radius: 11px;
  background: #ffffff; color: {TEXT}; cursor: pointer; }}
.btn--primary {{ background: {PRIMARY}; border-color: {PRIMARY}; color: #ffffff; }}
input[type=text], input[type=search], select, textarea {{ font: inherit; padding: 8px 12px;
  border: 2px solid #6b5b95; border-radius: 7px; }}
label {{ font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; }}
table {{ width: 100%; border-collapse: collapse; background: #ffffff; }}
th {{ background: #e7def7; text-align: left; padding: 10px 14px; font-size: 14px; }}
td {{ padding: 10px 14px; border-bottom: 1px solid #d8cfe8; }}
.msg--error {{ color: {ERROR}; background: #fdebe1; padding: 8px 12px; border-radius: 7px; }}
.msg--ok {{ color: #14532d; background: {SUCCESS_BG}; padding: 8px 12px; border-radius: 7px; }}
.icon-ok::before {{ content: "\\2713"; margin-right: 6px; }}
label.required::after {{ content: " (required)"; color: {ERROR}; }}
.sprite {{ display: inline-block; width: 16px; height: 16px;
  background: url("../img/sprite.png?cache=1") no-repeat; }}
.flag {{ background-image: url(../img/sprite.png); }}
.tracker {{ background: url(https://stats.example.invalid/pixel.gif?user=20417); }}
tr[data-ref="PRT-20417"] td {{ font-weight: 700; }}
.avatar {{ width: 28px; height: 28px; border-radius: 50%; }}
.field {{ margin: 0 0 16px; display: grid; gap: 6px; max-width: 420px; }}
/*# sourceMappingURL=app.css.map */
"""


def _shell(title: str, body: str) -> bytes:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{escape(title)} - Parts desk</title>
<link rel="icon" href="/static/img/favicon.png">
<link rel="stylesheet" href="/static/css/app.css">
<style>.muted {{ color: #7c6f99; font-size: 13px; }}</style></head>
<body><header id="topbar"><img id="logo" src="/static/img/logo.svg" alt="Parts desk" width="120" height="32">
<nav class="menu"><a id="navParts" href="/parts">Parts</a><a id="navReports" href="/reports">Reports</a></nav>
<span class="sprite"></span>
<img class="avatar" src="/uploads/avatars/me.png" alt="" width="28" height="28">
<button id="btnHelp" class="btn" type="button" title="Open the help pages" aria-label="Help">?</button>
</header><main>{body}</main></body></html>""".encode()


def _list(query: str, saved: str | None) -> bytes:
    rows = "".join(
        f'<tr data-ref="{n}"><td class="part-no"><a id="open-{n}" href="/parts/{n}">{n}</a></td>'
        f'<td>{escape(name)}</td><td><img class="avatar" src="/uploads/avatars/{n[-2:]}.png" alt="" '
        f'width="28" height="28"> {escape(owner)}</td><td>{mail}</td><td>{due}</td>'
        f"<td>{amount}</td><td>{status}</td></tr>"
        for n, name, owner, mail, due, amount, status in PARTS
        if query.lower() in (n + name + owner).lower()
    )
    note = (f'<p id="savedNote" class="msg--ok" role="status"><span class="icon-ok"></span>'
            f"Part {saved} was saved.</p>" if saved else "")  # fmt: skip
    return _shell(
        "Parts",
        f"""<h1 id="heading">Parts on order</h1>{note}
<form id="searchForm" method="get" action="/parts"><label for="q">Search</label>
<input id="q" name="q" type="search" placeholder="Part number or owner" title="Search the parts list">
<button id="btnSearch" class="btn btn--primary" type="submit">Search</button>
<a id="btnNew" class="btn" href="/parts/PRT-20419">Newest part</a></form>
<p class="muted">Amounts are in euro.</p>
<table id="parts"><thead><tr><th>Part</th><th>Description</th><th>Owner</th><th>E-mail</th>
<th>Due</th><th>Amount</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>""",
    )


def _edit(number: str, error: bool) -> bytes:
    part = next((p for p in PARTS if p[0] == number), None)
    if part is None:
        return b""
    n, name, owner, _mail, due, _amount, status = part
    options = "".join(
        f'<option value="{code}"{" selected" if label == status else ""}>{label}</option>'
        for code, label in (("open", "Open"), ("on-hold", "On hold"), ("closed", "Closed"))
    )
    message = ('<p id="formError" class="msg--error" role="alert">A description is required.</p>'
               if error else "")  # fmt: skip
    return _shell(
        "Edit part",
        f"""<h1 id="heading">Edit part <span id="partNo">{n}</span></h1>
{message}<form id="partForm" method="post" action="/parts/{n}">
<div class="field"><label class="required" for="description">Description</label>
<input id="description" name="description" type="text" value="{escape(name)}" maxlength="60"></div>
<div class="field"><label for="owner">Owner</label>
<input id="owner" name="owner" type="text" value="{escape(owner)}" placeholder="First and last name"></div>
<div class="field"><label for="status">Status</label><select id="status" name="status">{options}</select></div>
<fieldset id="urgency"><legend>Urgency</legend>
<label for="urgency-low"><input type="radio" name="urgency" id="urgency-low" value="low" checked> Low</label>
<label for="urgency-high"><input type="radio" name="urgency" id="urgency-high" value="high"> High</label>
</fieldset>
<div class="field"><label for="due">Due date</label><input id="due" name="due" type="text" value="{due}"></div>
<div class="field"><label for="notes">Notes</label><textarea id="notes" name="notes" rows="3"></textarea></div>
<button id="btnSave" class="btn btn--primary" type="submit" title="Save this part">Save part</button>
<a id="btnBack" class="btn" href="/parts">Back to the list</a></form>""",
    )


STATIC = {
    "/static/css/app.css": ("text/css; charset=utf-8", APP_CSS.encode()),
    "/static/css/base.css": ("text/css; charset=utf-8", BASE_CSS.encode()),
    "/static/fonts/labgrotesk-regular.woff2": ("font/woff2", FONT_FILE),
    "/static/img/logo.svg": ("image/svg+xml", LOGO),
    "/static/img/favicon.png": ("image/png", png(16, 16, (242, 183, 5))),
    "/static/img/sprite.png": ("image/png", png(48, 16, (15, 76, 58))),
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # quiet
        return

    def _send(self, status: int, kind: str, body: bytes, location: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if url.path in STATIC:
            self._send(200, *STATIC[url.path])
        elif url.path.startswith("/uploads/avatars/") and url.path.endswith(".png"):
            self._send(200, "image/png", png(28, 28, (200, 30, 30)))
        elif url.path in ("/", "/parts"):
            self._send(200, "text/html; charset=utf-8",
                       _list((query.get("q") or [""])[0], (query.get("saved") or [None])[0]))  # fmt: skip
        elif url.path.startswith("/parts/"):
            page = _edit(url.path.rsplit("/", 1)[-1], "error" in query)
            self._send(200 if page else 404, "text/html; charset=utf-8", page)
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        number = urlsplit(self.path).path.rsplit("/", 1)[-1]
        if not (form.get("description") or [""])[0].strip():
            self._send(303, "text/plain", b"", f"/parts/{number}?error=1")
        else:
            self._send(303, "text/plain", b"", f"/parts?saved={number}")


def serve() -> SimpleNamespace:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop() -> None:
        server.shutdown()
        server.server_close()

    return SimpleNamespace(base=f"http://127.0.0.1:{server.server_address[1]}", stop=stop)


def flow(page) -> None:
    """Search, open a part, try to save it without a description, give one, save."""
    pause = 500
    page.wait_for_timeout(pause)
    page.fill("#q", "Hartley")
    page.click("#btnSearch")
    page.wait_for_timeout(pause)
    page.click("#open-PRT-20417")
    page.wait_for_timeout(pause)
    page.fill("#description", "")
    page.click("#btnSave")
    page.wait_for_timeout(pause)
    page.fill("#description", "Hydraulic coupling, long")
    page.select_option("#status", "closed")
    page.click("#btnSave")
    page.wait_for_timeout(pause)
