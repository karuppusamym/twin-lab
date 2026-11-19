"""The look of an application, for the build package: `apps/<app>/look/`.

A builder must make the demo application look like the original: the same colours, layout,
spacing, fonts and interface texts. The value-free page snapshots (`apps/<app>/pages/`) carry the
structure and the inline styles. This module adds, from the same recordings:

- `look/css/`: the application's own style sheets as it delivered them (from the HAR), and the
  inline `<style>` blocks of each screen under `look/css/inline/`;
- `look/theme.json`: design tokens (colours, fonts per role, control sizes, spacing, layout),
  computed in a headless browser from the sanitised snapshots with the carried style sheets;
- `look/screens/<screen>.png`: a picture of each screen, rendered from the sanitised snapshot
  (interface text kept, data masked) with the carried style sheets. Never the recorder's own
  screenshots: those show real data;
- `look/assets/`: the images of the interface (logo, icons, sprites), and with an explicit
  opt-in `look/fonts/`: the web font files;
- `look/texts.json`: per screen, which texts are literal and which are placeholders for data;
- `look/manifest.json`: every carried file, every font and image and why it is or is not
  carried, and the result of the privacy check.

Privacy rules (the brief repeats them):

- Style sheets are the application's code, not customer data: selectors, colours and sizes are
  kept as written. Addresses are not: `url(...)` and `@import` become a path inside `look/` or
  are dropped (never a host, never a query). Source-map comments are dropped. In strings and
  comments, e-mail addresses, numbers of three or more digits (strings only; font names are left
  alone) and names the recording shows as data are masked.
- Fonts are licensed: the files are not copied. `TWINLAB_LOOK_FONTS=bundle` copies those seen in
  the HAR; otherwise the manifest names each font, where it came from and a generic fallback.
- Images: only static files of the application's own origin that the interface uses (a style
  sheet, or an `<img>` or icon outside data cells), without a query, without a record number in
  the path, not under an upload folder, up to `har.MAX_BODY` bytes. Never user content.
  A query is accepted only when it names a version (`?v=3`).
- Pictures are rendered, never recorded; `check()` fails when a carried text or the text of a
  rendered picture holds a value the recording shows as data.
- `TWINLAB_PAGE_TEXT=strict` carries none of this; `TWINLAB_LOOK=off` switches it off.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import struct
import threading
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit

from labctl import sanitize

LOOK_DIR = "look"
MANIFEST = "manifest.json"
FORMAT = "twinlab-look/1"
FONTS_ENV = "TWINLAB_LOOK_FONTS"  # "bundle": copy the web font files seen in the HAR
SWITCH_ENV = "TWINLAB_LOOK"  # "off": carry no look at all
MAX_DATA_URI = 64 * 1024  # an image embedded in a style sheet, kept up to this many characters
MAX_TOTAL = 20 * 1024 * 1024  # all bundled images of one application
RENDER_HOST = "http://twinlab-look.invalid"  # nothing is ever asked of the network
VIEWPORT = {"width": 1280, "height": 720}

FONT_EXT = {".woff2": "woff2", ".woff": "woff", ".ttf": "truetype", ".otf": "opentype",
            ".eot": "embedded-opentype"}  # fmt: skip
IMAGE_EXT = {".png", ".gif", ".jpg", ".jpeg", ".svg", ".webp", ".ico", ".avif", ".bmp"}
GENERIC = ("serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-serif",
           "ui-sans-serif", "ui-monospace", "ui-rounded", "math", "emoji", "fangsong")  # fmt: skip
# Fonts every desktop system ships (or maps to one it ships): nothing to obtain.
SYSTEM_FONTS = {
    "arial": "sans-serif", "helvetica": "sans-serif", "helvetica neue": "sans-serif",
    "verdana": "sans-serif", "tahoma": "sans-serif", "trebuchet ms": "sans-serif",
    "segoe ui": "sans-serif", "calibri": "sans-serif", "roboto": "sans-serif",
    "-apple-system": "sans-serif", "blinkmacsystemfont": "sans-serif",
    "times new roman": "serif", "times": "serif", "georgia": "serif", "cambria": "serif",
    "garamond": "serif", "palatino": "serif", "courier new": "monospace", "courier": "monospace",
    "consolas": "monospace", "menlo": "monospace", "monaco": "monospace",
    "lucida console": "monospace",
}  # fmt: skip
# A folder with one of these names holds what users put there, not the interface.
UPLOAD_WORDS = sanitize.UPLOAD_WORDS
# A query that only names a version of a static file (`logo.png?v=3`).
VERSION_KEYS = {"v", "ver", "version", "rev", "cache", "cb", "t", "ts", "h", "hash", "_"}
DATA_PARENTS = {"td", "dd", "textarea", "output", "pre"}


class LookError(Exception):
    """The look cannot be carried as it is: a carried file holds a value known as data.
    `places` names where (never the value); the message, for the person at this machine, names
    the values too."""

    def __init__(self, message: str, places: list[str] | None = None):
        super().__init__(message)
        self.places = places or []


def enabled() -> bool:
    return (os.environ.get(SWITCH_ENV) or "").strip().lower() not in ("off", "0", "no", "false")


def bundle_fonts() -> bool:
    return (os.environ.get(FONTS_ENV) or "").strip().lower() in ("bundle", "1", "yes", "true")


# --- what the recording delivered -------------------------------------------------------------------


@dataclass
class Resource:
    """One file the application delivered (a GET answered 200 with a body), from the HAR."""

    url: str
    host: str
    path: str
    query: str
    mime: str
    body: bytes

    @property
    def key(self) -> str:
        return sanitize.route_pattern(self.path)

    @property
    def ext(self) -> str:
        return os.path.splitext(self.path)[1].lower()

    @property
    def kind(self) -> str:
        """css, font, image or other."""
        mime = self.mime
        if mime == "text/css" or self.ext == ".css":
            return "css"
        if "font" in mime or self.ext in FONT_EXT:
            return "font"
        if mime.startswith("image/") or self.ext in IMAGE_EXT:
            return "image"
        return "other"


def resources(har: dict) -> list[Resource]:
    """Every file in a HAR that could be part of the look, in the order it was first asked for."""
    out: list[Resource] = []
    seen: set[str] = set()
    for entry in (har.get("log") or {}).get("entries") or []:
        req, resp = entry.get("request") or {}, entry.get("response") or {}
        if str(req.get("method", "")).upper() != "GET" or resp.get("status") != 200:
            continue
        content = resp.get("content") or {}
        text = content.get("text")
        if text is None:
            continue
        url = str(req.get("url") or "")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or url in seen:
            continue
        if content.get("encoding") == "base64":
            try:
                body = base64.b64decode(text)
            except ValueError:
                continue
        else:
            body = str(text).encode("utf-8")
        mime = str(content.get("mimeType") or "").split(";")[0].strip().lower()
        res = Resource(url, parts.netloc.lower(), parts.path or "/", parts.query, mime, body)
        if res.kind != "other":
            seen.add(url)
            out.append(res)
    return out


def _file_name(path: str, taken: set[str]) -> str:
    """A file name for a carried file: the address's last part, record numbers masked, unique."""
    last = unquote(path.rstrip("/").rsplit("/", 1)[-1])
    # a static file named by a word keeps its name, as the page snapshots do (static_file_name)
    base = last if sanitize.static_file_name(unquote(path)) else sanitize.mask_identifiers(last)
    base = base or "file"
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-.") or "file"
    name, n = base[-80:], 1
    while name.lower() in taken:
        n += 1
        stem, ext = os.path.splitext(base[-76:])
        name = f"{stem}-{n}{ext}"
    taken.add(name.lower())
    return name


def _shown_path(path: str) -> str:
    """An address as the manifest names it: the path only, record numbers masked (a static file
    named by a word keeps its name, as in the page snapshots)."""
    return sanitize.mask_asset_url(sanitize.route_pattern(path))[-160:]


# --- style sheets -----------------------------------------------------------------------------------

_CSS_PART = re.compile(
    r"(?P<comment>/\*.*?\*/)"
    r"|(?P<import>@import\s+(?:url\(\s*)?[\"']?(?P<target>[^\"')\s;]+)[\"']?\s*\)?(?P<media>[^;]*);)"
    r"|(?P<url>url\(\s*(?P<q>[\"']?)(?P<addr>.*?)(?P=q)\s*\))"
    r"|(?P<string>\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*')",
    re.DOTALL | re.IGNORECASE,
)
_FONT_PROPERTY = re.compile(r"(?:^|[;{\s])(font-family|font|src)\s*:[^;{}]*$", re.IGNORECASE)


@dataclass
class CssRefs:
    """What a cleaned style sheet refers to."""

    imports: list[str] = field(default_factory=list)  # carried file names it imports
    dropped: list[str] = field(default_factory=list)  # addresses (shown paths) not carried
    images: list[str] = field(default_factory=list)  # resource urls of images it uses
    fonts: list[str] = field(default_factory=list)  # resource urls of font files it names
    embedded_fonts: int = 0  # font files embedded as data: addresses (dropped unless opted in)


def scrub(text: str, known: set[str] = frozenset(), numbers: bool = True) -> str:
    """Words in a style sheet's string or comment, with what could be data masked: e-mail
    addresses, with `numbers` any part with three or more digits, and Title-case words the
    recording shows as data."""
    text = sanitize.EMAIL.sub(lambda m: sanitize.mask(m.group()), text)
    if numbers:
        text = sanitize.mask_identifiers(text)
    if known:
        text = re.sub(
            r"(?<![\w\\])[A-Z][a-z'’-]+",
            lambda m: sanitize.mask(m.group()) if m.group().lower() in known else m.group(),
            text,
        )
    return text


