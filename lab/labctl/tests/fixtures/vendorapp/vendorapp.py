"""A small application for the carried-libraries tests: Lending shelf.

Server-rendered, one screen (`/shelf`), synthetic data only. What matters is what its pages load:

- `/node_modules/tinylib/dist/css/tinylib.css` and `.../js/tinylib.min.js`: a made-up third-party
  library with an MIT banner and a version (`TinyLib v1.2.3`); its style sheet uses an icon font
  `../fonts/tinyicons.woff` that states no licence (carried with the library, under its licence);
- `/node_modules/mystery/dist/mystery.min.js`: a library whose licence is not known (not carried);
- `/node_modules/leaky/leaky.min.js`: an MIT library with a planted access key (refused);
- `/static/js/shelf.js`: the application's own script (never carried by this rule);
- `/static/css/shelf.css`: the application's own style sheet, declaring two web fonts:
  `Shelf Sans` (`/static/fonts/shelfsans.woff`, a real WOFF 1 font whose name table says SIL
  Open Font License 1.1) and `Plain Grotesk` (`/static/fonts/plaingrotesk.ttf`, a real TrueType
  font with no licence in its name table: not carried unless `TWINLAB_LOOK_FONTS=bundle`).

The fonts are built here, in a few lines of `struct` and `zlib`, so the test needs no font file
and no font package: every glyph is a filled box, so a page set in them looks nothing like the
fallback.
"""

from __future__ import annotations

import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit

OFL = "This Font Software is licensed under the SIL Open Font License, Version 1.1."
FAMILY = "Shelf Sans"
PLAIN = "Plain Grotesk"
SECRET = "AKIA" + "QWERTYUIOPASDFGH"  # shaped like a cloud access key: the leaky library holds it


# --- a minimal TrueType font, and the same as WOFF 1 -----------------------------------------------


def name_table(records: dict[int, str]) -> bytes:
    items = sorted(records.items())
    strings, rows = b"", b""
    for name_id, text in items:
        raw = text.encode("utf-16-be")
        rows += struct.pack(">HHHHHH", 3, 1, 0x409, name_id, len(raw), len(strings))
        strings += raw
    return struct.pack(">HHH", 0, len(items), 6 + 12 * len(items)) + rows + strings


def _checksum(data: bytes) -> int:
    data += b"\0" * (-len(data) % 4)
    return sum(struct.unpack(f">{len(data) // 4}I", data)) & 0xFFFFFFFF


