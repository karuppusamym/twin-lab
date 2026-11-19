"""Which third-party library a script or style sheet is, and its version.

One identification for the code map (`codemap.py`) and the page rules (`pagerules.py`), so both
name a library the same way:

- **Name**: a JSF resource library (`?ln=primefaces`), a public library host's address
  (`/ajax/libs/jquery/3.7.1/...`), the file's own name (`sweetalert.min.js` is SweetAlert even
  under `/node_modules/bootstrap-sweetalert/`), the package folder it is served from
  (`/node_modules/video.js/dist/video-js.min.css` is video.js), the rest of its address, a
  licence banner at the top of the file.
- **Version**: what the file's own content says first: a banner (`Bootstrap v4.0.0-alpha.6`,
  `/*! jQuery v3.2.1`, `/*! name vX.Y.Z */`), `@version`, a `VERSION = "..."` assignment in a
  library, JSF's `jsf.specversion`. Only when the content says nothing is the version read from
  the address (`jquery-3.7.1.min.js`, `?v=12.0.0`), and then it is marked as a guess
  (`version_from` is `ADDRESS`): a package folder or a cache-busting number is not a version.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl

CONTENT = "the file's own code"
ADDRESS = "its address (a guess: the file does not state it)"

# By file name first, then by the rest of the address. Two-word names before one-word names.
NAMES = [
    (r"jquery[.-]validate[.-]unobtrusive", "jQuery Validation Unobtrusive"),
    (r"jquery[.-]validat", "jQuery Validation"),
    (r"jquery[.-]?mask|jquery[.-]inputmask|inputmask", "Inputmask"),
    (r"jquery[.-]ui", "jQuery UI"), (r"jquery[.-]migrate", "jQuery Migrate"),
    (r"sweetalert2", "SweetAlert2"), (r"sweet-?alert", "SweetAlert"),
    (r"jquery", "jQuery"),
    (r"(^|/)angular(js)?([.-][\d.]+)?(\.min)?\.js$|/angularjs/", "AngularJS"),
    (r"react(-dom)?([.-]production|[.-]development)?(\.min)?\.js$", "React"),
    (r"(^|/)vue([.-][\d.]+|\.global|\.runtime|\.esm)?[^/]*\.js$", "Vue"),
    (r"bootstrap", "Bootstrap"), (r"popper", "Popper"), (r"lodash", "Lodash"),
    (r"underscore", "Underscore"), (r"moment", "Moment"), (r"knockout", "Knockout"),
    (r"backbone", "Backbone"), (r"(^|/)(jsf|faces)(-uncompressed)?\.js", "JSF"),
    (r"primefaces", "PrimeFaces"),
    (r"select2", "Select2"), (r"datatables", "DataTables"), (r"chart(\.umd)?(\.min)?\.js", "Chart.js"),
    (r"(^|/)d3(\.v\d)?(\.min)?\.js", "D3"), (r"zone(\.min)?\.js", "zone.js"),
    (r"(^|/)polyfills?([.-][\w]+)?\.js", "polyfills"), (r"core-js", "core-js"),
    (r"(^|/)(vendors?|chunk-vendors)([.~-][\w]+)?\.js", "vendor bundle"), (r"htmx", "htmx"),
    (r"alpine", "Alpine.js"), (r"axios", "axios"), (r"webforms|microsoftajax|webresource\.axd|"
                                                   r"scriptresource\.axd", "ASP.NET Web Forms"),
    (r"(^|/)(gtag|gtm|analytics|ga)\.js|googletagmanager|hotjar|newrelic|sentry", "analytics"),
]  # fmt: skip
# Libraries known by the first words of their file name (`rxjs.umd.min.js`, `tinymce-5.js`),
# named as written. Two-word names first.
STEMS = ("react-router-dom", "react-router", "vue-router", "font-awesome", "ext-all", "rxjs",
         "primeng", "highcharts", "require", "tinymce", "ckeditor", "fontawesome", "materialize",
         "foundation", "modernizr", "handlebars", "mustache", "three", "leaflet", "echarts",
         "swiper", "toastr", "mojarra", "myfaces", "richfaces", "icefaces", "dojo", "kendo",
         "telerik", "hammer", "stimulus", "turbo", "preact", "svelte", "ember", "angular")  # fmt: skip
JSF_LIBRARIES = {"primefaces", "javax.faces", "jakarta.faces", "omnifaces", "bootsfaces",
                 "richfaces", "icefaces", "mojarra", "myfaces", "faces"}  # fmt: skip
ANOTHER_ORIGIN = "a script or style sheet from another origin"
BUNDLE = "vendor bundle"

VERSION = r"\d+\.\d+(?:\.\d+)?(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?"
_BANNER = re.compile(r"^\s*/\*[!*]?\s*(?:@license\s+)?([A-Z][\w.]*(?: [A-Z][\w.]*){0,3})\s+v?(\d+\.\d+"
                     r"(?:\.\d+)?)", re.IGNORECASE)  # fmt: skip
_COMMENT = re.compile(r"/\*[\s\S]*?\*/|//[^\n]*")


@dataclass(frozen=True)
class Library:
    name: str
    version: str | None = None
    version_from: str | None = None  # CONTENT or ADDRESS (None: no version known)
    found_by: str = "its file name"

    @property
    def label(self) -> str:
        return self.name + (f" {self.version}" if self.version else "")


def _clean(v: str) -> str:
    return v.rstrip(".-")


def version_in_text(text: str | None, name: str | None = None) -> str | None:
    """A library's version as its own content states it (None when it does not)."""
    if not text:
        return None
    m = re.search(r"specversion\s*=\s*(\d)(\d)(\d{3})\b", text[:200_000])
    if m:
        return f"specification {int(m.group(1))}.{int(m.group(2))}"
    head = text[:6000]
    comments = "\n".join(c.group(0) for c in _COMMENT.finditer(head))
    if name:  # the library's own name with a version: `Bootstrap v4.0.0-alpha.6`, `Video.js 7.6.5`
        words = r"[\s.-]?".join(re.escape(w) for w in re.split(r"[\s.-]+", name) if w)
        m = re.search(rf"(?<![\w-]){words}(?:\.js)?\s*[:,]?\s*(?:version\s+)?v?({VERSION})(?!\d)",
                      comments, re.IGNORECASE)  # fmt: skip
        if m:
            return _clean(m.group(1))
    m = re.search(rf"@version\s+v?({VERSION})", comments)
    if m:
        return _clean(m.group(1))
    first = re.match(r"\s*(/\*[\s\S]*?\*/)", head)
    if first:  # `/*! name vX.Y.Z */`: the first banner of the file
        body = re.sub(r"^\s*\*+", "", first.group(1)[2:-2], flags=re.M)
        m = re.search(rf"\bv({VERSION})(?!\d)", body) or re.search(
            r"^[\s!]*(?:@license\s+)?[A-Za-z][\w.@/-]*(?: [A-Za-z][\w.@/-]*){0,3}\s+"
            r"(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)", body, re.M)  # fmt: skip
        if m:
            return _clean(m.group(1))
    if name:  # a library's `VERSION = "4.0.0"` (`var VERSION='4.0.0-alpha.6'`, `x.VERSION="1.2"`)
        m = re.search(rf"\bVERSION\s*[=:]\s*[\"']v?({VERSION})[\"']", text[:3_000_000])
        if m:
            return _clean(m.group(1))
    return None


