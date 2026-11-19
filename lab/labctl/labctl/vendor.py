"""The open-source client libraries and web fonts of an application, carried in the package.

A builder told only "jQuery 3.2.1, Bootstrap 4.0.0-alpha.6, video.js" writes stand-ins for the
dialogs, the carousel and the video player, and a page set in a fallback font. So the brief step
carries the exact files the browser received:

- `apps/<app>/vendor/<library>/<version>/...`: each third-party library's style sheets and
  scripts, byte for byte, with the assets its style sheets reference (icon fonts, images) at the
  same relative places, so the library's own `url(...)` references keep working;
- `apps/<app>/look/fonts/`: the web font files (`labctl.lookfeel` writes them, deciding with
  `font_licence()` here);
- `apps/<app>/vendor/vendor.json`: every library carried and not carried (with the reason), each
  file's original address, sha256 and size, and which page loads which file;
- `apps/<app>/THIRD-PARTY-NOTICES.md`: name, version, licence, original address, sha256 and
  files of everything carried, with the licence texts (short local copies under
  `labctl/licences/`, never downloaded).

A library file is carried only when all of these hold:

1. `labctl.libraries.identify` names it a third-party library (by banner, package folder, public
   library host or file name). The application's own scripts are never carried by this rule
   (`TWINLAB_PAGE_SCRIPTS=carry` stays the owner's opt-in for those, `labctl.pagerules`).
2. Its licence is one that allows redistribution (`REDISTRIBUTABLE`), read from the file's own
   licence banner or, when the banner says nothing, from `KNOWN_LIBRARIES`. A file whose banner
   names other terms, or whose licence is not known, is named with the reason and not carried.
3. It holds nothing shaped like a key, token or credential, and no internal host or private
   address (`findings()`, on the patterns of `labctl.codemap`). Public hosts in a library's
   comments (its home page, its licence address) are its code, not a leak.

A font is carried when its own `name` table (`labctl.fontname`) states a licence on the list, or,
when the file cannot be read or states nothing, its family is a known open font
(`KNOWN_FONTS`). `TWINLAB_LOOK_FONTS=bundle` carries every font file the HAR holds (the
package then says which licences were not identified), `off` carries none. `TWINLAB_VENDOR=off`
carries no library.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

DIR = "vendor"
FILE = "vendor.json"
NOTICES = "THIRD-PARTY-NOTICES.md"
FORMAT = "twinlab-vendor/1"
SWITCH_ENV = "TWINLAB_VENDOR"  # "off": carry no library
HEADING = "Client libraries and fonts"
UNKNOWN_VERSION = "version-unknown"
LICENCE_DIR = Path(__file__).parent / "licences"

REDISTRIBUTABLE = {
    "MIT": "MIT License",
    "BSD-2-Clause": "BSD 2-Clause License",
    "BSD-3-Clause": "BSD 3-Clause License",
    "Apache-2.0": "Apache License 2.0",
    "ISC": "ISC License",
    "MPL-2.0": "Mozilla Public License 2.0",
    "OFL-1.1": "SIL Open Font License 1.1",
    "Unlicense": "The Unlicense",
    "CC0-1.0": "Creative Commons Zero v1.0 Universal",
}
# How a licence is named in a banner or a font's name table, in the order they are tried.
_LICENCE_WORDS = [
    ("Apache-2.0", r"Apache[- ]License,?\s*(?:Version\s*|v\.?\s*)?2(?:\.0)?\b|\bApache-2\.0\b|"
                   r"apache\.org/licenses/LICENSE-2\.0|\bApache 2(?:\.0)?\b"),
    ("MPL-2.0", r"\bMPL[- ]?2\.0\b|Mozilla Public License,?\s*v(?:ersion)?\.?\s*2\.0|"
                r"mozilla\.org/MPL/2\.0"),
    ("OFL-1.1", r"SIL Open Font Licen[cs]e|\bOFL(?:-1\.1)?\b|scripts\.sil\.org/OFL|"
                r"openfontlicense\.org"),
    ("BSD-3-Clause", r"\bBSD[- ]3[- ]Clause\b|\bNew BSD\b|\bModified BSD\b|\b3-clause BSD\b"),
    ("BSD-2-Clause", r"\bBSD[- ]2[- ]Clause\b|\bSimplified BSD\b|\b2-clause BSD\b"),
    ("ISC", r"\bISC(?: License)?\b"),
    ("Unlicense", r"\bUnlicense\b|unlicense\.org"),
    ("CC0-1.0", r"\bCC0\b|creativecommons\.org/publicdomain/zero"),
    ("MIT", r"\bMIT\b"),
]  # fmt: skip
_RESTRICTIVE = re.compile(
    r"\b(?:A?GPL|LGPL|GNU (?:Lesser |Library |Affero )?General Public|proprietary|"
    r"commercial licen[cs]e|EULA|end[- ]user licen[cs]e|may not be (?:copied|distributed|"
    r"redistributed|embedded)|not for redistribution|without (?:the )?(?:prior )?written "
    r"permission)\b", re.IGNORECASE)  # fmt: skip

# Libraries whose licence is well known, for a file that does not state it (by
# `libraries.identify`'s name, lower case). `fonts`: the licence of the library's own fonts.
KNOWN_LIBRARIES: dict[str, dict] = {
    "jquery": {"licence": "MIT", "home": "https://jquery.com"},
    "jquery ui": {"licence": "MIT", "home": "https://jqueryui.com"},
    "jquery migrate": {"licence": "MIT", "home": "https://github.com/jquery/jquery-migrate"},
    "jquery validation": {"licence": "MIT", "home": "https://jqueryvalidation.org"},
    "bootstrap": {"licence": "MIT", "home": "https://getbootstrap.com"},
    "video.js": {"licence": "Apache-2.0", "fonts": "Apache-2.0", "home": "https://videojs.com"},
    "videojs-contrib-hls": {"licence": "Apache-2.0",
                            "home": "https://github.com/videojs/videojs-contrib-hls"},
    "tether": {"licence": "MIT", "home": "https://github.com/shipshapecode/tether"},
    "sweetalert": {"licence": "MIT", "home": "https://github.com/lipis/bootstrap-sweetalert"},
    "sweetalert2": {"licence": "MIT", "home": "https://sweetalert2.github.io"},
    "popper": {"licence": "MIT", "home": "https://popper.js.org"},
    "font-awesome": {"licence": "MIT", "fonts": "OFL-1.1", "home": "https://fontawesome.com"},
    "fontawesome": {"licence": "MIT", "fonts": "OFL-1.1", "home": "https://fontawesome.com"},
    "lodash": {"licence": "MIT", "home": "https://lodash.com"},
    "underscore": {"licence": "MIT", "home": "https://underscorejs.org"},
    "moment": {"licence": "MIT", "home": "https://momentjs.com"},
    "knockout": {"licence": "MIT", "home": "https://knockoutjs.com"},
    "backbone": {"licence": "MIT", "home": "https://backbonejs.org"},
    "angularjs": {"licence": "MIT", "home": "https://angularjs.org"},
    "react": {"licence": "MIT", "home": "https://react.dev"},
    "vue": {"licence": "MIT", "home": "https://vuejs.org"},
    "select2": {"licence": "MIT", "home": "https://select2.org"},
    "datatables": {"licence": "MIT", "home": "https://datatables.net"},
    "chart.js": {"licence": "MIT", "home": "https://www.chartjs.org"},
    "d3": {"licence": "ISC", "home": "https://d3js.org"},
    "axios": {"licence": "MIT", "home": "https://axios-http.com"},
    "alpine.js": {"licence": "MIT", "home": "https://alpinejs.dev"},
    "inputmask": {"licence": "MIT", "home": "https://github.com/RobinHerbots/Inputmask"},
    "toastr": {"licence": "MIT", "home": "https://github.com/CodeSeven/toastr"},
    "modernizr": {"licence": "MIT", "home": "https://modernizr.com"},
    "handlebars": {"licence": "MIT", "home": "https://handlebarsjs.com"},
    "mustache": {"licence": "MIT", "home": "https://github.com/janl/mustache.js"},
    "leaflet": {"licence": "BSD-2-Clause", "home": "https://leafletjs.com"},
    "zone.js": {"licence": "MIT", "home": "https://github.com/angular/angular"},
    "core-js": {"licence": "MIT", "home": "https://github.com/zloirock/core-js"},
    "rxjs": {"licence": "Apache-2.0", "home": "https://rxjs.dev"},
    "hammer": {"licence": "MIT", "home": "https://hammerjs.github.io"},
    "preact": {"licence": "MIT", "home": "https://preactjs.com"},
    "swiper": {"licence": "MIT", "home": "https://swiperjs.com"},
    "echarts": {"licence": "Apache-2.0", "home": "https://echarts.apache.org"},
}  # fmt: skip
# Open font families, by name (lower case, letters and digits only, after the weight and style
# words): what a font file is when it cannot be read or does not say.
KNOWN_FONTS: dict[str, str] = {
    name: "OFL-1.1" for name in (
        "lato", "fontawesome", "sourcesanspro", "sourcesans3", "sourceserifpro", "sourcecodepro",
        "montserrat", "raleway", "poppins", "nunito", "nunitosans", "merriweather",
        "playfairdisplay", "oswald", "notosans", "notoserif", "ptsans", "ptserif", "worksans",
        "firasans", "firacode", "ibmplexsans", "ibmplexmono", "ibmplexserif", "dmsans", "rubik",
        "karla", "mulish", "barlow", "manrope", "quicksand", "josefinsans", "cabin",
        "librebaskerville", "lora", "ebgaramond", "crimsontext", "titilliumweb")
}  # fmt: skip
KNOWN_FONTS["videojs"] = "Apache-2.0"  # video.js's icon font (videojs-font)
_STYLE_WORDS = ("webfont", "web", "regular", "normal", "italic", "oblique", "bold", "semibold",
                "demibold", "extrabold", "ultrabold", "light", "extralight", "ultralight", "thin",
                "hairline", "medium", "black", "heavy", "book", "variable", "vf", "subset",
                "latin", "ext")  # fmt: skip
# Not a library name: what `identify` says of a file it cannot name.
_NOT_NAMED = {"a script or style sheet from another origin", "vendor bundle", "polyfills",
              "analytics", "a library (licence banner)"}  # fmt: skip
TEXT_SUFFIXES = (".css", ".js", ".mjs", ".svg", ".map", ".json", ".txt")


def enabled() -> bool:
    return (os.environ.get(SWITCH_ENV) or "").strip().lower() not in ("off", "0", "no", "false")


def font_mode() -> str:
    """`bundle` (every font file, licence or not), `off` (none) or `licensed` (the default:
    those whose licence allows redistribution)."""
    from labctl import lookfeel

    value = (os.environ.get(lookfeel.FONTS_ENV) or "").strip().lower()
    if value in ("bundle", "1", "yes", "true"):
        return "bundle"
    if value in ("off", "0", "no", "false", "none"):
        return "off"
    return "licensed"


# --- licences ---------------------------------------------------------------------------------


@dataclass
class Licence:
    """What is known about one file's licence."""

    id: str | None = None  # an SPDX identifier when identified
    source: str = ""  # where it was read: the file's banner, a known mapping, the name table
    copyright: str | None = None
    why_not: str | None = None  # set when the file may not be carried

    @property
    def ok(self) -> bool:
        return self.id in REDISTRIBUTABLE and self.why_not is None

    def as_dict(self) -> dict:
        return {k: v for k, v in {"licence": self.id, "licence_from": self.source,
                                  "copyright": self.copyright}.items() if v}  # fmt: skip