def _tables(family: str, licence: str | None) -> dict[bytes, bytes]:
    glyphs = 96  # .notdef and one box for each of U+0020..U+007E
    box = (struct.pack(">hhhhh", 1, 50, 0, 550, 700) + struct.pack(">H", 3) + struct.pack(">H", 0)
           + bytes([0x01] * 4) + struct.pack(">hhhh", 50, 500, 0, -500)
           + struct.pack(">hhhh", 0, 0, 700, 0))  # fmt: skip
    box += b"\0" * (-len(box) % 2)
    glyf = box * glyphs
    loca = struct.pack(f">{glyphs + 1}H", *[i * len(box) // 2 for i in range(glyphs + 1)])
    head = struct.pack(">IIIIHHqqhhhhHHhhh", 0x00010000, 0x00010000, 0, 0x5F0F3CF5, 0x000B, 1000,
                       0, 0, 0, 0, 600, 700, 0, 8, 2, 0, 0)  # fmt: skip
    hhea = struct.pack(">IhhhHhhhhhhhhhhhH", 0x00010000, 800, -200, 0, 600, 50, 50, 550, 1, 0, 0,
                       0, 0, 0, 0, 0, glyphs)  # fmt: skip
    maxp = struct.pack(">IHHHHHHHHHHHHHH", 0x00010000, glyphs, 4, 1, 0, 0, 2, 0, 0, 0, 0, 0, 0,
                       0, 0)  # fmt: skip
    os2 = struct.pack(">HhHHHhhhhhhhhhhh", 3, 600, 400, 5, 0, 650, 700, 0, 140, 650, 700, 0, 480,
                      50, 250, 0)  # fmt: skip
    os2 += bytes(10) + struct.pack(">IIII", 1, 0, 0, 0) + b"TLAB"
    os2 += struct.pack(">HHHhhhHHII", 0x40, 0x20, 0x7E, 800, -200, 0, 800, 200, 1, 0)
    os2 += struct.pack(">hhHHH", 500, 700, 0, 0x20, 1)
    hmtx = struct.pack(">Hh", 600, 50) * glyphs
    seg = struct.pack(">HHHH", 4, 4, 1, 0)  # segCountX2, searchRange, entrySelector, rangeShift
    fmt4 = (seg + struct.pack(">HH", 0x7E, 0xFFFF) + b"\0\0" + struct.pack(">HH", 0x20, 0xFFFF)
            + struct.pack(">hh", 1 - 0x20, 1) + struct.pack(">HH", 0, 0))  # fmt: skip
    sub = struct.pack(">HHH", 4, 6 + len(fmt4), 0) + fmt4
    cmap = struct.pack(">HHHHI", 0, 1, 3, 1, 12) + sub
    post = struct.pack(">IIhhIIIII", 0x00030000, 0, -100, 50, 0, 0, 0, 0, 0)
    names = {1: family, 2: "Regular", 3: f"{family} Regular 1.0", 4: f"{family} Regular",
             5: "Version 1.000", 6: family.replace(" ", "") + "-Regular",
             0: f"Copyright 2026 The {family} Project Authors"}  # fmt: skip
    if licence:
        names[13] = licence
        if "Open Font" in licence:
            names[14] = "https://openfontlicense.org"
    return {b"OS/2": os2, b"cmap": cmap, b"glyf": glyf, b"head": head, b"hhea": hhea,
            b"hmtx": hmtx, b"loca": loca, b"maxp": maxp, b"name": name_table(names),
            b"post": post}  # fmt: skip


def ttf(family: str, licence: str | None = None) -> bytes:
    """A valid TrueType font: every printable ASCII character is a filled box."""
    tables = _tables(family, licence)
    count = len(tables)
    power = 1 << (count.bit_length() - 1)
    head = struct.pack(">IHHHH", 0x00010000, count, power * 16, power.bit_length() - 1,
                       count * 16 - power * 16)  # fmt: skip
    offset = 12 + 16 * count
    directory, body = b"", b""
    for tag in sorted(tables):
        data = tables[tag]
        directory += tag + struct.pack(">III", _checksum(data), offset + len(body), len(data))
        body += data + b"\0" * (-len(data) % 4)
    return head + directory + body


def woff(family: str, licence: str | None = None) -> bytes:
    """The same font as WOFF 1: each table compressed with zlib."""
    tables = _tables(family, licence)
    count = len(tables)
    offset = 44 + 20 * count
    directory, body = b"", b""
    sfnt_size = 12 + 16 * count + sum(len(t) + (-len(t) % 4) for t in tables.values())
    for tag in sorted(tables):
        data = tables[tag]
        packed = zlib.compress(data)
        if len(packed) >= len(data):
            packed = data
        directory += tag + struct.pack(">IIII", offset + len(body), len(packed), len(data),
                                       _checksum(data))  # fmt: skip
        body += packed + b"\0" * (-len(packed) % 4)
    total = offset + len(body)
    header = b"wOFF" + struct.pack(">IIHHIHHIIIII", 0x00010000, total, count, 0, sfnt_size, 1, 0,
                                   0, 0, 0, 0, 0)  # fmt: skip
    return header + directory + body


# --- the application ---------------------------------------------------------------------------------

TINYLIB_CSS = """/*!
 * TinyLib v1.2.3 (https://tinylib.example.org)
 * Copyright 2026 The TinyLib Authors
 * Licensed under MIT
 */
@font-face { font-family: "TinyIcons"; src: url("../fonts/tinyicons.woff") format("woff"); }
.tl-icon { font-family: "TinyIcons"; }
.tl-badge { display: inline-block; padding: 2px 8px; border-radius: 9px; background: #2b6cb0;
  color: #ffffff; }
.tl-unused { background: url("../img/never-loaded.png"); }
"""
TINYLIB_JS = """/*! TinyLib v1.2.3 | MIT License | tinylib.example.org */
!function(w){"use strict";w.TinyLib={version:"1.2.3",badge:function(el){el.classList.add("tl-badge")}}}(window);
"""
MYSTERY_JS = """/*! Mystery v2.0.0 | (c) 2026 Somebody */
!function(w){w.Mystery={on:function(){return 1}}}(window);
"""
LEAKY_JS = f"""/*! Leaky v1.0.0 | MIT License */
!function(w){{w.Leaky={{key:"{SECRET}"}}}}(window);
"""
SHELF_JS = """document.addEventListener("DOMContentLoaded", function () {
  var el = document.getElementById("count");
  if (el && window.TinyLib) { window.TinyLib.badge(el); }
});
"""
SHELF_CSS = f"""@font-face {{ font-family: "{FAMILY}"; src: url("../fonts/shelfsans.woff") format("woff");
  font-weight: 400; font-style: normal; }}
@font-face {{ font-family: "{PLAIN}"; src: url("../fonts/plaingrotesk.ttf") format("truetype"); }}
body {{ margin: 0; font-family: "{FAMILY}", Georgia, serif; font-size: 18px; background: #fdfaf2;
  color: #1f2937; }}
#topbar {{ padding: 14px 24px; background: #234e52; color: #ffffff; }}
.note {{ font-family: "{PLAIN}", monospace; }}
main {{ padding: 24px; }}
"""
PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Shelf - Lending shelf</title>
<link rel="stylesheet" href="/node_modules/tinylib/dist/css/tinylib.css">
<link rel="stylesheet" href="/static/css/shelf.css">
<script src="/node_modules/tinylib/dist/js/tinylib.min.js"></script>
<script src="/node_modules/mystery/dist/mystery.min.js"></script>
<script src="/node_modules/leaky/leaky.min.js"></script>
<script src="/static/js/shelf.js"></script>
</head><body><header id="topbar"><span class="tl-icon">A</span> Lending shelf</header>
<main><h1 id="heading">Books on the shelf</h1>
<p class="note">Borrow for three weeks.</p>
<p>Books available: <span id="count">Seven</span></p>
<form id="search" method="get" action="/shelf"><label for="q">Title</label>
<input id="q" name="q" type="text"><button id="go" type="submit">Find</button></form>
</main></body></html>
"""

STATIC = {
    "/node_modules/tinylib/dist/css/tinylib.css": ("text/css", TINYLIB_CSS.encode()),
    "/node_modules/tinylib/dist/js/tinylib.min.js": ("application/javascript", TINYLIB_JS.encode()),
    "/node_modules/tinylib/dist/fonts/tinyicons.woff": ("font/woff", woff("TinyIcons")),
    "/node_modules/mystery/dist/mystery.min.js": ("application/javascript", MYSTERY_JS.encode()),
    "/node_modules/leaky/leaky.min.js": ("application/javascript", LEAKY_JS.encode()),
    "/static/js/shelf.js": ("application/javascript", SHELF_JS.encode()),
    "/static/css/shelf.css": ("text/css", SHELF_CSS.encode()),
    "/static/fonts/shelfsans.woff": ("font/woff", woff(FAMILY, OFL)),
    "/static/fonts/plaingrotesk.ttf": ("font/ttf", ttf(PLAIN)),
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # quiet
        return

    def _send(self, status: int, kind: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path in STATIC:
            self._send(200, *STATIC[path])
        elif path in ("/", "/shelf"):
            self._send(200, "text/html; charset=utf-8", PAGE.encode())
        else:
            self._send(404, "text/plain", b"not found")


def serve() -> SimpleNamespace:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop() -> None:
        server.shutdown()
        server.server_close()

    return SimpleNamespace(base=f"http://127.0.0.1:{server.server_address[1]}", stop=stop)


def flow(page) -> None:
    """Type a title and search."""
    page.wait_for_timeout(400)
    page.fill("#q", "Atlas")
    page.click("#go")
    page.wait_for_timeout(400)