def _address(path: str, query: str, own: bool) -> tuple[str, str | None, str] | None:
    """(name, version from the address, how) by the address alone."""
    q = dict(parse_qsl(query or ""))
    low = (path or "").lower()
    m = re.search(r"/(?:javax|jakarta)\.faces\.resource/(.+?)(?:\.xhtml|\.jsf|\.faces)?$", low)
    if m:
        ln = (q.get("ln") or "").lower()
        if ln in JSF_LIBRARIES or not ln and m.group(1) in ("jsf.js", "faces.js"):
            return ln or "javax.faces", q.get("v"), "its JSF resource address"
        return "", None, ""  # the application's own JSF resource
    if not own:
        cdn = re.search(
            r"/(?:ajax/libs|npm|gh)/(@?[\w.-]+(?:/[\w.-]+)?)[@/]v?(\d+\.\d+[\w.-]*)", low
        )
        if cdn:
            return cdn.group(1), cdn.group(2), "its address"
        cdn = re.search(r"^/(@?[\w.-]+)@(\d+\.\d+[\w.-]*)/", low)
        if cdn:
            return cdn.group(1), cdn.group(2), "its address"
    file = low.rsplit("/", 1)[-1]
    stem = re.sub(r"\.(?:min\.)?(?:m?js|css)$", "", file)
    vq = q.get("v") or q.get("ver")
    for rx, name in NAMES:
        if file and re.search(rx, file):
            return name, _version_after(stem) or vq, "its file name"
    pkg = re.search(r"(?:^|/)(?:node_modules|bower_components)/((?:@[\w.-]+/)?[\w.-]+)/", low)
    if pkg:  # a file served from a package folder is that package
        return pkg.group(1), vq, "its package folder"
    for rx, name in NAMES:
        if re.search(rx, low):
            return name, vq, "its address"
    for lib in STEMS:
        if stem == lib or re.match(rf"{re.escape(lib)}(?:[.\-_~@]|\d|$)", stem):
            return lib, _version_after(stem[len(lib) :]) or vq, "its file name"
    return None