def licence_in_text(text: str) -> tuple[str | None, bool]:
    """(the first licence on the list the text names, whether it names restrictive terms)."""
    best: tuple[int, str] | None = None
    for spdx, rx in _LICENCE_WORDS:
        m = re.search(rx, text, re.IGNORECASE if spdx not in ("MIT", "ISC") else 0)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), spdx)
    return (best[1] if best else None), bool(_RESTRICTIVE.search(text))


def banner(text: str) -> str:
    """The comments at the top of a file (its licence banner), up to 4000 characters."""
    from labctl.libraries import _COMMENT

    head = text[:8000]
    out = []
    for m in _COMMENT.finditer(head):
        if head[: m.start()].strip(" \t\r\n;!") and m.start() > 4000:
            break
        out.append(m.group())
        if sum(len(x) for x in out) > 4000:
            break
    return "\n".join(out)[:4000]


def copyright_line(text: str) -> str | None:
    m = re.search(r"(?im)^[\s/*!#-]*((?:copyright|\(c\)|©)\s*[^\n*]{3,160})", text)
    if not m:
        m = re.search(r"(?i)((?:copyright|\(c\)|©)\s*[^\n*|]{3,120})", text)
    return re.sub(r"\s+", " ", m.group(1)).strip(" *|/") if m else None