def clean_css(
    css: str,
    base: str = "",
    lookup=None,
    place=None,
    known: set[str] = frozenset(),
    fonts: bool = False,
) -> tuple[str, CssRefs]:
    """A style sheet as the application wrote it, safe to carry (the rules in the module text).

    `base` is the sheet's own address, `lookup(url)` the `Resource` an address names (or None)
    and `place(resource)` where that resource is carried, relative to the sheet (or None when it
    is not carried). `fonts`: keep the font files (the opt-in). Selectors, colours, sizes and
    every declaration stay as they are.
    """
    refs = CssRefs()
    lookup = lookup or (lambda url: None)
    place = place or (lambda res: None)

    def address(raw: str) -> str:
        raw = raw.strip()
        if raw.startswith("#") or not raw:  # a reference inside the document (an SVG filter)
            return f"url({raw})"
        if raw.lower().startswith("data:"):
            kind = raw[5:].split(";")[0].split(",")[0].lower()
            if "font" in kind:
                refs.embedded_fonts += 1
                return f'url("{raw}")' if fonts else "url(#font-not-carried)"
            if kind.startswith("image/") and len(raw) <= MAX_DATA_URI:
                return f'url("{raw}")'
            return "url(#)"
        res = lookup(urljoin(base, raw))
        ext = os.path.splitext(urlsplit(raw).path)[1].lower()
        kind = res.kind if res is not None else (
            "font" if ext in FONT_EXT else "image" if ext in IMAGE_EXT else "other")  # fmt: skip
        where = place(res) if res is not None else None
        if res is not None and kind == "font":
            refs.fonts.append(res.url)
        elif res is not None and kind == "image":
            refs.images.append(res.url)
        if where:
            return f'url("{where}")'
        if kind == "font":
            return "url(#font-not-carried)"
        refs.dropped.append(_shown_path(urlsplit(urljoin(base, raw)).path))
        return "url(#)"

    def one(m: re.Match) -> str:
        if m.group("comment"):
            if "sourceMappingURL" in m.group():
                return ""
            return scrub(m.group(), known, numbers=False)
        if m.group("import"):
            res = lookup(urljoin(base, m.group("target")))
            where = place(res) if res is not None else None
            if where and res.kind == "css":
                refs.imports.append(where)
                return f'@import url("{where}"){m.group("media").rstrip()};'
            shown = _shown_path(urlsplit(urljoin(base, m.group("target"))).path)
            refs.dropped.append(shown)
            return f"/* @import not carried: {shown} */"
        if m.group("url"):
            return address(m.group("addr"))
        text = m.group("string")
        before = css[max(0, m.start() - 400) : m.start()]
        before = before[max(before.rfind(";"), before.rfind("{"), before.rfind("}")) + 1 :]
        if _FONT_PROPERTY.search(" " + before):  # a font's name: `"Segoe UI"`, local("Lato")
            return sanitize.EMAIL.sub(lambda e: sanitize.mask(e.group()), text)
        return text if sanitize.css_symbol(text) else scrub(text, known)

    return _CSS_PART.sub(one, css.replace("\r\n", "\n")), refs


def css_findings(css: str, data: Data) -> list[str]:
    """What a carried style sheet still holds that the recording shows as data, in the only
    places a style sheet has words: strings and comments (font names apart)."""
    found: list[str] = []
    for m in _CSS_PART.finditer(css):
        text = m.group("comment") or m.group("string")
        if not text:
            continue
        if m.group("string"):
            before = css[max(0, m.start() - 400) : m.start()]
            before = before[max(before.rfind(";"), before.rfind("{"), before.rfind("}")) + 1 :]
            if _FONT_PROPERTY.search(" " + before):
                continue
        found += data.findings(text, numbers=bool(m.group("string")))
    return found


_FONT_FACE = re.compile(r"@font-face\s*\{([^}]*)\}", re.IGNORECASE)
_DECL = re.compile(r"([a-z-]+)\s*:\s*([^;]+)", re.IGNORECASE)
_SRC = re.compile(
    r"url\(\s*[\"']?(?P<addr>[^\"')]+)[\"']?\s*\)(?:\s*format\(\s*[\"']?(?P<fmt>[\w-]+)[\"']?\s*\))?"
    r"|local\(\s*[\"']?(?P<local>[^\"')]+)[\"']?\s*\)",
    re.IGNORECASE,
)


def font_faces(css: str, base: str = "", lookup=None) -> list[dict]:
    """The web fonts a style sheet declares: family, weight, style and each file it names (its
    path, format and, when the HAR has it, size). A file is named, never copied here."""
    lookup = lookup or (lambda url: None)
    out: list[dict] = []
    for block in _FONT_FACE.findall(re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)):
        decl = {k.lower(): v.strip() for k, v in _DECL.findall(block)}
        family = decl.get("font-family", "").strip("\"' ")
        if not family:
            continue
        files, local = [], []
        for m in _SRC.finditer(decl.get("src", "")):
            if m.group("local"):
                local.append(m.group("local").strip())
                continue
            addr = m.group("addr").strip()
            if addr.lower().startswith("data:"):
                files.append({"path": "(embedded in the style sheet)", "embedded": True,
                              "format": m.group("fmt") or addr[5:].split(";")[0].split("/")[-1]})  # fmt: skip
                continue
            url = urljoin(base, addr)
            res = lookup(url)
            ext = os.path.splitext(urlsplit(url).path)[1].lower()
            files.append({"path": _shown_path(urlsplit(url).path),
                          "format": m.group("fmt") or FONT_EXT.get(ext, ext.lstrip(".") or "?"),
                          "bytes": len(res.body) if res is not None else None,
                          "in_har": res is not None, "_url": url})  # fmt: skip
        out.append({"family": family, "weight": decl.get("font-weight", "normal"),
                    "style": decl.get("font-style", "normal"), "files": files, "local": local})  # fmt: skip
    return out


def generic_fallback(family: str, stacks: list[str] = ()) -> str:
    """The closest generic family for a font: the generic its own stack ends with, else what the
    name says, else sans-serif."""
    low = family.strip("\"' ").lower()
    for stack in stacks:
        names = [n.strip("\"' ").lower() for n in stack.split(",")]
        if low in names:
            for n in reversed(names):
                if n in GENERIC:
                    return n
    if low in SYSTEM_FONTS:
        return SYSTEM_FONTS[low]
    if re.search(r"mono|code|courier|consol", low):
        return "monospace"
    if (
        re.search(r"(?<!sans[ -])serif|times|georgia|garamond|roman|slab", low)
        and "sans" not in low
    ):
        return "serif"
    return "sans-serif"


# --- images -----------------------------------------------------------------------------------------


