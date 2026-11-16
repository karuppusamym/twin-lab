"""Privacy for recorded evidence: drop credentials, reduce bodies to shapes, mask page text.

A recording of a real system holds customer data, cookies and tokens. Nothing recorded is kept as
a value. Headers that carry credentials are dropped as soon as a request is read; bodies become
shapes (field names and types); page snapshots keep their structure and the words the application
itself puts on the screen (labels, headings, buttons, table headers, options, titles, placeholders)
and shape-mask every other text and every value: `Smith` -> `Xxxxx`, `1001` -> `9999`.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

# --- headers ---------------------------------------------------------------------------------------

CREDENTIAL_HEADER = re.compile(
    r"^(cookie|set-cookie|set-cookie2|authorization|proxy-authorization|www-authenticate|"
    r"proxy-authenticate|x-.*-token|x-.*-key|x-api-key|x-auth.*|x-csrf.*|x-xsrf.*)$"
    r"|token|secret|passw|api[-_]?key|session|cookie|auth|credential|signature|csrf|xsrf|jwt",
    re.IGNORECASE,
)


def is_credential_header(name: str) -> bool:
    return bool(CREDENTIAL_HEADER.search(name.strip()))


def safe_headers(headers: list[dict] | None) -> dict[str, str]:
    """Headers without anything that could be a credential, lower-cased names."""
    out: dict[str, str] = {}
    for h in headers or []:
        name = str(h.get("name", "")).strip().lower()
        if name and not is_credential_header(name):
            out[name] = str(h.get("value", ""))
    return out


# --- values ----------------------------------------------------------------------------------------


def mask(text: str) -> str:
    """The shape of a value: letters to x/X, digits to 9, everything else kept."""
    return "".join(
        ("X" if c.isupper() else "x") if c.isalpha() else "9" if c.isdigit() else c for c in text
    )


IDENTIFIER_ATTRS = {"id", "class", "name", "for", "href", "action", "src", "aria-controls", "aria-labelledby",
                    "aria-describedby", "headers", "list", "form"}  # fmt: skip
DATA_TOKEN = re.compile(r"[A-Za-z0-9]*\d[A-Za-z0-9]*")


def mask_identifiers(value: str) -> str:
    """Mask record identifiers built into attribute values: any part with 3 or more digits.

    `loan-L-200040` -> `loan-L-999999`, `/reviews/R-1001` -> `/reviews/R-9999`. Short codes such
    as `CRD-01` are application constants and stay as they are.
    """
    return DATA_TOKEN.sub(
        lambda m: mask(m.group()) if sum(c.isdigit() for c in m.group()) >= 3 else m.group(), value
    )


def mask_attributes(attrs):
    return [
        (
            k,
            mask_identifiers(v)
            if v and (k.lower() in IDENTIFIER_ATTRS or k.lower().startswith("data-"))
            else v,
        )
        for k, v in attrs
    ]


ID_SEGMENT = re.compile(
    r"^(\d+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{16,}|"
    r"(?=[^/]*\d)[A-Za-z0-9][A-Za-z0-9._~%+-]*)$",
    re.IGNORECASE,
)
VERSION_SEGMENT = re.compile(r"^v\d+(\.\d+)*$", re.IGNORECASE)


def is_id_segment(segment: str) -> bool:
    """Whether a path segment is a value (a number, UUID, hash, code with digits, e-mail, key)."""
    if not segment or segment.startswith("{") or VERSION_SEGMENT.match(segment):
        return False
    if any(c in segment for c in "@:,=;"):  # jane@bank.com, 1234:close, a=b: never a route name
        return True
    if re.search(r"\.(js|css|html?|png|jpe?g|gif|svg|ico|woff2?|map|json)$", segment, re.I):
        return False
    return bool(ID_SEGMENT.match(segment))


def route_pattern(path: str) -> str:
    """`/reviews/R-1001` -> `/reviews/{id}`; a second value becomes `{id2}` and so on."""
    path = urlsplit(path).path or "/"
    out, n = [], 0
    for seg in path.split("/"):
        seg = seg.split(";", 1)[0]  # matrix parameters such as ;jsessionid=... are dropped
        if is_id_segment(seg):
            n += 1
            out.append("{id}" if n == 1 else f"{{id{n}}}")
        else:
            out.append(seg)
    return "/".join(out) or "/"


SAFE_WORD = re.compile(r"^([^\d@]*|\(?\d{1,3}[).,:;]?)$")  # no digits, or a small number


def words(value) -> set[str]:
    """The words in a value (strings in JSON, page text), to recognise them in messages."""
    out: set[str] = set()
    if isinstance(value, str):
        out.update(w.lower() for w in re.findall(r"[^\W\d_]{2,}", value))
    elif isinstance(value, dict):
        for k, v in value.items():
            out |= words(v)
            if not re.match(r"^[A-Za-z_][\w-]*$", str(k)):
                out |= words(str(k))
    elif isinstance(value, list):
        for v in value[:500]:
            out |= words(v)
    return out


EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
NAME_WORD = re.compile(r"^[A-Z][a-z'’-]+$")  # Title-case word: how names are written


def app_text(text: str, known: set[str] = frozenset(), names: bool = True) -> str:
    """Text the page presents as the application's own words (labels, headings, titles, options),
    with anything that looks like data masked:

    - e-mail addresses, and parts with 3 or more digits (record numbers);
    - what follows a colon in a label-value heading (`Customer: Jane Doe`);
    - Title-case words the same recording shows elsewhere as data;
    - with `names` (headings, titles, options, tooltips): any run of two or more Title-case words,
      which is how names and addresses are written ("Jane Doe", "Elm Street"). Button, label and
      column-header text is passed with `names=False`: that is where an app's own Title Case lives.
    """
    text = EMAIL.sub(lambda m: mask(m.group()), text)
    text = mask_identifiers(text)
    head, sep, tail = text.partition(":")
    if sep and tail.strip() and len(head.split()) <= 4 and not tail.strip().startswith("//"):
        text = head + sep + mask(tail)
    parts = re.split(r"(\s+)", text)
    bare = [re.sub(r"[^\w'’-]", "", p) for p in parts]
    title = [bool(NAME_WORD.match(b)) for b in bare]
    out = []
    for i, part in enumerate(parts):
        if part.isspace() or not part:
            out.append(part)
            continue
        neighbours = [j for j in (i - 2, i + 2) if 0 <= j < len(parts)]
        in_run = names and title[i] and any(title[j] for j in neighbours)
        seen = title[i] and bare[i].lower() in known
        out.append(mask(part) if in_run or seen else part)
    return "".join(out)


def mask_message(text: str, values: set[str] = frozenset()) -> str:
    """An application message with any value-like word masked (`Case C-1001` -> `Case X-9999`).

    A word is masked when it holds digits or `@`, or when it is capitalised and was seen as a value
    somewhere in the same evidence (`values`, lower-case): names in messages. Numbers of up to
    three digits are kept: in a message they are usually limits ("at least 10 characters").
    """
    out = []
    for i, w in enumerate(re.split(r"(\s+)", text.strip())):
        bare = re.sub(r"[^\w]", "", w)
        named = i > 0 and bare[:1].isupper() and bare.lower() in values
        out.append(mask(w) if named or not SAFE_WORD.match(w) else w)
    return "".join(out)[:300]


# --- page snapshots --------------------------------------------------------------------------------

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
        "source", "track", "wbr"}  # fmt: skip
DROP_WITH_CONTENT = {"script", "template"}
KEEP_TEXT = {"label", "h1", "h2", "h3", "h4", "h5", "h6", "button", "th", "option", "optgroup",
             "title", "legend", "caption", "dt", "summary"}  # fmt: skip
# Inline tags that do not change whose text it is: `<button><span>Save</span></button>`.
PASS_THROUGH = {"span", "b", "strong", "i", "em", "small", "abbr", "u", "sup", "sub", "kbd", "mark",
                "bdi", "font", "nobr"}  # fmt: skip
INTERACTIVE = {"input", "select", "textarea", "button", "a", "form"}
BUTTON_TYPES = {"submit", "button", "reset"}
KEEP_ATTRS = {
    "id", "class", "name", "type", "for", "role", "placeholder", "title", "aria-label", "lang",
    "aria-labelledby", "aria-describedby", "aria-hidden", "aria-expanded", "aria-selected",
    "disabled", "readonly", "required", "checked", "selected", "multiple", "hidden", "rows",
    "cols", "size", "maxlength", "minlength", "min", "max", "step", "pattern", "autocomplete",
    "method", "enctype", "colspan", "rowspan", "scope", "tabindex", "charset", "rel", "media",
    "width", "height", "style", "dir", "novalidate", "ng-version", "data-reactroot", "data-v-app",
    "aria-live", "aria-modal", "aria-controls", "aria-current", "aria-invalid", "aria-required",
    "http-equiv", "viewport", "crossorigin", "inputmode", "spellcheck", "accept", "list",
}  # fmt: skip
URL_ATTRS = {"href", "src", "action", "formaction", "poster", "data"}


class _Page(HTMLParser):
    """Rebuilds a page without values, and collects what the model needs from it."""

    def __init__(self, known: set[str] = frozenset()):
        super().__init__(convert_charrefs=True)
        self.known = known  # words the recording shows as data elsewhere (lower-case)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.with_id: list[bool] = []  # per open element: has an id (then its text is a value)
        self.drop = 0  # inside a script or template
        self.ids: set[str] = set()
        self.classes: set[str] = set()
        self.elements: list[dict] = []
        self.labels: dict[str, str] = {}  # for= id -> label text
        self.open_label: list[dict] = []  # label being read: {"for", "text", "wraps"}
        self.open_text: list[dict] = []  # element whose kept text is being read
        self.select: dict | None = None
        self.option: list[str] | None = None
        self.markers: dict[str, str] = {}
        self.title = ""
        self.body_elements = 0
        self.in_body = False
        self.values: set[str] = set()  # words of every text and value masked

    # attributes ------------------------------------------------------------------------------
    def _attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        kind = (dict(attrs).get("type") or "").lower()
        parts = []
        for name, value in attrs:
            name = name.lower()
            if name.startswith("on") or name in ("srcdoc", "nonce", "integrity"):
                continue
            if value is None:
                parts.append(name)
                continue
            if name == "value":
                keep = tag == "input" and kind in BUTTON_TYPES
                if not keep:
                    self.values |= words(value)
                    value = mask(value)
            elif name in URL_ATTRS:
                value = _safe_url(value)
            elif name == "content" and tag == "meta":
                meta = dict(attrs)
                kind_ = (meta.get("name") or meta.get("http-equiv") or "").lower()
                # Only harmless metadata stays: tokens (csrf), user names and the like go.
                value = value if kind_ in ("viewport", "generator", "content-type") else ""
            elif name in ("title", "aria-label", "placeholder", "alt"):
                value = app_text(value, self.known)
            elif name == "style":
                value = re.sub(r"url\([^)]*\)", "url(#)", value)  # no recorded addresses or tokens
            elif name not in KEEP_ATTRS:
                self.values |= words(value)
                value = mask(value)
            parts.append(f'{name}="{escape(value, quote=True)}"')
        return (" " + " ".join(parts)) if parts else ""

    # tags ------------------------------------------------------------------------------------
    def handle_decl(self, decl: str) -> None:
        if decl.lower().startswith("doctype"):
            self.out.append(f"<!{decl}>")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, closed=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, closed=True)

    def _start(self, tag: str, attrs, closed: bool) -> None:
        attrs = mask_attributes(attrs)  # record numbers in ids and links never leave the evidence
        if self.drop:
            if tag in DROP_WITH_CONTENT and not closed:
                self.drop += 1
            return
        if tag in DROP_WITH_CONTENT:
            if not closed:
                self.drop = 1
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "body":
            self.in_body = True
        elif self.in_body and tag not in ("link", "noscript", "style"):
            self.body_elements += 1
        if "ng-version" in a:
            self.markers.setdefault("angular", a["ng-version"] or "")
        if "data-reactroot" in a:
            self.markers.setdefault("react", "data-reactroot")
        if "data-v-app" in a or any(k.startswith("data-v-") for k in a):
            self.markers.setdefault("vue", "data-v- attribute")
        if a.get("name") == "__VIEWSTATE" or a.get("id") == "__VIEWSTATE":
            self.markers.setdefault("aspnet-webforms", "__VIEWSTATE")
        if "javax.faces" in a.get("name", "") or "javax.faces" in a.get("id", ""):
            self.markers.setdefault("jsf", "javax.faces.ViewState")
        if a.get("id"):
            self.ids.add(a["id"])
        self.classes.update(c for c in a.get("class", "").split() if c)
        self._element(tag, a)

        self.out.append(f"<{tag}{self._attrs(tag, attrs)}{' /' if closed else ''}>")
        if tag not in VOID and not closed:
            self.stack.append(tag)
            row_header = tag == "th" and (a.get("scope") == "row" or "tbody" in self.stack)
            # A row header (or any element with an id) holds a value, not the app's words.
            self.with_id.append(row_header or (bool(a.get("id")) and tag not in KEEP_TEXT))

    def _element(self, tag: str, a: dict[str, str]) -> None:
        kind = a.get("type", "").lower()
        if tag == "label":
            self.open_label.append({"for": a.get("for"), "text": [], "wraps": None})
        el = None
        if a.get("id") or (tag in INTERACTIVE and a.get("name") and kind != "hidden"):
            el = {"tag": tag, "id": a.get("id") or None, "name": a.get("name") or None,
                  "type": kind or None, "classes": a.get("class", "").split(),
                  "label": a.get("aria-label") or a.get("placeholder") or a.get("title") or None,
                  "options": [], "text": []}  # fmt: skip
            if tag == "input" and kind in BUTTON_TYPES and a.get("value"):
                el["label"] = a["value"]
            if el["label"]:  # the same masking as the attribute gets in the page itself
                el["label"] = app_text(el["label"], self.known)
            self.elements.append(el)
            if self.open_label and self.open_label[-1]["wraps"] is None:
                self.open_label[-1]["wraps"] = el
        if tag == "select":
            self.select = el or {"options": []}
        if tag == "option":
            self.option = []
        if tag in KEEP_TEXT and tag not in ("label", "option"):
            self.open_text.append({"tag": tag, "el": el, "text": []})

    def handle_endtag(self, tag: str) -> None:
        if self.drop:
            if tag in DROP_WITH_CONTENT:
                self.drop -= 1
            return
        if tag not in self.stack:
            return
        while self.stack:
            top = self.stack.pop()
            self.with_id.pop()
            self.out.append(f"</{top}>")
            self._end(top)
            if top == tag:
                break

    def _end(self, tag: str) -> None:
        if tag == "label" and self.open_label:
            label = self.open_label.pop()
            text = " ".join("".join(label["text"]).split())
            if label["for"]:
                self.labels[label["for"]] = text
            elif label["wraps"] is not None and not label["wraps"]["label"]:
                label["wraps"]["label"] = text
        elif tag == "option" and self.option is not None:
            text = " ".join("".join(self.option).split())
            if self.select is not None and text:
                self.select["options"].append(text)
            self.option = None
        elif tag == "select":
            self.select = None
        elif self.open_text and self.open_text[-1]["tag"] == tag:
            item = self.open_text.pop()
            text = " ".join("".join(item["text"]).split())
            if tag == "title" and not self.title:
                self.title = text
            if item["el"] is not None and not item["el"]["label"]:
                item["el"]["label"] = text or None

    def handle_data(self, data: str) -> None:
        if self.drop:
            return
        keep = self._kept()
        if keep:
            own = next((t for t in reversed(self.stack) if t in KEEP_TEXT), "")
            # names, e-mails and record numbers in headings, titles and options are data
            data = app_text(data, self.known, names=own not in ("button", "label", "th", "legend"))
            for label in self.open_label:
                label["text"].append(data)
            for item in self.open_text:
                item["text"].append(data)
            if self.option is not None:
                self.option.append(data)
        if not keep and self._data_cell():
            self.values |= words(data)  # only text where records are shown counts as data
        self.out.append(escape(data if keep else mask(data), quote=False))

    def _data_cell(self) -> bool:
        """Inside a table or description cell, or an element with an id: where records show."""
        return any(t in ("td", "dd") for t in self.stack) or bool(self.with_id and self.with_id[-1])

    def _kept(self) -> bool:
        """Text is the application's own words only directly inside a label, heading, button..."""
        for tag, has_id in zip(reversed(self.stack), reversed(self.with_id), strict=True):
            if (tag in KEEP_TEXT and not (tag == "th" and has_id)) or tag == "style":
                return True
            if has_id or tag not in PASS_THROUGH:
                return False
        return False

    def handle_comment(self, data: str) -> None:
        return

    def close(self) -> None:
        super().close()
        while self.stack:
            top = self.stack.pop()
            self.with_id.pop()
            self.out.append(f"</{top}>")
            self._end(top)
        for el in self.elements:
            if el["id"] and el["id"] in self.labels and not el["label"]:
                el["label"] = self.labels[el["id"]] or None
            el.pop("text", None)