def library_licence(name: str, texts: list[str]) -> Licence:
    """The licence of a library from its files' banners, else from `KNOWN_LIBRARIES`."""
    known = KNOWN_LIBRARIES.get(name.lower())
    restrictive = False
    copyright = None
    for text in texts:
        head = banner(text)
        copyright = copyright or copyright_line(head)
        spdx, bad = licence_in_text(head)
        if spdx:
            return Licence(spdx, "the licence banner of the file", copyright)
        restrictive = restrictive or bad
    if restrictive:
        return Licence(None, "the licence banner of the file", copyright,
                       "its banner names licence terms that are not on the list of licences "
                       "that allow redistribution")  # fmt: skip
    if known:
        return Licence(known["licence"], f"the known licence of {name} (the file does not state "
                       "it)", copyright)  # fmt: skip
    return Licence(None, "", copyright, "its licence is not identified: the file states none and "
                   "the library is not one whose licence is known here")  # fmt: skip


def family_key(name: str) -> str:
    """`LatoWebBlack` -> `lato`, `fontawesome-webfont` -> `fontawesome`."""
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    key = re.sub(r"\d{3}$", "", key)
    changed = True
    while changed:
        changed = False
        for word in _STYLE_WORDS:
            if key.endswith(word) and len(key) > len(word) + 1:
                key = key[: -len(word)]
                changed = True
    return key


def font_licence(body: bytes, family: str | None = None, file_name: str | None = None,
                 library: Licence | None = None, library_name: str | None = None) -> Licence:  # fmt: skip
    """The licence of a web font file: its own `name` table, else what is known of its family,
    else (a font a library's style sheet uses) the library's licence for its fonts."""
    from labctl import fontname

    names = fontname.read(body)
    own = " ".join(x for x in (names.licence, names.licence_url) if x)
    copyright = names.copyright
    if own:
        spdx, bad = licence_in_text(own)
        if spdx:
            return Licence(spdx, "the font's own name table (licence record)", copyright)
        if bad or names.licence:
            return Licence(None, "the font's own name table (licence record)", copyright,
                           "its name table states a licence that is not on the list of licences "
                           "that allow redistribution")  # fmt: skip
    for candidate in (names.family, family, file_name):
        if candidate:
            spdx = KNOWN_FONTS.get(family_key(os.path.splitext(candidate)[0]
                                              if candidate == file_name else candidate))  # fmt: skip
            if spdx:
                where = ("cannot be read here: " + names.why) if names.why else "states no licence"
                return Licence(spdx, f"the known licence of the `{candidate}` family (the file "
                               f"{where})", copyright)  # fmt: skip
    if library is not None and library.ok:
        lib = KNOWN_LIBRARIES.get((library_name or "").lower()) or {}
        spdx = lib.get("fonts") or library.id
        return Licence(spdx, f"the licence of {library_name or 'the library'}, whose style sheet "
                       "uses this font (the font file states none)", copyright)  # fmt: skip
    why = names.why or "it states no licence"
    return Licence(None, "", copyright, f"its licence could not be identified ({why}, and the "
                   "family is not a known open font)")  # fmt: skip


# --- the secret and host check ----------------------------------------------------------------

_STRING = re.compile(r"\"(?:\\.|[^\"\\\n]){0,400}\"|'(?:\\.|[^'\\\n]){0,400}'|`[^`]{0,400}`")
_DATA_URI = re.compile(r"data:[\w/+.-]+(?:;[\w=.-]+)*,[A-Za-z0-9+/=%._-]+")
_ALPHABET = re.compile(r"ABCDEFGHIJKLMNOPQRSTUVWXYZ|abcdefghijklmnopqrstuvwxyz|0123456789")