def image_size(body: bytes, ext: str = "") -> tuple[int, int] | None:
    """(width, height) of a PNG, GIF, JPEG, WebP or SVG image, from its own header."""
    try:
        if body[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", body[16:24])
        if body[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", body[6:10])
        if body[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(body):
                if body[i] != 0xFF:
                    i += 1
                    continue
                marker = body[i + 1]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", body[i + 5 : i + 9])
                    return w, h
                i += 2 + struct.unpack(">H", body[i + 2 : i + 4])[0]
            return None
        if body[:4] == b"RIFF" and body[8:16] == b"WEBPVP8X":
            w = int.from_bytes(body[24:27], "little") + 1
            h = int.from_bytes(body[27:30], "little") + 1
            return w, h
        if ext == ".svg" or b"<svg" in body[:2000]:
            head = body[:4000].decode("utf-8", "replace")
            tag = re.search(r"<svg\b[^>]*>", head, re.DOTALL)
            if tag:
                w = re.search(r'\bwidth="([\d.]+)', tag.group())
                h = re.search(r'\bheight="([\d.]+)', tag.group())
                if w and h:
                    return round(float(w.group(1))), round(float(h.group(1)))
                box = re.search(r'viewBox="[\d.\s-]*?([\d.]+)[\s,]+([\d.]+)"', tag.group())
                if box:
                    return round(float(box.group(1))), round(float(box.group(2)))
    except (struct.error, ValueError, IndexError):
        return None
    return None


def interface_image(res: Resource, own_hosts: set[str], in_data: bool = False) -> str | None:
    """None when an image is part of the interface and may be carried, else why it is not.

    Carried: a static file of the application's own origin, asked for without a query (but one
    that only names a version: `?v=3`), with no
    record number in its path, not under a folder that holds what users put there, up to the
    size the analysis reads (`har.MAX_BODY`), and not shown inside a data cell."""
    from labctl import har

    if res.host not in own_hosts:
        return "served by another host"
    if res.query and not all(
        q.split("=", 1)[0].lower() in VERSION_KEYS for q in res.query.split("&") if q
    ):
        return "asked for with a query (not a static file)"
    if len(res.body) > har.MAX_BODY:
        return f"larger than {har.MAX_BODY} bytes"
    if declared:
        return None
    if res.key != res.path:
        return "a record number in its path (content of a record, not the interface)"
    folders = {w for part in res.path.lower().split("/")[:-1] for w in re.split(r"[^a-z]+", part)}
    if folders & UPLOAD_WORDS:
        return "under a folder for user content (" + ", ".join(sorted(folders & UPLOAD_WORDS)) + ")"
    if in_data:
        return "shown inside a data cell (content of a record, not the interface)"
    return None


DIALOG_SEP = "--dialog-"  # screens/<screen>--dialog-<id>.png: a dialog drawn open
_BACKDROP_HIDE = ".modal-backdrop{display:none!important}body.modal-open{overflow:auto!important}"


def dialogs_seen_open(recordings: list[Path]) -> dict[str, set[str]]:
    """Per address pattern, the dialogs (`#id`) some snapshot of the recordings shows open."""
    from labctl import har

    out: dict[str, set[str]] = {}
    for path in recordings:
        try:
            rec = har.load(path)
        except (OSError, ValueError):
            continue
        for step in rec.steps:
            if step.dom is None or not step.url.startswith("http"):
                continue
            html = step.dom.read_text(encoding="utf-8", errors="replace")
            pattern = sanitize.route_pattern(urlsplit(step.url).path or "/")
            found = {d for d in har.open_dialogs(html) if d.startswith("#")}
            out.setdefault(pattern, set()).update(found)
    return out


def _hide(idents: list[str]) -> str:
    return "".join('[id="' + d[1:] + '"]{display:none!important}' for d in idents)


def without_dialogs(html: str) -> str:
    """A page snapshot with every open dialog closed (hidden), for the screen's own picture."""
    from labctl import har

    shut = [d for d in har.open_dialogs(html) if d.startswith("#")]
    if not shut:
        return html
    return _inject(html, "<style>" + _hide(shut) + _BACKDROP_HIDE + "</style>")


def with_dialog_open(html: str, ident: str) -> str | None:
    """A page snapshot with one dialog drawn open (Bootstrap: `show`, displayed, a backdrop),
    every other dialog closed. None when the page has no element of that id."""
    from labctl import har

    m = re.search(r'<([A-Za-z][\w-]*)\b[^>]*\bid="' + re.escape(ident.lstrip("#")) + r'"[^>]*>',
                  html)  # fmt: skip
    if m is None:
        return None
    new = re.sub(r'\saria-hidden="true"', "", m.group(0))
    if re.search(r'\sclass="', new):
        new = re.sub(r'\sclass="([^"]*)"', lambda c: f' class="{c.group(1)} show"', new, count=1)
    if m.group(1).lower() == "dialog" and " open" not in new:
        new = new[:-1] + " open>"
    style = "display: block;"
    if re.search(r'\sstyle="', new):
        new = re.sub(r'\sstyle="([^"]*)"',
                     lambda s: ' style="' + re.sub(r"display\s*:\s*none;?", "", s.group(1))
                     + " " + style + '"', new, count=1)  # fmt: skip
    else:
        new = new[:-1] + f' style="{style}">'
    html = html[: m.start()] + new + html[m.end() :]
    backdrop = '<div class="modal-backdrop fade show"></div>'
    html = (
        html.replace("</body>", backdrop + "</body>", 1) if "</body>" in html else html + backdrop
    )
    others = [d for d in har.open_dialogs(html) if d.startswith("#") and d != ident]
    return _inject(html, "<style>" + _hide(others) + "</style>") if others else html


def _inject(html: str, block: str) -> str:
    head = re.search(r"</head>", html, re.IGNORECASE)
    if head is not None:
        return html[: head.start()] + block + html[head.start() :]
    return block + html


def interface_image_paths(recordings: list[Path]) -> set[str]:
    """The address paths (`/imgs/Lumia_1520.jpg`) of the images `write()` carries as interface
    images: static files of the application's own origin (`interface_image`) named by a word
    (`sanitize.static_file_name`). An answer field holding such a path names a carried file."""
    from labctl import har

    out: set[str] = set()
    for path in recordings:
        try:
            rec = har.load(path)
        except (OSError, ValueError):
            continue
        own = {urlsplit(s.url).netloc.lower() for s in rec.steps if s.url.startswith("http")}
        for res in resources(rec.har):
            if (res.kind == "image" and interface_image(res, own) is None
                    and sanitize.static_file_name(res.path)):  # fmt: skip
                out.add(res.path)
    return out


def names_image(value, images: set[str]) -> bool:
    """Whether an answer's value is the address of one of `images` (relative or absolute path)."""
    if not isinstance(value, str) or not value or len(value) > 300 or "://" in value:
        return False
    return "/" + value.split("?", 1)[0].lstrip("./") in images


def _missing_reason(src: str, found) -> str:
    """Why an image a page snapshot shows has no file: its address was masked in the snapshot
    (a record number, so the file it names is a record's), or the browser never asked for it."""
    path = urlsplit(src).path
    if re.search(r"9{3,}", path):
        shape = re.compile(
            "^"
            + re.sub(
                r"9+", lambda m: r"\d{%d}" % len(m.group()), re.escape(path.rsplit("/", 1)[-1])
            )
            + "$"
        )
        if any(shape.match(r.path.rsplit("/", 1)[-1]) for r in found if r.kind == "image"):
            return (
                "in the recording, but its address holds a record number, masked in the "
                "page snapshot: the file is a record's, not the interface's"
            )
    return "not in the recording (the browser did not ask for it)"


# --- the sanitised pages ----------------------------------------------------------------------------

_MASKED_TOKEN = re.compile(r"[Xx9]+(?:[^\w\s]+[Xx9]+)*")  # the sanitiser's shape of a value
_TYPED_TOKEN = re.compile(r"[A9]+(?:[^\w\s]+[A9]+)*")  # the recorder's shape of a typed value
TEXT_ATTRS = ("placeholder", "title", "aria-label", "alt")


def text_kind(text: str) -> str:
    """`literal` (the application's own words, to copy exactly), `data` (a placeholder: every
    word is a shape, letters x/X and digits 9, or the recorder's shape of a typed value, letters
    A and digits 9) or `mixed` (literal words around placeholders)."""
    tokens = [t.strip(".,;:!?()[]\"'") for t in text.split()]
    tokens = [t for t in tokens if re.search(r"\w", t)]
    if not tokens:
        return "literal"
    if len(tokens) == 1 and len(tokens[0]) == 1:
        return "literal"  # `x` on a close button, `A`
    if all(_TYPED_TOKEN.fullmatch(t) for t in tokens):
        return "data"  # `A-9`, `A A-A`: what a person typed, shown again by the page
    masked = [bool(_MASKED_TOKEN.fullmatch(t)) or (bool(_TYPED_TOKEN.fullmatch(t)) and "9" in t)
              for t in tokens]  # fmt: skip
    if all(masked):
        return "data"
    return "mixed" if any(masked) else "literal"


_LABEL_TAGS = sanitize.LABEL_TAGS | {"option", "optgroup"}


class _PageRead(HTMLParser):
    """Reads a sanitised page: its texts and where they are, its style sheets and images, and
    the codes its own markup gives its choices."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str]] = []  # (tag, selector)
        self.texts: list[dict] = []
        self.links: list[str] = []  # style sheet addresses, in order
        self.inline: list[str] = []  # inline <style> blocks
        self.images: list[tuple[str, bool, str]] = []  # (address, inside a data cell, where)
        self.codes: set[str] = set()
        self.labels: set[str] = set()  # texts of labels, buttons, headers, options, tooltips
        self.skip = 0

    @staticmethod
    def _sel(tag: str, a: dict) -> str:
        if a.get("id"):
            return f"{tag}#{a['id']}"
        classes = ".".join((a.get("class") or "").split()[:2])
        return tag + (f".{classes}" if classes else "")

    def _in_data(self) -> bool:
        return any(tag in DATA_PARENTS for tag, _ in self.stack)

    def _add(self, where: str, kind: str, text: str, label: bool = False) -> None:
        text = " ".join(text.split())
        if text and label:
            self.labels.add(text)
        if text and len(self.texts) < 2000:
            self.texts.append({"where": where, "kind": kind, "text": text[:300],
                               "is": text_kind(text)})  # fmt: skip

    def handle_starttag(self, tag: str, attrs) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        sel = self._sel(tag, a)
        rel = a.get("rel", "").lower()
        if tag == "link" and "stylesheet" in rel and a.get("href"):
            self.links.append(a["href"])
        elif tag == "link" and "icon" in rel and a.get("href") not in (None, "", "#"):
            self.images.append((a["href"], False, "the page's icon"))
        elif tag in ("img", "image", "use") or (tag == "input" and a.get("type") == "image"):
            src = a.get("src") or a.get("href") or a.get("xlink:href") or ""
            if src and not src.startswith("#"):
                self.images.append((src, self._in_data(), sel))
        for name in TEXT_ATTRS:
            if a.get(name):
                self._add(sel, name, a[name], tag in ("input", "select", "textarea", "button"))
        if tag == "input" and a.get("type", "").lower() in sanitize.BUTTON_TYPES and a.get("value"):
            self._add(sel, "value", a["value"], True)
        if tag == "option":
            self.codes |= sanitize.code_words(a.get("value", ""))
        elif tag == "label":
            self.codes |= sanitize.code_words(a.get("for", ""))
        elif tag == "input" and a.get("type", "").lower() in ("radio", "checkbox"):
            self.codes |= sanitize.code_words(a.get("id", ""), a.get("value", ""))
        if tag in ("script", "noscript", "template"):
            self.skip += 1
        if tag not in sanitize.VOID:
            self.stack.append((tag, sel))

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in sanitize.VOID and self.stack and self.stack[-1][0] == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "noscript", "template") and self.skip:
            self.skip -= 1
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data: str) -> None:
        if self.skip or not self.stack:
            return
        tag, sel = self.stack[-1]
        if tag == "style":
            self.inline.append(data)
        elif data.strip():
            where = sel
            if tag in sanitize.PASS_THROUGH and len(self.stack) > 1 and "#" not in sel:
                where = f"{self.stack[-2][1]} {sel}"
            label = any(t in _LABEL_TAGS for t, _ in self.stack)
            self._add(where, "title" if tag == "title" else "text", data, label)


def read_page(html: str) -> _PageRead:
    reader = _PageRead()
    reader.feed(html)
    reader.close()
    return reader


def page_pattern(html: str) -> str:
    """The address pattern a page snapshot was written for (its first line names it)."""
    m = re.match(r"<!-- (\S+): value-free snapshot", html)
    return m.group(1) if m else "/"


# --- what the recording shows as data ---------------------------------------------------------------


@dataclass
class Data:
    """The values the privacy pass knows as data, to check what is carried against.

    `names`: Title-case words the recording shows as data (data cells, field values, API
    answers) and the interface does not use itself, lower-case: `sanitize`'s own known set.
    `tokens`: every part of a recorded text or answer with the shape of data (an e-mail address,
    a date, an amount, anything with three or more digits)."""

    names: set[str] = field(default_factory=set)
    tokens: set[str] = field(default_factory=set)

    def findings(
        self, text: str, codes: set[str] = frozenset(), numbers: bool = True, label: bool = False
    ) -> list[str]:
        """The known values a text holds. `codes`: words the same page carries as the code of
        a choice (`sanitize.code_words`), which a text may repeat. `label`: the text of a label,
        button, header, option or a control's tooltip, where the sanitiser keeps a single word
        the data also shows (`Open the help pages`): there a name counts only next to another
        Title-case word, or when the text is nothing else."""
        found = []
        parts = text.split()
        names = [re.findall(r"(?<![\w])[A-Z][a-z'’-]+", p) for p in parts]
        words = [w for p in parts for w in sanitize.WORD.findall(p)]
        whole = bool(words) and all(w.lower() in self.names for w in words)
        for i, part in enumerate(parts):
            if _SHAPE.fullmatch(part):  # `A-9`, `Xxxx`: a shape, not a value
                continue
            bare = part.strip(".,;:!?()[]{}\"'<>*/")
            if numbers and bare in self.tokens:
                found.append(bare)
            beside = any(names[j] for j in (i - 1, i + 1) if 0 <= j < len(parts))
            for word in names[i]:
                if word.lower() in self.names and word.lower() not in codes:
                    if not label or beside or whole:
                        found.append(word)
        if any(
            not sanitize.EXAMPLE_EMAIL.search(m.group()) and not _SHAPE.fullmatch(m.group())
            for m in sanitize.EMAIL.finditer(text)
        ):
            found.append("an e-mail address")
        return found


class _RawText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs) -> None:
        if tag in ("script", "style"):
            self.skip += 1
        for name, value in attrs:
            if value and name.lower() in ("value", *TEXT_ATTRS):
                self.parts.append(value)

    def handle_endtag(self, tag) -> None:
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data) -> None:
        if not self.skip:
            self.parts.append(data)


# A shape, not a value: the recorder writes a typed value as `A-9` (letters A, digits 9), the
# sanitiser a masked one as `Xxx-999`.
_SHAPE = re.compile(r"[AXx9\W_]+")


def _data_tokens(texts) -> set[str]:
    out: set[str] = set()
    for text in texts:
        for part in str(text).split():
            if sanitize._mask_data_tokens(part) != part and not _SHAPE.fullmatch(part):
                out.add(part.strip(".,;:!?()[]{}\"'<>*/"))
    return out


def _strings(value, out: list[str], cap: int = 20000) -> None:
    if len(out) >= cap:
        return
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            _strings(v, out, cap)
    elif isinstance(value, list):
        for v in value[:500]:
            _strings(v, out, cap)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        out.append(str(value))


def known_data(recordings: list[Path]) -> Data:
    """The data the recordings show (see `Data`), with the sanitiser's own knowledge."""
    from labctl import har

    data = Data()
    for path in recordings:
        try:
            rec = har.load(path)
        except (OSError, ValueError):
            continue
        entries = har.exchanges(rec.har)
        doms = [s.dom.read_text(encoding="utf-8", errors="replace") for s in rec.steps
                if s.dom is not None]  # fmt: skip
        known, _vocab = har.page_knowledge(doms, entries)
        data.names |= known
        texts: list[str] = []
        for html in doms + [e.resp_text for e in entries
                            if e.resp_text and e.resp_mime == "text/html"]:  # fmt: skip
            raw = _RawText()
            raw.feed(html)
            raw.close()
            texts += raw.parts
        for e in entries:
            if e.resp_json is not None:
                _strings(e.resp_json, texts)
        data.tokens |= _data_tokens(texts)
    data.tokens.discard("")
    return data


# --- rendering --------------------------------------------------------------------------------------

LOOK_JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const sel = (el) => {
    let s = el.localName;
    if (el.id) return s + "#" + el.id;
    const c = Array.from(el.classList).slice(0, 2).join(".");
    return c ? s + "." + c : s;
  };
  const box = (s, p) => [s[p + "Top"], s[p + "Right"], s[p + "Bottom"], s[p + "Left"]].join(" ");
  // a background image or gradient: the element's own, else that of a child that covers it
  // (a footer whose inner row carries the gradient)
  const bgImage = (el, depth) => {
    const own = getComputedStyle(el).backgroundImage;
    if (own && own !== "none") return own;
    if (depth >= 2) return "";
    const r = el.getBoundingClientRect();
    for (const c of Array.from(el.children).slice(0, 8)) {
      const b = c.getBoundingClientRect();
      if (r.width * r.height > 0 && b.width * b.height >= 0.9 * r.width * r.height) {
        const got = bgImage(c, depth + 1);
        if (got) return got;
      }
    }
    return "";
  };
  const pick = (el) => {
    const s = getComputedStyle(el);
    return {
      color: s.color, background: s.backgroundColor, background_image: bgImage(el, 0),
      font_family: s.fontFamily,
      font_size: s.fontSize, font_weight: s.fontWeight, font_style: s.fontStyle,
      line_height: s.lineHeight, letter_spacing: s.letterSpacing,
      text_transform: s.textTransform, text_align: s.textAlign,
      text_decoration: s.textDecorationLine, padding: box(s, "padding"), margin: box(s, "margin"),
      border: s.borderTopWidth + " " + s.borderTopStyle + " " + s.borderTopColor,
      border_bottom: s.borderBottomWidth + " " + s.borderBottomStyle + " " + s.borderBottomColor,
      radius: s.borderTopLeftRadius, shadow: s.boxShadow, display: s.display, gap: s.gap,
    };
  };
  const kinds = {
    body: "body", h1: "h1", h2: "h2", h3: "h3", paragraph: "p", label: "label, legend",
    input: "input:not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button])" +
      ":not([type=hidden]):not([type=reset]):not([type=image]):not([type=file]), textarea",
    select: "select",
    button: "button, input[type=submit], input[type=button], input[type=reset], [role=button], " +
      "a.btn, a.button",
    link: "a[href]:not(header a, nav a, [role=navigation] a, .btn, .button, [role=button])",
    nav_link: "header a[href], nav a[href], [role=navigation] a[href]",
    table: "table", th: "th", td: "td",
    header: "header, [role=banner], #header, #topbar, .navbar, .topbar, .app-header",
    nav: "nav, [role=navigation], .nav, .menu, .sidebar", main: "main, [role=main]",
    footer: "footer, [role=contentinfo], [id^=foot], .footer", form: "form", fieldset: "fieldset",
    dialog: "dialog, [role=dialog], .dialog, .modal",
    error: "[role=alert], .error, .alert-danger, .invalid-feedback, [class*=error], [class*=danger]",
    success: "[role=status], .success, .notice, .alert-success, [class*=success]",
  };
  const roles = {};
  for (const [kind, q] of Object.entries(kinds)) {
    const groups = new Map();
    let els = [];
    try { els = Array.from(document.querySelectorAll(q)).filter(vis).slice(0, 300); } catch (e) {}
    for (const el of els) {
      const style = pick(el), key = JSON.stringify(style);
      let g = groups.get(key);
      if (!g) {
        const r = el.getBoundingClientRect();
        g = {count: 0, sample: sel(el), classes: Array.from(el.classList).slice(0, 4),
             type: el.getAttribute("type") || "", width: Math.round(r.width),
             height: Math.round(r.height), style};
        groups.set(key, g);
      }
      g.count += 1;
    }
    roles[kind] = Array.from(groups.values()).sort((a, b) => b.count - a.count).slice(0, 4);
  }
  const palette = {};
  const note = (c, as) => {
    if (!c || c === "rgba(0, 0, 0, 0)" || c === "transparent") return;
    const p = palette[c] || (palette[c] = {uses: 0, as: {}});
    p.uses += 1; p.as[as] = (p.as[as] || 0) + 1;
  };
  const families = {};
  for (const el of Array.from(document.querySelectorAll("body, body *")).slice(0, 4000)) {
    if (!vis(el)) continue;
    const s = getComputedStyle(el);
    note(s.backgroundColor, "background");
    if (Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.data.trim())
        || /^(input|select|textarea|button)$/.test(el.localName)) {
      note(s.color, "text");
      families[s.fontFamily] = (families[s.fontFamily] || 0) + 1;
    }
    if (parseFloat(s.borderTopWidth) > 0 && s.borderTopStyle !== "none") note(s.borderTopColor, "border");
    else if (parseFloat(s.borderBottomWidth) > 0 && s.borderBottomStyle !== "none")
      note(s.borderBottomColor, "border");
  }
  const regions = [];
  const q = "body > *, header, nav, main, aside, footer, form, table, [role=dialog], " +
    "[role=banner], [role=main], [role=navigation]";
  for (const el of Array.from(document.querySelectorAll(q)).filter(vis).slice(0, 40)) {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    if (r.width < 40 || r.height < 16) continue;
    regions.push({selector: sel(el), x: Math.round(r.x), y: Math.round(r.y + window.scrollY),
                  width: Math.round(r.width), height: Math.round(r.height), display: s.display,
                  position: s.position, max_width: s.maxWidth});
  }
  const loaded = [];
  try { document.fonts.forEach((f) => { if (f.status === "loaded") loaded.push(f.family.replace(/["']/g, "")); }); }
  catch (e) {}
  const main = document.querySelector("main, [role=main]") || document.body;
  // What the picture shows, one text per line, as written (before any text-transform).
  const shown = [];
  const walk = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    const p = n.parentElement;
    if (!n.data.trim() || !p || /^(style|script|title|noscript)$/.test(p.localName)) continue;
    if (p.localName === "option" || vis(p)) shown.push(n.data.replace(/\s+/g, " ").trim());
  }
  for (const el of document.querySelectorAll("[placeholder], [alt], input[value], [title]")) {
    if (!vis(el)) continue;
    for (const a of ["placeholder", "alt", "value", "title"]) {
      const v = el.getAttribute(a);
      if (v && v.trim() && (a !== "value" || el.localName === "input")) shown.push(v.trim());
    }
  }
  return {
    roles, palette, families, regions, fonts_loaded: loaded,
    page: {width: document.documentElement.clientWidth,
           height: document.documentElement.scrollHeight,
           content_width: Math.round(main.getBoundingClientRect().width),
           content_max_width: getComputedStyle(main).maxWidth},
    text: shown.join("\n"),
  };
}
"""


@dataclass
class Rendered:
    png: bytes
    look: dict  # LOOK_JS's answer without its text
    text: str  # the text the picture shows
    asked: list[str]  # addresses the page asked for that are not carried


def _answer(doc: str, html: str, files: dict[str, tuple[bytes, str]], asked: list[str]):
    """Answers a rendered page's requests: the page itself, then only carried files."""

    def handle(route) -> None:
        request = route.request
        if request.url == doc and request.resource_type == "document":
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
            return
        key = sanitize.route_pattern(unquote(urlsplit(request.url).path))
        ours = request.url.startswith(RENDER_HOST)
        hit = files.get(key) if ours else None
        carried = re.search(r"/(?:assets|fonts|css)/[^/]+$", key)
        if hit is None and carried and ours:
            hit = files.get(carried.group())  # a path inside look/, named by a carried sheet
        if hit is None:
            asked.append(key)
            route.abort()
        else:
            route.fulfill(status=200, content_type=hit[1], body=hit[0])

    return handle


def _render_all(pages: dict[str, tuple[str, str]], files: dict[str, tuple[bytes, str]],
                viewport: dict) -> dict[str, Rendered]:  # fmt: skip
    """Each page (`name -> (address pattern, sanitised html)`) rendered in headless Chromium
    with only the carried `files` (`address key -> (bytes, type)`); nothing reaches a network."""
    from playwright.sync_api import sync_playwright

    out: dict[str, Rendered] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport=viewport, device_scale_factor=1,
                                          service_workers="block")  # fmt: skip
            for name, (pattern, html) in pages.items():
                page = context.new_page()
                doc = RENDER_HOST + quote(pattern.replace("{", "").replace("}", ""))
                asked: list[str] = []
                page.route("**/*", _answer(doc, html, files, asked))
                try:
                    page.goto(doc, wait_until="load", timeout=20000)
                    page.wait_for_timeout(150)
                    look = page.evaluate(LOOK_JS)
                    png = page.screenshot(full_page=True, timeout=20000)
                    out[name] = Rendered(png, look, str(look.pop("text", "")), sorted(set(asked)))
                except Exception:  # noqa: BLE001 - one page that cannot be drawn: no picture of it
                    continue
                finally:
                    page.close()
        finally:
            browser.close()
    return out


def render(pages: dict[str, tuple[str, str]], files: dict[str, tuple[bytes, str]],
           viewport: dict | None = None) -> tuple[dict[str, Rendered], str | None]:  # fmt: skip
    """(`name -> Rendered`, None), or ({}, why not) when no headless browser can be started.
    Runs in a thread of its own, so it also works under a running event loop (the portal)."""
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return {}, "Playwright is not installed on the machine that made the brief"
    result: dict = {}

    def work() -> None:
        try:
            result["ok"] = _render_all(pages, files, viewport or VIEWPORT)
        except Exception as exc:  # noqa: BLE001 - no browser, or it failed: said in the manifest
            result["error"] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"

    thread = threading.Thread(target=work, name="twinlab-look", daemon=True)
    thread.start()
    thread.join(timeout=300)
    if "ok" in result:
        return result["ok"], None
    return {}, result.get("error") or "the headless browser did not finish in time"


# --- design tokens ----------------------------------------------------------------------------------


def colour(value: str) -> str:
    """`rgb(22, 50, 79)` -> `#16324f`; a colour with transparency stays `rgba(...)`."""
    m = re.fullmatch(r"rgba?\(\s*(\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\s*\)", value.strip())
    if not m:
        return value.strip()
    r, g, b = (int(m.group(i)) for i in (1, 2, 3))
    alpha = float(m.group(4)) if m.group(4) is not None else 1.0
    if alpha >= 1:
        return f"#{r:02x}{g:02x}{b:02x}"
    return f"rgba({r}, {g}, {b}, {alpha:g})"


def _clear(value: str) -> bool:
    return value in ("", "transparent", "rgba(0, 0, 0, 0)")


def background_image(value: str) -> str:
    """A computed `background-image` as a token: gradients with hex colours, an image's address
    as the path of the carried file (never the rendering's host)."""
    value = re.sub(r"rgba?\([^)]*\)", lambda m: colour(m.group()), value or "")
    value = re.sub(r'url\("?' + re.escape(RENDER_HOST) + r'([^")]*)"?\)', r"url(\1)", value)
    return value.strip()


def _style(group: dict) -> dict:
    """One group's computed style, colours as hex, nothing empty."""
    out = {}
    for key, value in (group.get("style") or {}).items():
        if key in ("color", "background"):
            value = "transparent" if _clear(value) else colour(value)
        elif key == "background_image":
            value = background_image(value)
        elif key in ("border", "border_bottom"):
            width, _, rest = value.partition(" ")
            kind, _, col = rest.partition(" ")
            value = "none" if kind == "none" or width == "0px" else f"{width} {kind} {colour(col)}"
        if value not in ("", "normal", "none", "0px", "0px 0px 0px 0px") or key in (
                "border", "background", "padding"):  # fmt: skip
            out[key] = value
    out["width"], out["height"] = group.get("width"), group.get("height")
    return out


def _merge(looks: dict[str, dict]) -> dict[str, list[dict]]:
    """Per kind of element, its style variants over every screen, most used first."""
    merged: dict[str, dict[str, dict]] = {}
    for screen, look in sorted(looks.items()):
        for kind, groups in (look.get("roles") or {}).items():
            for g in groups:
                style = _style(g)
                key = json.dumps({k: v for k, v in style.items() if k not in ("width", "height")},
                                 sort_keys=True)  # fmt: skip
                item = merged.setdefault(kind, {}).setdefault(
                    key, {"uses": 0, "example": g.get("sample"), "screens": [],
                          "classes": g.get("classes") or [], "type": g.get("type") or "",
                          **style})  # fmt: skip
                item["uses"] += int(g.get("count") or 0)
                if screen not in item["screens"]:
                    item["screens"].append(screen)
    return {kind: sorted(groups.values(), key=lambda g: (-g["uses"], str(g["example"])))
            for kind, groups in merged.items()}  # fmt: skip


def _font(variant: dict | None) -> dict | None:
    if not variant:
        return None
    keys = ("font_family", "font_size", "font_weight", "font_style", "line_height",
            "letter_spacing", "text_transform", "color")  # fmt: skip
    return {k.removeprefix("font_"): variant[k] for k in keys if k in variant}


def _control(variant: dict | None) -> dict | None:
    if not variant:
        return None
    keys = ("background", "background_image", "color", "border", "border_bottom", "radius",
            "padding", "margin", "height", "font_size", "font_weight", "shadow",
            "text_transform")  # fmt: skip
    return {k: variant[k] for k in keys if k in variant}


_PRIMARY = re.compile(r"primary|submit|cta|main|confirm|success", re.IGNORECASE)


def _buttons(variants: list[dict]) -> tuple[dict | None, dict | None]:
    """(primary, secondary): the primary is the variant its class or type names, else the one
    with a filled, coloured background; the secondary the most used other one."""
    if not variants:
        return None, None

    def filled(v: dict) -> bool:
        m = re.fullmatch(r"#(..)(..)(..)", v.get("background", ""))
        if not m:
            return False
        r, g, b = (int(x, 16) for x in m.groups())
        return max(r, g, b) - min(r, g, b) > 40  # a colour, not white, grey or black

    primary = next((v for v in variants if any(_PRIMARY.search(c) for c in v["classes"])), None)
    primary = primary or next((v for v in variants if filled(v)), None)
    primary = primary or next((v for v in variants if v["type"] == "submit"), None)
    secondary = next((v for v in variants if v is not primary), None)
    return primary, secondary


def theme(looks: dict[str, dict], viewport: dict | None = None) -> dict:
    """Design tokens from what `LOOK_JS` measured on each rendered screen."""
    roles = _merge(looks)

    def first(kind: str) -> dict | None:
        return (roles.get(kind) or [None])[0]

    body, header, th, td = first("body"), first("header"), first("th"), first("td")
    inp, link, error, success = first("input"), first("link"), first("error"), first("success")
    primary, secondary = _buttons(roles.get("button") or [])
    colours = {
        "page_background": (body or {}).get("background"),
        "text": (body or {}).get("color"),
        "heading": (first("h1") or first("h2") or {}).get("color"),
        "link": (link or {}).get("color"),
        "menu_link": (first("nav_link") or {}).get("color"),
        "border": ((inp or {}).get("border") or "").split(" ")[-1] or None,
        "header_background": (header or {}).get("background"),
        "header_background_image": (header or {}).get("background_image"),
        "footer_background": (first("footer") or {}).get("background"),
        "footer_background_image": (first("footer") or {}).get("background_image"),
        "page_background_image": (body or {}).get("background_image"),
        "header_text": (header or {}).get("color"),
        "table_header_background": (th or {}).get("background"),
        "table_row_border": ((td or {}).get("border_bottom") or "").split(" ")[-1] or None,
        "primary_button_background": (primary or {}).get("background"),
        "primary_button_text": (primary or {}).get("color"),
        "secondary_button_background": (secondary or {}).get("background"),
        "secondary_button_text": (secondary or {}).get("color"),
        "error_text": (error or {}).get("color"),
        "error_background": (error or {}).get("background"),
        "success_text": (success or {}).get("color"),
        "success_background": (success or {}).get("background"),
    }
    palette: dict[str, dict] = {}
    families: dict[str, int] = {}
    regions, widths = [], []
    for screen, look in sorted(looks.items()):
        for raw, item in (look.get("palette") or {}).items():
            p = palette.setdefault(colour(raw), {"uses": 0, "as": {}})
            p["uses"] += int(item.get("uses") or 0)
            for use, n in (item.get("as") or {}).items():
                p["as"][use] = p["as"].get(use, 0) + int(n)
        for stack, n in (look.get("families") or {}).items():
            families[stack] = families.get(stack, 0) + int(n)
        page = look.get("page") or {}
        if page.get("content_width"):
            widths.append({"screen": screen, "content_width": page["content_width"],
                           "content_max_width": page.get("content_max_width"),
                           "page_height": page.get("height")})  # fmt: skip
        regions += [{"screen": screen, **r} for r in (look.get("regions") or [])[:12]]
    fonts = {
        role: _font(first(kind))
        for role, kind in (
            ("body", "body"),
            ("h1", "h1"),
            ("h2", "h2"),
            ("h3", "h3"),
            ("label", "label"),
            ("input", "input"),
            ("select", "select"),
            ("button", "button"),
            ("link", "link"),
            ("menu_link", "nav_link"),
            ("table_header", "th"),
            ("table_cell", "td"),
            ("message", "error"),
        )  # fmt: skip
        if first(kind)
    }
    main, form = first("main"), first("form")
    return {
        "format": FORMAT,
        "computed_from": "the sanitised page snapshots, rendered in headless Chromium with the "
        "carried style sheets (computed styles; no text is read)",
        "viewport": viewport or VIEWPORT,
        "colors": {k: v for k, v in colours.items() if v and v != "transparent"},
        "palette": [{"color": c, "uses": p["uses"], "as": sorted(p["as"], key=lambda k: -p["as"][k])}
                    for c, p in sorted(palette.items(), key=lambda x: (-x[1]["uses"], x[0]))[:24]],
        "fonts": fonts,
        "font_stacks": [s for s, _ in sorted(families.items(), key=lambda x: (-x[1], x[0]))],
        "controls": {k: v for k, v in {
            "input": _control(inp), "select": _control(first("select")),
            "button_primary": _control(primary), "button_secondary": _control(secondary),
            "table_header": _control(th), "table_cell": _control(td),
            "message_error": _control(error), "message_success": _control(success),
            "dialog": _control(first("dialog")), "fieldset": _control(first("fieldset")),
        }.items() if v},
        "spacing": {k: v for k, v in {
            "main_padding": (main or {}).get("padding"), "main_margin": (main or {}).get("margin"),
            "form_padding": (form or {}).get("padding"), "form_gap": (form or {}).get("gap"),
            "heading_margin": (first("h1") or {}).get("margin"),
            "paragraph_margin": (first("paragraph") or {}).get("margin"),
            "label_margin": (first("label") or {}).get("margin"),
            "header_padding": (header or {}).get("padding"),
        }.items() if v},
        "layout": {"pages": widths, "regions": regions},
        "variants": {kind: [{k: v for k, v in g.items() if k != "type"} for g in groups[:3]]
                     for kind, groups in sorted(roles.items()) if groups},
    }  # fmt: skip


def families_of(stacks: list[str]) -> list[str]:
    """Every font family the stacks name, in order of first use, generic names left out."""
    out: list[str] = []
    for stack in stacks:
        for name in stack.split(","):
            name = name.strip().strip("\"'")
            if name and name.lower() not in GENERIC and name not in out:
                out.append(name)
    return out


# --- writing the look -------------------------------------------------------------------------------


def look_dir(root: Path, app_id: str) -> Path:
    return Path(root) / "apps" / app_id / LOOK_DIR


def _viewport(recordings: list[Path]) -> dict:
    for path in recordings:
        meta = Path(path) / "recording.json"
        if meta.is_file():
            try:
                view = (json.loads(meta.read_text(encoding="utf-8")) or {}).get("viewport") or {}
                if int(view.get("width") or 0) > 0 and int(view.get("height") or 0) > 0:
                    return {"width": int(view["width"]), "height": int(view["height"])}
            except (ValueError, TypeError, OSError):
                continue
    return dict(VIEWPORT)


def _write(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8", newline="\n")
    else:
        path.write_bytes(data)


def _json(value) -> str:
    return json.dumps(value, indent=1, ensure_ascii=False) + "\n"


def write(root: Path, app_id: str, recordings: list[Path] | None = None) -> dict | None:
    """Write `apps/<app>/look/` from the app's recordings and its page snapshots, and return the
    manifest (None when nothing is carried: no page snapshot, strict mode, or switched off).

    Raises `LookError` when something that would be carried holds a value the recordings show as
    data: nothing is left in `look/` then.
    """
    from labctl import har

    root = Path(root)
    out = look_dir(root, app_id)
    shutil.rmtree(out, ignore_errors=True)
    pages_dir = root / "apps" / app_id / "pages"
    page_files = sorted(pages_dir.glob("*.html")) if pages_dir.is_dir() else []
    if not enabled() or sanitize.page_text_mode() == "strict" or not page_files:
        return None
    if recordings is None:
        from labctl import factory

        recordings = factory.discover(root, app_id).recordings
    data = known_data(recordings)

    # What the recordings delivered, and which hosts are the application's own.
    found: dict[str, Resource] = {}
    own_hosts: set[str] = set()
    for path in recordings:
        try:
            rec = har.load(path)
        except (OSError, ValueError):
            continue
        own_hosts |= {urlsplit(s.url).netloc.lower() for s in rec.steps if s.url.startswith("http")}
        docs = {
            urlsplit(str((e.get("request") or {}).get("url") or "")).netloc.lower()
            for e in (rec.har.get("log") or {}).get("entries") or []
            if "html" in str(((e.get("response") or {}).get("content") or {}).get("mimeType"))
        }
        own_hosts |= docs if not rec.steps else set()
        for res in resources(rec.har):
            found.setdefault(res.url, res)
    by_key: dict[str, Resource] = {}
    for res in found.values():
        by_key.setdefault(res.key, res)

    def lookup(url: str) -> Resource | None:
        return found.get(url) or by_key.get(sanitize.route_pattern(urlsplit(url).path or "/"))

    pages = {p.stem: p.read_text(encoding="utf-8") for p in page_files}
    reads = {name: read_page(html) for name, html in pages.items()}
    patterns = {name: page_pattern(html) for name, html in pages.items()}

    # Images: which the interface uses, and which of those are carried.
    taken: set[str] = set()
    images: dict[str, dict] = {}
    total = 0

    def image(res: Resource, where: str, in_data: bool = False) -> dict:
        nonlocal total
        ikey = res.host + res.path  # one file, whatever version its address names
        item = images.get(ikey)
        if item is not None and not in_data and item["reason"].startswith("shown inside a data"):
            # first met in a data cell (the cart's row), also shown as the interface shows it
            del images[ikey]
            item = None
        if item is None:
            why = interface_image(res, own_hosts, in_data)
            if why is None and res.ext == ".svg" and data.findings(
                    res.body.decode("utf-8", "replace")):  # fmt: skip
                why = "holds text the recording shows as data"
            if why is None and total + len(res.body) > MAX_TOTAL:
                why = f"the carried images already hold {MAX_TOTAL} bytes"
            size = image_size(res.body, res.ext)
            item = images[ikey] = {
                "path": _shown_path(res.path), "type": res.mime or res.ext.lstrip("."),
                "bytes": len(res.body), "width": size[0] if size else None,
                "height": size[1] if size else None, "where": [], "bundled": why is None,
                "file": None, "reason": why or "a static file of the interface", "_res": res,
            }  # fmt: skip
            if why is None:
                total += len(res.body)
                item["file"] = f"assets/{_file_name(res.path, taken)}"
        if where not in item["where"]:
            item["where"].append(where)
        return item

    # Fonts and style sheets.
    font_taken: set[str] = set()
    font_files: dict[str, str] = {}  # resource url -> carried file (the opt-in only)
    keep_fonts = bundle_fonts()

    def place_from(prefix: str):
        def place(res: Resource) -> str | None:
            if res.kind == "css":
                name = css_names.get(res.url)
                return name if name else None
            if res.kind == "font":
                if not keep_fonts:
                    return None
                if res.url not in font_files:
                    font_files[res.url] = f"fonts/{_file_name(res.path, font_taken)}"
                return prefix + font_files[res.url]
            if res.kind == "image":
                item = image(res, "a style sheet")
                return prefix + item["file"] if item["bundled"] else None
            return None

        return place

    css_taken: set[str] = set()
    sheets = [r for r in found.values() if r.kind == "css"]
    css_names = {r.url: _file_name(r.path, css_taken) for r in sheets}
    css_list, faces = [], []
    for res in sheets:
        raw = res.body.decode("utf-8", "replace")
        text, refs = clean_css(raw, res.url, lookup, place_from("../"), data.names, keep_fonts)
        name = css_names[res.url]
        _write(out / "css" / name, text)
        for face in font_faces(raw, res.url, lookup):
            faces.append({**face, "declared_in": f"css/{name}"})
        css_list.append({
            "file": f"css/{name}", "source": _shown_path(res.path),
            "from": "the application's own origin" if res.host in own_hosts else "another host",
            "bytes": len(text.encode("utf-8")), "imports": [f"css/{i}" for i in refs.imports],
            "not_carried": sorted(set(refs.dropped)), "embedded_fonts": refs.embedded_fonts,
        })  # fmt: skip
    inline_list = []
    for name, read in sorted(reads.items()):
        blocks = [b for b in read.inline if b.strip()]
        if blocks:
            text = "\n\n".join(b.strip() for b in blocks) + "\n"
            _write(out / "css" / "inline" / f"{name}.css", text)
            inline_list.append({"file": f"css/inline/{name}.css", "screen": name,
                                "blocks": len(blocks), "bytes": len(text.encode("utf-8"))})  # fmt: skip
            for face in font_faces(text):
                faces.append({**face, "declared_in": f"css/inline/{name}.css"})

    # Images the pages themselves show.
    for name, read in sorted(reads.items()):
        base = RENDER_HOST + patterns[name]
        for src, in_data, where in read.images:
            res = lookup(urljoin(base, src))
            if res is not None and res.kind == "image":
                image(res, f"{where} on {name}", in_data)
            elif res is None and not src.lower().startswith("data:"):
                shown = _shown_path(urlsplit(urljoin(base, src)).path)
                item = images.setdefault("missing " + shown, {
                    "path": shown, "type": None, "bytes": None, "width": None, "height": None,
                    "where": [], "bundled": False, "file": None,
                    "reason": _missing_reason(src, found.values())})  # fmt: skip
                if f"{where} on {name}" not in item["where"]:
                    item["where"].append(f"{where} on {name}")
    for item in images.values():
        if item["bundled"]:
            _write(out / item["file"], item["_res"].body)
    for url, rel in font_files.items():
        _write(out / rel, found[url].body)

    # What a rendering may load: only what is carried, at the address the page asks for it.
    served: dict[str, tuple[bytes, str]] = {}
    for res in sheets:
        text = (out / "css" / css_names[res.url]).read_text(encoding="utf-8")
        # in the page a sheet sits at its own address: its references are those of the package
        served[res.key] = (text.encode("utf-8"), "text/css")
        served[f"/css/{css_names[res.url]}"] = served[res.key]
    for item in images.values():
        if item["bundled"]:
            res = item["_res"]
            served[res.key] = (res.body, res.mime or "application/octet-stream")
            served["/" + item["file"]] = served[res.key]
    for url, rel in font_files.items():
        served[found[url].key] = (found[url].body, found[url].mime or "font/woff2")
        served["/" + rel] = served[found[url].key]
    viewport = _viewport(recordings)
    # Each screen drawn with no dialog open; each dialog the recording showed open on that screen
    # drawn on its own, open over it.
    seen_open = dialogs_seen_open(recordings)
    to_draw = {n: (patterns[n], without_dialogs(pages[n])) for n in pages}
    dialog_of: dict[str, tuple[str, str]] = {}
    for n in pages:
        for ident in sorted(seen_open.get(patterns[n], set())):
            html = with_dialog_open(pages[n], ident)
            if html is not None:
                key = f"{n}{DIALOG_SEP}{_file_name(ident.lstrip('#'), set())}"
                to_draw[key] = (patterns[n], html)
                dialog_of[key] = (n, ident)
    rendered, why_not = render(to_draw, served, viewport)

    screens = []
    texts: dict[str, list[dict]] = {}
    for name in sorted(pages):
        read = reads[name]
        texts[name] = read.texts
        entry = {"screen": name, "address": patterns[name], "page": f"pages/{name}.html",
                 "picture": None,
                 "literal_texts": sum(1 for t in read.texts if t["is"] == "literal"),
                 "mixed_texts": sum(1 for t in read.texts if t["is"] == "mixed"),
                 "data_placeholders": sum(1 for t in read.texts if t["is"] == "data")}  # fmt: skip
        shot = rendered.get(name)
        if shot is not None:
            _write(out / "screens" / f"{name}.png", shot.png)
            size = image_size(shot.png)
            entry.update(picture=f"screens/{name}.png", width=size[0] if size else None,
                         height=size[1] if size else None,
                         sha256=hashlib.sha256(shot.png).hexdigest(),
                         not_loaded=[a for a in shot.asked if not a.endswith((".js", ".mjs"))])  # fmt: skip
        for key, (screen, ident) in sorted(dialog_of.items()):
            dshot = rendered.get(key)
            if screen != name or dshot is None:
                continue
            _write(out / "screens" / f"{key}.png", dshot.png)
            entry.setdefault("dialogs", []).append({
                "dialog": ident, "picture": f"screens/{key}.png",
                "sha256": hashlib.sha256(dshot.png).hexdigest(),
                "drawn": "open over the screen, as the recording showed it"})  # fmt: skip
        screens.append(entry)
    # tokens from the screens; from a dialog's picture only the dialog's own style
    looks = {n: r.look for n, r in rendered.items() if n not in dialog_of}
    for key in dialog_of:
        if key in rendered:
            roles = rendered[key].look.get("roles") or {}
            looks[key] = {"roles": {"dialog": roles.get("dialog") or []}}
    design = theme(looks, viewport) if rendered else None
    if design is not None:
        _write(out / "theme.json", _json(design))
    _write(out / "texts.json", _json({
        "format": FORMAT,
        "how_to_read": "Per screen, every text of the page snapshot. `literal`: the "
        "application's own words, copy them exactly. `data`: a placeholder for data (letters "
        "x/X, digits 9), show the app's data there. `mixed`: literal words around placeholders.",
        "screens": texts,
    }))  # fmt: skip

    # Fonts: every family used or declared, where it comes from, what to do about it.
    stacks = (design or {}).get("font_stacks") or []
    declared = {f["family"].lower(): f for f in faces}
    loaded = {f.lower() for r in rendered.values() for f in r.look.get("fonts_loaded") or []}
    roles_of: dict[str, list[str]] = {}
    for role, font in ((design or {}).get("fonts") or {}).items():
        first = next(iter(families_of([font.get("family", "")])), None)
        if first:
            roles_of.setdefault(first.lower(), []).append(role)
    fonts = []
    for family in [*families_of(stacks), *[f["family"] for f in faces]]:
        low = family.lower()
        if any(f["family"].lower() == low for f in fonts):
            continue
        variants = [f for f in faces if f["family"].lower() == low]
        files = []
        for face in variants:
            for f in face["files"]:
                url = f.get("_url")
                files.append({k: v for k, v in {
                    "path": f["path"], "format": f["format"], "bytes": f.get("bytes"),
                    "weight": face["weight"], "style": face["style"],
                    "in_har": f.get("in_har", False), "embedded": f.get("embedded"),
                    "declared_in": face["declared_in"],
                    "bundled": font_files.get(url) if url else None,
                }.items() if v is not None})  # fmt: skip
        item = {
            "family": family,
            "source": ("web font (declared with @font-face in " + declared[low]["declared_in"] + ")")
            if low in declared else
            ("system font (not delivered by the application: installed on the machines that use it)"),
            "generic_fallback": generic_fallback(family, stacks),
            "used_for": sorted(roles_of.get(low, [])),
            "files": files,
            "bundled": bool(files) and any(f.get("bundled") for f in files),
        }  # fmt: skip
        if low in declared:
            item["loaded_in_pictures"] = low in loaded
            item["to_do"] = (
                "The font files are in look/fonts/: use them under the font's licence."
                if item["bundled"] else
                f"Not copied (fonts are licensed). Obtain `{family}` under its licence and serve "
                f"it at the path the style sheet names, or use the fallback `{item['generic_fallback']}`."
            )  # fmt: skip
        elif low in SYSTEM_FONTS:
            item["to_do"] = "Nothing to obtain: name it in font-family as the original does."
        else:
            item["to_do"] = (f"Not delivered by the application. Name it in font-family as the "
                             f"original does; where it is not installed the browser uses "
                             f"`{item['generic_fallback']}`.")  # fmt: skip
        fonts.append(item)

    image_list = [{k: v for k, v in item.items() if not k.startswith("_")}
                  for item in sorted(images.values(), key=lambda i: i["path"])]  # fmt: skip
    manifest = {
        "format": FORMAT,
        "page_text": sanitize.page_text_mode(),
        "viewport": viewport,
        "css": css_list,
        "inline_css": inline_list,
        "fonts": fonts,
        "fonts_bundled": keep_fonts,
        "images": image_list,
        "screens": screens,
        "pictures": "rendered from the sanitised page snapshots with the carried style sheets; "
        "never the recorder's screenshots"
        if rendered
        else f"not rendered: {why_not}",
        "theme": "theme.json" if design is not None else None,
        "texts": "texts.json",
        "privacy": {
            "css": "the application's code, kept as written; addresses reduced to carried files "
            "or dropped; e-mail addresses, record numbers and recorded names in strings and "
            "comments masked",
            "fonts": "named, not copied" if not keep_fonts else f"copied on request ({FONTS_ENV})",
            "images": "only static files of the application's own origin that the interface uses",
            "pictures": "rendered, never recorded",
        },
    }
    problems = check_files(out, manifest, data, {n: r.text for n, r in rendered.items()})
    manifest["check"] = {
        "known_names": len(data.names), "known_tokens": len(data.tokens),
        "files_checked": sum(1 for p in out.rglob("*") if p.is_file()) + 1,
        "pictures_checked": len(rendered), "result": "pass" if not problems else "fail",
    }  # fmt: skip
    if problems:
        shutil.rmtree(out, ignore_errors=True)
        raise LookError(
            f"{app_id}: the look is not carried, because it would hold values the recording "
            "shows as data: " + "; ".join(problems[:12]),
            sorted({p.split(" holds ")[0].split(" shows ")[0] for p in problems}),
        )
    _write(out / MANIFEST, _json(manifest))
    return manifest


# --- the privacy check ------------------------------------------------------------------------------


def check_files(out: Path, manifest: dict, data: Data,
                picture_texts: dict[str, str] | None = None) -> list[str]:  # fmt: skip
    """Every known data value found in what `look/` carries: the style sheets (strings and
    comments), the texts, the SVG images, and the text each rendered picture shows. Empty when
    the look is clean."""
    out = Path(out)
    problems: list[str] = []
    for path in sorted((out / "css").rglob("*.css")) if (out / "css").is_dir() else []:
        for hit in css_findings(path.read_text(encoding="utf-8", errors="replace"), data)[:5]:
            problems.append(f"{path.relative_to(out).as_posix()} holds `{hit}`")
    pages_dir = out.parent / "pages"
    codes: dict[str, set[str]] = {}
    labels: dict[str, set[str]] = {}
    for entry in manifest.get("screens") or []:
        page = pages_dir / f"{entry['screen']}.html"
        read = read_page(page.read_text(encoding="utf-8")) if page.is_file() else None
        codes[entry["screen"]] = read.codes if read else set()
        labels[entry["screen"]] = read.labels if read else set()
    texts_file = out / "texts.json"
    if texts_file.is_file():
        screens = json.loads(texts_file.read_text("utf-8")).get("screens") or {}
        for screen, items in screens.items():
            for item in items:
                text = str(item.get("text") or "")
                for hit in data.findings(text, codes.get(screen, set()),
                                         label=text in labels.get(screen, set())):  # fmt: skip
                    problems.append(f"texts.json ({screen}, {item.get('where')}) holds `{hit}`")
    for screen, shown in sorted((picture_texts or {}).items()):
        hits: set[str] = set()
        base = screen.split(DIALOG_SEP)[0]  # a dialog's picture: the codes of its screen
        for line in shown.splitlines():
            line = " ".join(line.split())
            hits.update(data.findings(line, codes.get(base, set()),
                                      label=line in labels.get(base, set())))  # fmt: skip
        for hit in sorted(hits)[:5]:
            problems.append(f"the picture screens/{screen}.png shows `{hit}`")
    for path in sorted((out / "assets").glob("*.svg")) if (out / "assets").is_dir() else []:
        for hit in data.findings(path.read_text(encoding="utf-8", errors="replace"))[:3]:
            problems.append(f"{path.relative_to(out).as_posix()} holds `{hit}`")
    return problems


def package_files(root: Path, app_id: str) -> list[tuple[str, Path]]:
    """(path in the package, file) for everything under `apps/<app>/look/`."""
    out = look_dir(root, app_id)
    if not (out / MANIFEST).is_file():
        return []
    return [(f"apps/{app_id}/{LOOK_DIR}/{p.relative_to(out).as_posix()}", p)
            for p in sorted(out.rglob("*")) if p.is_file() and not p.is_symlink()]  # fmt: skip


def package_check(root: Path, app_id: str, recordings: list[Path] | None = None) -> list[str]:
    """Why the look must not be packaged as it is (empty: it may).

    - Every picture is one Twin Lab rendered: named in the manifest, with the same sha256. A
      recorder screenshot put there by hand is refused.
    - When the app's recordings are on this machine, every carried text, and the text of every
      picture rendered again from what is carried, is checked against the data they show.
    """
    root = Path(root)
    out = look_dir(root, app_id)
    if not out.is_dir():
        return []
    if not (out / MANIFEST).is_file():
        return [f"apps/{app_id}/look has no {MANIFEST}: generate the brief again"]
    manifest = json.loads((out / MANIFEST).read_text(encoding="utf-8"))
    problems: list[str] = []
    listed = {
        s["picture"]: s.get("sha256") for s in manifest.get("screens") or [] if s.get("picture")
    }
    for s in manifest.get("screens") or []:  # a dialog drawn open over its screen
        listed.update({d["picture"]: d.get("sha256") for d in s.get("dialogs") or []})
    for path in sorted(out.rglob("*")):
        rel = path.relative_to(out).as_posix()
        if not path.is_file() or path.suffix.lower() not in (
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".gif",
        ):
            continue
        if rel.startswith("assets/"):
            if not any(i.get("file") == rel for i in manifest.get("images") or []):
                problems.append(f"look/{rel} is not an image the manifest lists")
        elif listed.get(rel) != hashlib.sha256(path.read_bytes()).hexdigest():
            problems.append(f"look/{rel} is not a picture Twin Lab rendered (never put a "
                            "recorded screenshot there: it shows real data)")  # fmt: skip
    if recordings is None:
        from labctl import factory

        recordings = factory.discover(root, app_id).recordings
    if recordings and not problems:
        data = known_data(recordings)
        pictures: dict[str, str] = {}
        pages_dir = root / "apps" / app_id / "pages"
        for entry in manifest.get("screens") or []:
            page = pages_dir / f"{entry['screen']}.html"
            if entry.get("picture") and page.is_file():
                # what the picture shows is the text of the page it was rendered from
                pictures[entry["screen"]] = "\n".join(
                    t["text"] for t in read_page(page.read_text(encoding="utf-8")).texts)  # fmt: skip
        problems += check_files(out, manifest, data, pictures)
    return problems


# --- the brief and the task -------------------------------------------------------------------------

HEADING = "Look and feel"


def _load(root: Path, app_id: str) -> tuple[dict | None, dict | None, dict | None]:
    out = look_dir(root, app_id)

    def read(name: str) -> dict | None:
        path = out / name
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None

    return read(MANIFEST), read("theme.json"), read("texts.json")


def _ticks(items: list[str], cap: int) -> str:
    shown = ", ".join(f"`{i}`" for i in items[:cap])
    more = len(items) - cap
    return shown + (f", and {more} more" if more > 0 else "")


def brief_section(root: Path, app_id: str, refused: list[str] | None = None) -> str:
    """The brief's section "Look and feel" ("" when nothing is carried and nothing to say).
    `refused`: where the privacy check found data (a `LookError`'s places), when it did."""
    manifest, design, texts = _load(Path(root), app_id)
    app = f"apps/{app_id}"
    if manifest is None:
        pages = Path(root) / "apps" / app_id / "pages"
        if not pages.is_dir() or not any(pages.glob("*.html")):
            return ""
        why = ("the privacy check found values the recording shows as data in "
               + _ticks(refused, 8) + ", so nothing was kept" if refused else
               "strict page-text mode masks every text and the style sheets"
               if sanitize.page_text_mode() == "strict" else
               f"it is switched off (`{SWITCH_ENV}`)" if not enabled() else "nothing was recorded")  # fmt: skip
        return (f"## {HEADING}\n\nNo look is carried for this application: {why}. The page "
                f"snapshots in `{app}/pages/` are the only reference for layout.\n\n")  # fmt: skip
    w: list[str] = [f"## {HEADING}", ""]
    w += [
        "The demo application must look like the original: the same colours, layout, spacing, "
        "fonts and texts. Everything below is in "
        f"`{app}/look/` and was taken from the recording. It holds no customer data.",
        "",
        "How to use it:",
        "",
        "1. Start from the carried style sheets. Use them as the base of your styles. Do not "
        "restyle from memory.",
        "2. `theme.json` is the summary: colours, fonts, control sizes, spacing and layout. Use "
        "it to check your result, and where a style sheet is missing.",
        "3. Compare each screen with its picture. Your screen must match it in layout, colours, "
        "spacing and fonts.",
        "4. Literal texts must be identical: same words, same capitals, same punctuation.",
        "5. A placeholder (letters `x`/`X`, digits `9`) stands for data. Show your own data "
        "there, never the placeholder.",
        "",
        "### Style sheets",
        "",
    ]
    if manifest["css"]:
        w += ["| File | Original address | From |", "|---|---|---|"]
        w += [f"| `look/{c['file']}` | `{c['source']}` | {c['from']} |" for c in manifest["css"]]
        w.append("")
        missing = sorted({d for c in manifest["css"] for d in c["not_carried"]})
        if missing:
            w += ["Addresses in these files that are not carried (the reference is `url(#)`): "
                  + _ticks(missing, 12) + ".", ""]  # fmt: skip
    else:
        w += ["The application delivered no separate style sheet in the recording.", ""]
    if manifest["inline_css"]:
        w += ["Styles written into the pages (also inside each page snapshot): "
              + _ticks([f"look/{c['file']}" for c in manifest["inline_css"]], 30) + ".", ""]  # fmt: skip
    w += [
        "Privacy rule: a style sheet is the application's code, so it is carried as written. "
        "Addresses (`url(...)`, `@import`) point into `look/` or are removed. E-mail addresses, "
        "record numbers and recorded names in strings and comments are masked.",
        "",
    ]
    if design is not None:
        w += ["### Design tokens", "", "From `look/theme.json` (computed styles of the rendered "
              "snapshots):", ""]  # fmt: skip
        if design.get("colors"):
            w += ["| Colour | Value |", "|---|---|"]
            w += [f"| {k.replace('_', ' ')} | `{v}` |" for k, v in design["colors"].items()]
            w.append("")
        if design.get("fonts"):
            w += ["| Text | Font family | Size | Weight |", "|---|---|---|---|"]
            w += [f"| {role.replace('_', ' ')} | `{f.get('family', '')}` | {f.get('size', '')} | "
                  f"{f.get('weight', '')} |" for role, f in design["fonts"].items()]  # fmt: skip
            w.append("")
        controls = design.get("controls") or {}
        rows = [(k, c) for k, c in controls.items() if k in (
            "input", "select", "button_primary", "button_secondary", "table_cell")]  # fmt: skip
        if rows:
            w += [
                "| Control | Padding | Border | Corner radius | Height |",
                "|---|---|---|---|---|",
            ]
            w += [f"| {k.replace('_', ' ')} | {c.get('padding', '')} | {c.get('border', '')} | "
                  f"{c.get('radius', '0px')} | {c.get('height', '')}px |" for k, c in rows]  # fmt: skip
            w.append("")
        widths = (design.get("layout") or {}).get("pages") or []
        if widths:
            sizes = sorted({(p["content_width"], str(p.get("content_max_width"))) for p in widths})
            view = design.get("viewport") or VIEWPORT
            w += [f"Window when recorded: {view['width']} x {view['height']} pixels. Main "
                  "content width: " + ", ".join(
                      f"{cw}px" + (f" (max-width {mw})" if mw not in ("none", "None") else "")
                      for cw, mw in sizes[:4])
                  + ". The regions of each screen are in `theme.json` under `layout`.", ""]  # fmt: skip
    w += ["### Fonts", ""]
    if manifest["fonts"]:
        w += ["| Font family | Where it comes from | Files | Fallback | What to do |",
              "|---|---|---|---|---|"]  # fmt: skip
        for f in manifest["fonts"]:
            files = "; ".join(f"`{x['path']}` ({x['format']})" for x in f["files"][:4]) or "none"
            w.append(f"| `{f['family']}` | {f['source']} | {files} | `{f['generic_fallback']}` | "
                     f"{f['to_do']} |")  # fmt: skip
        w.append("")
    else:
        w += ["No font family could be read (no picture was rendered).", ""]
    w += [
        "Privacy rule: fonts are licensed, so font files are not copied into the package"
        + (f" (here they were, on request: `{FONTS_ENV}=bundle`)." if manifest["fonts_bundled"]
           else f". The owner can allow it with `{FONTS_ENV}=bundle` when the brief is made.")
        + " Without the file, a picture shows the fallback font.",
        "",
        "### Images and icons",
        "",
    ]  # fmt: skip
    if manifest["images"]:
        w += [
            "| Original address | Size | Where it appears | In the package |",
            "|---|---|---|---|",
        ]
        for i in manifest["images"][:40]:
            size = (f"{i['width']} x {i['height']}" if i.get("width") else
                    f"{i['bytes']} bytes" if i.get("bytes") else "not known")  # fmt: skip
            where = "; ".join(i["where"][:3])
            got = f"`look/{i['file']}`" if i["bundled"] else f"no: {i['reason']}"
            w.append(f"| `{i['path']}` | {size} | {where} | {got} |")
        w.append("")
    else:
        w += ["The recording shows no image file the interface uses.", ""]
    w += [
        "Privacy rule: an image is carried only when it is part of the interface: a static file "
        "of the application's own address, used by a style sheet or by a page outside its data "
        "cells, with no record number or query in its address and not under an upload folder. "
        "Pictures that users add (avatars, attachments) are never carried: draw a neutral "
        "placeholder of the same size.",
        "",
        "### Screens: picture and texts",
        "",
    ]
    if any(s.get("picture") for s in manifest["screens"]):
        w += ["Each picture was rendered from the page snapshot with the carried style sheets. It "
              "shows the real styling and the real interface texts, and placeholders where data "
              "was. It is never a recorded screenshot. Open the picture next to your screen and "
              "compare.", ""]  # fmt: skip
    else:
        w += [f"No picture could be rendered ({manifest['pictures']}). Use the page snapshots and "
              "`theme.json`.", ""]  # fmt: skip
    for s in manifest["screens"]:
        items = ((texts or {}).get("screens") or {}).get(s["screen"]) or []
        literal, seen = [], set()
        for t in items:
            if t["is"] == "literal" and t["text"] not in seen and re.search(r"\w", t["text"]):
                seen.add(t["text"])
                literal.append(t["text"].replace("`", "'").replace("|", "/"))
        mixed = [t["text"].replace("`", "'") for t in items if t["is"] == "mixed"]
        holders, seen_h = [], set()
        for t in items:
            if t["is"] == "data" and t["where"] not in seen_h:
                seen_h.add(t["where"])
                holders.append(t["where"] + ("" if t["kind"] == "text" else f" ({t['kind']})"))
        picture = f"`look/{s['picture']}`" if s.get("picture") else "no picture"
        drawn = "".join(f"; dialog `{d['dialog']}` drawn open: `look/{d['picture']}`"
                        for d in s.get("dialogs") or [])  # fmt: skip
        w += [f"**{s['screen']}** (`{s['address']}`): picture {picture}, snapshot "
              f"`{s['page']}`{drawn}.", ""]  # fmt: skip
        w.append("- Literal texts, copy exactly: " + (_ticks(literal, 60) if literal else "none")
                 + ".")  # fmt: skip
        if mixed:
            w.append("- Literal words around a placeholder: " + _ticks(list(dict.fromkeys(mixed)), 15)
                     + ".")  # fmt: skip
        w.append("- Placeholders for data (show your data there): "
                 + (_ticks(holders, 30) if holders else "none") + ".")  # fmt: skip
        w.append("")
    w += ["The full list, text by text, is in `look/texts.json`. `look/manifest.json` lists every "
          "carried file and the privacy check that passed.", ""]  # fmt: skip
    return "\n".join(w) + "\n"


def task_text(root: Path, app_id: str) -> str:
    """The task's section on the look ("" when the package carries none)."""
    manifest, design, _ = _load(Path(root), app_id)
    if manifest is None:
        return ""
    app = f"apps/{app_id}"
    pictures = any(s.get("picture") for s in manifest["screens"])
    lines = [
        "## Same look",
        "",
        "The demo app must also **look** like the original. The package carries the look in "
        f"`{app}/look/`; the brief's *{HEADING}* section explains each file:",
        "",
        f"- `{app}/look/css/`: the original style sheets. Use them as the base of your styles.",
    ]
    if design is not None:
        lines.append(f"- `{app}/look/theme.json`: colours, fonts, control sizes, spacing and "
                     "layout in one file.")  # fmt: skip
    if pictures:
        lines.append(f"- `{app}/look/screens/<screen>.png`: one picture per screen, with the real "
                     "styling and interface texts, and placeholders where data was. Your screen "
                     "must match its picture.")  # fmt: skip
    lines += [
        f"- `{app}/look/texts.json`: per screen, which texts are literal (copy them exactly) and "
        "which are placeholders for data (letters `x`/`X`, digits `9`: show your data there).",
        f"- `{app}/look/manifest.json`: the fonts to use (font files are "
        + ("in `look/fonts/`" if manifest.get("fonts_bundled") else "not included: obtain them or "
           "use the fallback named there")
        + ") and the images of the interface"
        + (" in `look/assets/`" if any(i["bundled"] for i in manifest["images"]) else "")
        + ".",
        "",
        "Copy the files you use from `look/` into your app (for example its static folder); "
        f"leave `{app}/look/` itself unchanged. In BUILD-NOTES.md, list each screen with how "
        "you compared it with its picture and any difference you could not remove.",
        "",
        "",
    ]  # fmt: skip
    return "\n".join(lines)


def check_step(root: Path, app_id: str) -> str:
    """One sentence for the task's checks ("" when the package carries no picture)."""
    manifest, _, _ = _load(Path(root), app_id)
    if manifest is None or not any(s.get("picture") for s in manifest["screens"]):
        return ""
    view = manifest.get("viewport") or VIEWPORT
    return (f" Look: open each screen at {view['width']} x {view['height']} pixels next to "
            f"`apps/{app_id}/look/screens/<screen>.png` and compare layout, colours, spacing, "
            "fonts and every literal text.")  # fmt: skip