def _safe_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme in ("javascript", "data"):
        return "#"
    if parts.scheme in ("mailto", "tel", "sms"):
        return f"{parts.scheme}:{mask(value.split(':', 1)[1])}"
    path = route_pattern(parts.path) if parts.path else ""
    query = "&".join(f"{q.split('=', 1)[0]}=" for q in parts.query.split("&") if q)
    fragment = (
        f"#{parts.fragment}" if parts.fragment and not re.search(r"\d", parts.fragment) else ""
    )
    return path + (f"?{query}" if query else "") + fragment


class PageInfo:
    """A value-free page: its HTML, ids, classes, elements and framework markers."""

    def __init__(self, parser: _Page):
        self.html = "".join(parser.out)
        self.ids = parser.ids
        self.classes = parser.classes
        self.elements = parser.elements
        self.markers = parser.markers
        self.title = parser.title
        self.values = parser.values
        self.body_elements = parser.body_elements

    @property
    def has_content(self) -> bool:
        """False for an empty single-page-app shell (`<div id="root"></div>` and scripts)."""
        return self.body_elements > 3 or any(
            e["tag"] in ("input", "select", "textarea", "form") for e in self.elements
        )


def page(html: str, known: set[str] = frozenset()) -> PageInfo:
    """Parse a recorded page. The result holds no recorded value.

    `known` is every word the same recording shows as data elsewhere; headings and options that
    repeat one are masked too.
    """
    first = _Page(known)  # first pass: what this page itself shows as data
    first.feed(html)
    first.close()
    parser = _Page(set(known) | first.values)
    parser.feed(html)
    parser.close()
    return PageInfo(parser)