def findings(text: str) -> list[str]:
    """Why a library file may not be carried: something shaped like a key, token, credential
    or connection string (`codemap`'s patterns), a long random value outside an embedded
    `data:` file, an internal host name or private address in a string or an address. Kind and
    shape only, never the value."""
    from labctl import codemap, sanitize

    found: list[str] = []
    for rx, what in codemap._SECRET_PATTERNS:
        for m in rx.finditer(text):
            if what == "a credential":
                v = m.group(1)
                if not (re.search(r"\d", v) and re.search(r"[A-Za-z]", v)) and not codemap._random(
                    v
                ):
                    continue
                if re.fullmatch(r"[A-Za-z_$][\w$.]*(?:\(|\[)?", v):
                    continue  # `token: e.token`: a name in code, not a value
            found.append(f"{what} (shape `{sanitize.mask(m.group())[:24]}`)")
    plain = _DATA_URI.sub("data:", text)
    for m in codemap._RANDOM.finditer(plain):
        if codemap._random(m.group()) and not _ALPHABET.search(m.group()):
            found.append(f"a long random value (shape `{sanitize.mask(m.group())[:24]}`)")
    places = [m.group() for m in _STRING.finditer(plain)]
    places += [m.group(1) for m in codemap._URL_HOST.finditer(plain)]
    for place in places:
        for rx in (codemap._INTERNAL, codemap._PRIVATE_IP):
            for m in rx.finditer(place):
                h = m.group().lower()
                if not codemap._EXAMPLE_HOST.search(h) and not re.fullmatch(r"[\w$]+\.local", h):
                    found.append(f"an internal host name or address (shape `{sanitize.mask(h)}`)")
    return sorted(set(found))


# --- what the recording delivered ---------------------------------------------------------------


@dataclass
class File:
    url: str
    host: str
    path: str
    query: str
    mime: str
    body: bytes

    @property
    def ext(self) -> str:
        return os.path.splitext(self.path)[1].lower()

    @property
    def kind(self) -> str:
        from labctl import lookfeel

        if self.mime == "text/css" or self.ext == ".css":
            return "style sheet"
        if "javascript" in self.mime or self.ext in (".js", ".mjs"):
            return "script"
        if "font" in self.mime or self.ext in lookfeel.FONT_EXT:
            return "font"
        if self.mime.startswith("image/") or self.ext in lookfeel.IMAGE_EXT:
            return "image"
        return "other"

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


def delivered(har: dict) -> list[File]:
    """Every style sheet, script, font and image a HAR answered with a body, first seen first."""
    import base64

    out: list[File] = []
    seen: set[str] = set()
    for entry in (har.get("log") or {}).get("entries") or []:
        req, resp = entry.get("request") or {}, entry.get("response") or {}
        if str(req.get("method", "")).upper() != "GET" or resp.get("status") != 200:
            continue
        content = resp.get("content") or {}
        text = content.get("text")
        url = str(req.get("url") or "")
        parts = urlsplit(url)
        if not text or parts.scheme not in ("http", "https") or url in seen:
            continue
        try:
            body = (base64.b64decode(text) if content.get("encoding") == "base64"
                    else str(text).encode("utf-8"))  # fmt: skip
        except ValueError:
            continue
        mime = str(content.get("mimeType") or "").split(";")[0].strip().lower()
        f = File(url, parts.netloc.lower(), unquote(parts.path or "/"), parts.query, mime, body)
        if f.kind != "other":
            seen.add(url)
            out.append(f)
    return out


_REF = re.compile(r"<(script|link)\b([^>]*)>", re.IGNORECASE)
_ATTR = re.compile(r"([\w-]+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)")


def page_references(har: dict) -> dict[str, list[str]]:
    """Per page (its address pattern), the scripts and style sheets it references, in order."""
    from labctl import sanitize

    out: dict[str, list[str]] = {}
    for entry in (har.get("log") or {}).get("entries") or []:
        content = (entry.get("response") or {}).get("content") or {}
        if "html" not in str(content.get("mimeType") or "") or not content.get("text"):
            continue
        if content.get("encoding") == "base64":
            continue
        url = str((entry.get("request") or {}).get("url") or "")
        page = sanitize.route_pattern(urlsplit(url).path or "/")
        refs = out.setdefault(page, [])
        for m in _REF.finditer(str(content["text"])):
            attrs = {k.lower(): v.strip("\"'") for k, v in _ATTR.findall(m.group(2))}
            src = attrs.get("src") if m.group(1).lower() == "script" else (
                attrs.get("href") if "stylesheet" in attrs.get("rel", "").lower() else None)  # fmt: skip
            if src and not src.lower().startswith(("data:", "javascript:")):
                full = urljoin(url, src).split("#")[0]
                if full not in refs:
                    refs.append(full)
    return out


# --- carrying -----------------------------------------------------------------------------------


@dataclass
class Group:
    name: str
    version: str | None
    version_from: str | None
    found_by: str
    files: list[File] = field(default_factory=list)
    assets: list[File] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # assets the style sheets name, not in HAR


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-.") or "library"


def _root(paths: list[str]) -> str:
    """The folder the files of one library sit under, as the browser asked for them."""
    for rx in (r"^(.*?/(?:node_modules|bower_components)/(?:@[^/]+/)?[^/]+/)",
               r"^(.*?/(?:ajax/libs|gh)/[^/]+/v?\d[^/]*/)", r"^(.*?/npm/(?:@[^/]+/)?[^/@]+@[^/]+/)",
               r"^(/(?:@[^/]+/)?[^/@]+@\d[^/]*/)"):  # fmt: skip
        roots = {m.group(1) if (m := re.match(rx, p)) else None for p in paths}
        if len(roots) == 1 and None not in roots:
            return roots.pop()
    dirs = [p.rsplit("/", 1)[0] for p in paths]
    common = os.path.commonpath(dirs) if dirs else "/"
    return common.replace("\\", "/").rstrip("/") + "/"