def _version_after(text: str) -> str | None:
    m = re.search(r"(?<![\d.])v?(\d+\.\d+(?:\.\d+)?(?:[-.](?:rc|beta|alpha)\.?\d*)?)(?![\d])", text)
    return m.group(1) if m else None


def identify(path: str, text: str | None = None, *, own: bool = True,
             query: str = "") -> Library | None:  # fmt: skip
    """The library a script or style sheet is, with its version (None: the application's own
    file). `own` is False for a file from another origin."""
    found = _address(path, query, own)
    if found is not None and not found[0]:
        return None
    if found is None:
        head = (text or "")[:600]
        m = _BANNER.match(head)
        if m and not re.search(r"\bapp(lication)?\b", m.group(1), re.IGNORECASE):
            found = (m.group(1), None, "a licence banner in the code")
        elif (
            re.search(r"@license|\(c\) \d{4}.*(?:MIT|Apache|BSD)", head) and len(text or "") > 20000
        ):
            found = ("a library (licence banner)", None, "a licence banner in the code")
        elif not own:
            return Library(ANOTHER_ORIGIN, found_by="its address (another origin)")
        else:
            return None
    name, guess, how = found
    if name == BUNDLE:
        return Library(name, found_by=how)
    stated = version_in_text(text, name)
    if stated:
        return Library(name, stated, CONTENT, how)
    if guess:
        return Library(name, guess, ADDRESS, how)
    return Library(name, None, None, how)


def group(rows: list[dict]) -> list[dict]:
    """Files of the same library and version as one entry listing its files. A file whose version
    is not known joins the entry of its library when that library has exactly one version."""
    out: dict[tuple[str, str | None], dict] = {}
    for r in rows:
        key = (r["name"], r.get("version"))
        have = out.get(key)
        if have is None:
            out[key] = {**r, "files": list(r.get("files") or [])}
            continue
        for f in r.get("files") or []:
            if f not in have["files"]:
                have["files"].append(f)
        if have.get("version_from") == ADDRESS and r.get("version_from") == CONTENT:
            have["version_from"] = CONTENT
    for key in [k for k in out if k[1] is None]:
        versions = [k for k in out if k[0] == key[0] and k[1] is not None]
        if len(versions) == 1:
            into = out[versions[0]]
            for f in out.pop(key)["files"]:
                if f not in into["files"]:
                    into["files"].append(f)
    return list(out.values())