def _inside(path: str, root: str) -> str:
    rel = path[len(root) :] if path.startswith(root) else path.lstrip("/")
    parts = [
        re.sub(r"[^A-Za-z0-9._@+-]+", "-", p) for p in rel.split("/") if p not in ("", ".", "..")
    ]
    return "/".join(parts) or "file"


_CSS_URL = re.compile(r"url\(\s*[\"']?([^\"')]+?)[\"']?\s*\)|@import\s+[\"']([^\"']+)[\"']",
                      re.IGNORECASE)  # fmt: skip


def _text_bytes(f: File) -> bytes:
    """A text file as the repository stores it (Unix line endings: the package's checksums)."""
    return f.body.replace(b"\r\n", b"\n") if f.ext in TEXT_SUFFIXES else f.body


def write(root: Path, app_id: str, recordings: list[Path] | None = None) -> dict | None:
    """Write `apps/<app>/vendor/` and `THIRD-PARTY-NOTICES.md` from the recordings (and the
    fonts `look/` carries); return `vendor.json`'s document (None: nothing to say)."""
    import shutil

    from labctl import har, libraries, lookfeel

    root = Path(root)
    app = root / "apps" / app_id
    out = app / DIR
    shutil.rmtree(out, ignore_errors=True)
    (app / NOTICES).unlink(missing_ok=True)
    if recordings is None:
        from labctl import factory

        recordings = factory.discover(root, app_id).recordings
    files: dict[str, File] = {}
    pages: dict[str, list[str]] = {}
    own_hosts: set[str] = set()
    for path in recordings or []:
        try:
            rec = har.load(path)
        except (OSError, ValueError):
            continue
        own_hosts |= {urlsplit(s.url).netloc.lower() for s in rec.steps if s.url.startswith("http")}
        for f in delivered(rec.har):
            files.setdefault(f.url, f)
        for page, refs in page_references(rec.har).items():
            have = pages.setdefault(page, [])
            have += [r for r in refs if r not in have]
    by_path = {}
    for f in files.values():
        by_path.setdefault(f.host + f.path, f)

    rows, own_code = [], []
    for f in files.values():
        if f.kind not in ("script", "style sheet"):
            continue
        lib = libraries.identify(f.path, f.text(), own=f.host in own_hosts, query=f.query)
        if lib is None:
            if f.kind == "script":
                own_code.append(f.path)
            continue
        rows.append({"name": lib.name, "version": lib.version, "version_from": lib.version_from,
                     "found_by": lib.found_by, "files": [f.url]})  # fmt: skip
    groups = []
    for g in libraries.group(rows):
        group = Group(g["name"], g.get("version"), g.get("version_from"), g["found_by"],
                      [files[u] for u in g["files"]])  # fmt: skip
        for sheet in [x for x in group.files if x.kind == "style sheet"]:
            for m in _CSS_URL.finditer(re.sub(r"/\*.*?\*/", "", sheet.text(), flags=re.DOTALL)):
                ref = (m.group(1) or m.group(2) or "").strip()
                if not ref or ref.startswith(("data:", "#")) or ref.lower().startswith("about:"):
                    continue
                full = urljoin(sheet.url, ref)
                parts = urlsplit(full)
                hit = files.get(full.split("#")[0]) or by_path.get(parts.netloc.lower()
                                                                   + unquote(parts.path))  # fmt: skip
                if hit is not None and hit.kind in ("font", "image", "style sheet"):
                    if hit not in group.files and hit not in group.assets:
                        group.assets.append(hit)
                elif hit is None and unquote(parts.path) not in group.missing:
                    group.missing.append(unquote(parts.path))
        groups.append(group)

    carried, refused = [], []
    for group in sorted(groups, key=lambda x: (x.name.lower(), x.version or "")):
        addresses = [f.path for f in group.files]
        entry = {"name": group.name, "version": group.version,
                 "version_from": group.version_from, "found_by": group.found_by,
                 "addresses": addresses}  # fmt: skip
        if not enabled():
            refused.append({**entry, "reason": f"libraries are switched off ({SWITCH_ENV}=off)"})
            continue
        if group.name in _NOT_NAMED or group.name.lower() in ("javax.faces", "jsf"):
            refused.append({**entry, "reason": "not identified as a known third-party library "
                            f"({group.name})"})  # fmt: skip
            continue
        lic = library_licence(group.name, [f.text() for f in group.files])
        if lic.ok and lic.source.startswith("the known licence") and not _confirmed(group):
            refused.append({**entry, "reason": "named only by its file name: the file states "
                            "neither a licence nor a version and is not served from a package "
                            "folder or a public library host, so it may be the application's "
                            "own code"})  # fmt: skip
            continue
        if not lic.ok:
            refused.append({**entry, **lic.as_dict(), "reason": lic.why_not or "its licence "
                            f"({lic.id}) is not on the list of licences that allow "
                            "redistribution"})  # fmt: skip
            continue
        bad = []
        for f in [*group.files, *[a for a in group.assets if a.kind == "style sheet"]]:
            bad += [f"{f.path}: {x}" for x in findings(f.text())]
        if bad:
            refused.append({**entry, **lic.as_dict(), "reason": "the secret and host check "
                            "refused it: " + "; ".join(bad[:5])})  # fmt: skip
            continue
        version = group.version or UNKNOWN_VERSION
        folder = f"{DIR}/{slug(group.name)}/{slug(version)}"
        base = _root([f.path for f in [*group.files, *group.assets]])
        listed, skipped = [], []
        for f in [*group.files, *group.assets]:
            is_asset = f in group.assets
            licence = lic
            if is_asset and f.kind == "font":
                licence = font_licence(f.body, None, f.path.rsplit("/", 1)[-1], lic, group.name)
                if not licence.ok:
                    skipped.append({"address": f.path, "reason": licence.why_not or ""})
                    continue
            rel = f"{folder}/{_inside(f.path, base)}"
            data = _text_bytes(f)
            target = app / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            item = {"file": rel, "address": f.path, "kind": f.kind,
                    "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                    "from": "the application's own origin" if f.host in own_hosts else
                    f"another host ({f.host})" if f.host in _public_hosts() else "another host"}  # fmt: skip
            if is_asset:
                item["used_by"] = "a style sheet of the library"
                if licence is not lic:
                    item.update(licence.as_dict())
            if f.kind == "style sheet" and "data:" in f.text() and re.search(
                    r"data:(?:application/(?:x-)?font|font/)", f.text()):  # fmt: skip
                item["embedded_fonts"] = "font files embedded as data: addresses, carried with it"
            listed.append(item)
        used_on = sorted(p for p, refs in pages.items()
                         if any(urlsplit(r).path in addresses for r in refs))  # fmt: skip
        carried.append({**entry, "version": group.version, "folder": folder, **lic.as_dict(),
                        "home": (KNOWN_LIBRARIES.get(group.name.lower()) or {}).get("home"),
                        "files": listed, "not_carried": skipped,
                        "not_in_recording": group.missing, "pages": used_on})  # fmt: skip
    fonts = _look_fonts(root, app_id)
    if not carried and not refused and not fonts:
        return None
    page_lists = {}
    for page, refs in sorted(pages.items()):
        paths = [urlsplit(r).path for r in refs]
        in_vendor = [it["address"] for c in carried for it in c["files"] if it["address"] in paths]
        if in_vendor:
            page_lists[page] = sorted(in_vendor, key=paths.index)
    doc = {
        "format": FORMAT,
        "rule": "Third-party libraries carried byte for byte when identified, under a licence "
        "that allows redistribution, and free of keys, tokens and internal hosts; the "
        "application's own scripts are never carried by this rule.",
        "libraries": carried,
        "not_carried": refused,
        "own_scripts_not_carried": sorted(set(own_code)),
        "pages": page_lists,
        "fonts": fonts,
        "font_mode": font_mode(),
        "notices": NOTICES,
    }
    lookfeel._write(out / FILE, json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    if carried or fonts:
        lookfeel._write(app / NOTICES, notices(app_id, doc))
    return doc


def _confirmed(group: Group) -> bool:
    """Whether more than a file name says a file is the library: its own content states the
    version, or it is served from a package folder or a public library host."""
    from labctl.libraries import CONTENT

    if group.version_from == CONTENT or group.found_by in ("its package folder", "its address",
                                                           "its JSF resource address"):  # fmt: skip
        return True
    return all(re.search(r"/(?:node_modules|bower_components)/", f.path)
               or f.host in _public_hosts() for f in group.files)  # fmt: skip


def _public_hosts() -> set[str]:
    from labctl import codemap

    return codemap.PUBLIC_HOSTS


def _look_fonts(root: Path, app_id: str) -> list[dict]:
    """The font files `look/` carries, with their licence (from `look/manifest.json`)."""
    from labctl import lookfeel

    manifest = lookfeel.look_dir(root, app_id) / lookfeel.MANIFEST
    if not manifest.is_file():
        return []
    try:
        doc = json.loads(manifest.read_text(encoding="utf-8"))
    except ValueError:
        return []
    out = []
    for font in doc.get("fonts") or []:
        for f in font.get("files") or []:
            if not f.get("bundled"):
                continue
            path = lookfeel.look_dir(root, app_id) / f["bundled"]
            if not path.is_file():
                continue
            data = path.read_bytes()
            out.append({k: v for k, v in {
                "family": font["family"], "file": f"{lookfeel.LOOK_DIR}/{f['bundled']}",
                "address": f["path"], "format": f.get("format"), "weight": f.get("weight"),
                "style": f.get("style"), "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data), "licence": f.get("licence"),
                "licence_from": f.get("licence_from"), "copyright": f.get("copyright"),
                "warning": f.get("warning"),
            }.items() if v is not None})  # fmt: skip
    return out


# --- the notices --------------------------------------------------------------------------------


def licence_text(spdx: str) -> str | None:
    path = LICENCE_DIR / f"{spdx}.txt"
    return path.read_text(encoding="utf-8") if path.is_file() else None


def notices(app_id: str, doc: dict) -> str:
    app = f"apps/{app_id}"
    w = [f"# Third-party notices: `{app_id}`", "",
         "The open-source files this package carries, as the recorded application delivered "
         "them, with their licences. They are third-party software and fonts, not the "
         "application's own code and not data. Keep this file with them.", ""]  # fmt: skip
    used: list[str] = []
    if doc.get("libraries"):
        w += ["## Client libraries", ""]
        for lib in doc["libraries"]:
            spdx = lib.get("licence")
            used += [spdx] if spdx and spdx not in used else []
            w += [f"### {lib['name']} {lib.get('version') or '(version not stated)'}", "",
                  f"- Licence: {REDISTRIBUTABLE.get(spdx, spdx)} (`{spdx}`), from "
                  f"{lib.get('licence_from')}."]  # fmt: skip
            if lib.get("copyright"):
                w.append(f"- Copyright: {lib['copyright']}")
            if lib.get("home"):
                w.append(f"- Project: {lib['home']}")
            w.append(f"- In the package: `{app}/{lib['folder']}/`")
            w += ["", "| File | Original address | sha256 | Bytes |", "|---|---|---|---|"]
            for f in lib["files"]:
                extra = f" ({f['licence']})" if f.get("licence") else ""
                w.append(f"| `{app}/{f['file']}`{extra} | `{f['address']}` | `{f['sha256']}` | "
                         f"{f['bytes']} |")  # fmt: skip
                if f.get("licence") and f["licence"] not in used:
                    used.append(f["licence"])
            w.append("")
    if doc.get("fonts"):
        w += ["## Web fonts", ""]
        w += ["| Family | File | Original address | Licence | Copyright | sha256 |",
              "|---|---|---|---|---|---|"]  # fmt: skip
        for f in doc["fonts"]:
            spdx = f.get("licence")
            if spdx and spdx not in used:
                used.append(spdx)
            lic = (f"{REDISTRIBUTABLE.get(spdx, spdx)} (`{spdx}`), from {f.get('licence_from')}"
                   if spdx else f"**not identified**: {f.get('warning') or 'carried on request'}")  # fmt: skip
            w.append(f"| {f['family']} | `{app}/{f['file']}` | `{f['address']}` | {lic} | "
                     f"{(f.get('copyright') or '').replace('|', '/')} | `{f['sha256']}` |")  # fmt: skip
        w.append("")
        if any(not f.get("licence") for f in doc["fonts"]):
            w += ["**Warning:** some font files above were carried on the owner's request "
                  "(`TWINLAB_LOOK_FONTS=bundle`) although their licence was not identified. "
                  "Check that you may distribute them before you do.", ""]  # fmt: skip
    if doc.get("not_carried"):
        w += ["## Named, not carried", ""]
        for lib in doc["not_carried"]:
            w.append(f"- {lib['name']}{' ' + lib['version'] if lib.get('version') else ''} "
                     f"({', '.join('`' + a + '`' for a in lib['addresses'][:4])}): "
                     f"{lib['reason']}.")  # fmt: skip
        w.append("")
    if used:
        w += ["## Licence texts", "",
              "The standard text of each licence used above. Where a file names its copyright "
              "holders (above), they take the place of the placeholder in the text.", ""]  # fmt: skip
        for spdx in used:
            text = licence_text(spdx)
            if text:
                w += [f"### {REDISTRIBUTABLE.get(spdx, spdx)} (`{spdx}`)", "", "```text",
                      text.strip("\n"), "```", ""]  # fmt: skip
    return "\n".join(w).rstrip("\n") + "\n"


# --- the package --------------------------------------------------------------------------------


def load(root: Path, app_id: str) -> dict | None:
    path = Path(root) / "apps" / app_id / DIR / FILE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def package_files(root: Path, app_id: str) -> dict[str, Path]:
    app = Path(root) / "apps" / app_id
    out: dict[str, Path] = {}
    folder = app / DIR
    for p in sorted(folder.rglob("*")) if folder.is_dir() else []:
        if p.is_file() and not p.is_symlink():
            out[f"apps/{app_id}/{p.relative_to(app).as_posix()}"] = p
    if (app / NOTICES).is_file():
        out[f"apps/{app_id}/{NOTICES}"] = app / NOTICES
    return out


def package_check(root: Path, app_id: str) -> list[str]:
    """Why `vendor/` may not be packaged: a file `vendor.json` does not list or whose sha256
    differs, a text file the check refuses now, a library under a licence not on the list."""
    app = Path(root) / "apps" / app_id
    folder = app / DIR
    if not folder.is_dir():
        return []
    doc = load(root, app_id)
    if doc is None:
        return [f"apps/{app_id}/{DIR} has no readable {FILE}: generate the brief again"]
    listed = {f["file"]: f for lib in doc.get("libraries") or [] for f in lib.get("files") or []}
    problems = []
    for lib in doc.get("libraries") or []:
        if lib.get("licence") not in REDISTRIBUTABLE:
            problems.append(f"{lib['name']}: licence `{lib.get('licence')}` does not allow "
                            "redistribution")  # fmt: skip
    for p in sorted(folder.rglob("*")):
        if not p.is_file() or p.name == FILE and p.parent == folder:
            continue
        rel = p.relative_to(app).as_posix()
        item = listed.get(rel)
        data = p.read_bytes()
        if item is None:
            problems.append(f"{rel} is not a file {FILE} lists")
            continue
        if item["sha256"] != hashlib.sha256(data.replace(b"\r\n", b"\n") if p.suffix.lower()
                                            in TEXT_SUFFIXES else data).hexdigest():  # fmt: skip
            problems.append(f"{rel} is not the file the brief step carried (its sha256 differs)")
            continue
        if p.suffix.lower() in (".js", ".mjs", ".css", ".json", ".svg"):
            problems += [f"{rel}: {x}" for x in findings(data.decode("utf-8", "replace"))]
    return problems


# --- what the brief, the documents and the task say ----------------------------------------------


def _addr(items: list[str], cap: int = 4) -> str:
    shown = ", ".join(f"`{a}`" for a in items[:cap])
    return shown + (f" and {len(items) - cap} more" if len(items) > cap else "")


def library_rows(doc: dict, app: str) -> list[str]:
    rows = ["| Library | Version | Licence | Files in the package (original address) |",
            "|---|---|---|---|"]  # fmt: skip
    for lib in doc.get("libraries") or []:
        files = "; ".join(f"`{app}/{f['file']}` (`{f['address']}`)" for f in lib["files"][:6])
        more = len(lib["files"]) - 6
        rows.append(f"| {lib['name']} | {lib.get('version') or 'not stated'} | "
                    f"{lib.get('licence')} | {files}{f'; and {more} more' if more > 0 else ''} |")  # fmt: skip
    return rows


def use_text(app: str) -> str:
    return ("Use these exact library files and versions. Serve each at the address the pages "
            "reference (the original address above, for example by serving "
            f"`{app}/{DIR}/<library>/<version>/` at the folder the original served it from), or "
            "from your own static folder with the page references adjusted. Do not replace a "
            "library with another one, a newer version or code of your own that imitates it "
            "(dialogs, carousels, tooltips, the video player): load the library and call it as "
            "the original does.")  # fmt: skip


def brief_section(root: Path, app_id: str) -> str:
    """The brief's section on the libraries and fonts carried ("" when none is identified)."""
    doc = load(root, app_id)
    if doc is None:
        return ""
    app = f"apps/{app_id}"
    w = [f"## {HEADING}", ""]
    if doc.get("libraries"):
        w += [f"The package carries the third-party client libraries the application loaded, "
              f"byte for byte, under `{app}/{DIR}/` (`{app}/{DIR}/{FILE}` lists each file with "
              f"its original address and sha256; `{app}/{NOTICES}` holds their licences):", ""]  # fmt: skip
        w += library_rows(doc, app) + [""]
        w += [use_text(app), ""]
        if doc.get("pages"):
            w += ["Which page loads which carried file, in the order the page loads them:", ""]
            w += [f"- `{p}`: {_addr(a, 12)}" for p, a in doc["pages"].items()]
            w.append("")
        missing = sorted({m for lib in doc["libraries"] for m in lib.get("not_in_recording") or []})
        if missing:
            w += ["Files the libraries' style sheets name that the recording never loaded (not "
                  "carried; the library works without them or they were not needed): "
                  + _addr(missing, 8) + ".", ""]  # fmt: skip
    if doc.get("not_carried"):
        w += ["Named, not carried (use the same library and version, obtained under its own "
              "licence):", ""]  # fmt: skip
        w += [
            f"- {x['name']}{' ' + x['version'] if x.get('version') else ''} "
            f"({_addr(x['addresses'])}): {x['reason']}."
            for x in doc["not_carried"]
        ]
        w.append("")
    if doc.get("fonts"):
        w += [f"Web fonts carried in `{app}/look/fonts/` (the carried style sheets in "
              f"`{app}/look/css/` point at them; licences in `{app}/{NOTICES}`): "
              + ", ".join(f"{f['family']} (`{f['file']}`, {f.get('licence') or 'licence not identified'})"
                          for f in doc["fonts"][:12]) + ".", ""]  # fmt: skip
    if doc.get("own_scripts_not_carried"):
        w += ["The application's own scripts are not carried by this rule ("
              + _addr(doc["own_scripts_not_carried"], 6) + "): build their behaviour from the "
              "brief.", ""]  # fmt: skip
    return "\n".join(w) + "\n"


def rule_text(app: str, doc: dict | None) -> str:
    """The sentence the *same technology* rule adds when libraries are carried."""
    if not doc or not doc.get("libraries"):
        return ""
    names = ", ".join(f"{x['name']} {x.get('version') or '(version as carried)'}"
                      for x in doc["libraries"])  # fmt: skip
    return (f" The exact library files are available in `{app}/{DIR}/` ({names}; licences in "
            f"`{app}/{NOTICES}`): use them as they are, never a stand-in of your own.")  # fmt: skip


def task_text(root: Path, app_id: str) -> str:
    doc = load(root, app_id)
    if doc is None or not (doc.get("libraries") or doc.get("fonts")):
        return ""
    app = f"apps/{app_id}"
    w = ["## Same libraries and fonts", ""]
    if doc.get("libraries"):
        w += [f"The package carries the application's third-party client libraries in "
              f"`{app}/{DIR}/`, exactly as the browser received them:", ""]  # fmt: skip
        w += [
            f"- {x['name']} {x.get('version') or '(version not stated)'} ({x.get('licence')}): "
            f"`{app}/{x['folder']}/`"
            for x in doc["libraries"]
        ]
        w += ["", use_text(app), ""]
    if doc.get("fonts"):
        w += [f"The web fonts are in `{app}/look/fonts/`, and the style sheets in `{app}/look/css/` "
              "already point at them: serve them so your screens use the real typography.", ""]  # fmt: skip
    w += [f"Keep `{app}/{NOTICES}` with these files.", "", ""]
    return "\n".join(w)


def page_lines(doc: dict | None, app: str, path: str) -> list[str]:
    """For a screen's page file: the carried libraries its page loads."""
    if not doc or not doc.get("libraries"):
        return []
    from labctl.codemap import path_matches

    refs = next((a for p, a in (doc.get("pages") or {}).items()
                 if p == path or path_matches(path, p) or path_matches(p, path)), None)  # fmt: skip
    if not refs:
        return []
    where = {f["address"]: (lib, f) for lib in doc["libraries"] for f in lib["files"]}
    out = ["Third-party files this page loads, in order (use these exact files; "
           f"`{app}/{DIR}/{FILE}`):", ""]  # fmt: skip
    for a in refs:
        lib, f = where[a]
        out.append(f"- `{a}`: {lib['name']} {lib.get('version') or ''}".rstrip()
                   + f" (`{app}/{f['file']}`)")  # fmt: skip
    return out
