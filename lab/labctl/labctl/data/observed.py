"""Shape synthetic data from what a recording showed: the *observed profile*.

A recording of a real system holds real values (the raw HAR sits in the evidence store, outside
Git). The profile is the only thing that may leave it, and it is deliberately lossy:

- A value is kept **verbatim** only when it belongs to a small closed vocabulary: a column whose
  name does not mark it as personal, with at most `MAX_VOCAB` distinct values, each short, made of
  words, with at most two digits, that repeats (or sits in a code-named column such as `status`),
  and is not a value seen in a personal column. Statuses, types, categories pass.
- Everything else is **shape only**: kind, pattern (`[R]-####`: literal prefix, `#` digit), length
  range, casing, number and date ranges rounded outward, null share, distinct count. Names, e-mail
  addresses, telephone numbers, addresses, notes and long identifiers are never copied.
- Anchors (the records a recorded flow navigates to) keep an identifier only when it has a
  case-number shape (`R-1001`) in a column that does not look like an account, loan or person key.
  Otherwise the profile holds the shape and the generator makes a stand-in.

The profile is a YAML file a person reads before use (`verbatim:` lists every kept value).
Reading the raw evidence needs the evidence store; reading a profile needs nothing else.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "Profile", "ProfileError", "build", "classify_column", "is_vocab_value", "pattern_of",
    "write", "summary", "keep_anchor_id", "make_from_pattern",
]  # fmt: skip

VERSION = 1
MAX_VOCAB = 12  # most distinct values a code list may have
MAX_VALUE_LEN = 30
MAX_WORDS = 4
MAX_DIGITS = 2  # a kept value has at most this many digits: longer digit runs are identifiers
MAX_ITEMS = 500  # rows read per response

# Column names that mark personal, free-text or secret content: shape only, whatever the values.
PERSONAL = re.compile(
    r"name|e_?mail|phone|mobile|tel$|tel_|fax|address|addr|street|city|town|zip|postal|post_?code|"
    r"country|region|birth|dob|ssn|social|passport|iban|card|account|acct|tax|licen[cs]e|national|"
    r"user|login|author|owner|assign|reviewer|officer|agent|operator|handler|reporter|approver|"
    r"borrower|customer|client|member|employee|staff|contact|patient|student|person|"
    r"note|comment|descr|text|message|reason|body|remark|detail|summary|title|subject|guidance|"
    r"explan|token|secret|passw|hash|salt|key$|_ip$|^ip_|ip_address|url|link|path|file|"
    r"property|location|geo|branch|province|site",
    re.IGNORECASE,
)
# Identifier columns that may belong to a person or an account: pattern without a literal prefix.
SENSITIVE_ID = re.compile(
    r"account|acct|card|iban|ssn|social|tax|passport|licen[cs]e|phone|policy|loan|mortgage|claim|"
    r"customer|client|member|patient|employee|student|person|user|contact|borrower|national",
    re.IGNORECASE,
)
CODE_NAME = re.compile(
    r"(status|state|type|kind|code|priority|severity|level|stage|role|mode|category|channel|"
    r"currency|outcome|result|decision|flag|class|grade|product|sub_?type)$|(^|_)(cat|categ)$",
    re.IGNORECASE,
)
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
PHONE = re.compile(r"^\+?[\d\s().-]{7,}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
VOCAB_VALUE = re.compile(r"^[A-Za-z][A-Za-z0-9]*([ _/.\-][A-Za-z0-9]+){0,%d}$" % (MAX_WORDS - 1))
IDENT = re.compile(r"^(?P<prefix>[A-Za-z]{1,4}[-_/]?)?(?P<digits>\d{2,12})$")
ANCHOR_ID = re.compile(r"^[A-Z]{1,4}[-_]\d{2,8}$")
AMOUNT_NAME = re.compile(r"amount|rate|total|price|fee|score|term|count|qty|quantity|ltv|percent|"
                         r"balance|cost|limit", re.IGNORECASE)  # fmt: skip
CODE_SHAPED = re.compile(
    r"[A-Z0-9][A-Z0-9_./-]*"
)  # how a code reads in a column not named like one
PEOPLE = ("user", "customer", "person", "people", "employee", "contact", "client", "member",
          "staff", "agent", "patient", "student", "borrower", "owner", "account", "loan",
          "policy", "claim")  # fmt: skip
ITEM_KEYS = ("items", "data", "results", "rows", "content", "records", "value", "list")


class ProfileError(ValueError):
    """A profile that holds something it must not, or is not a profile."""


# --- names ---------------------------------------------------------------------------------------


def snake(name: str) -> str:
    """`reviewNo`, `Review No`, `review-no` -> `review_no`."""
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name))
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def key(name: str) -> str:
    """Matching key: lower-case letters and digits only."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def singular(word: str) -> str:
    w = key(word)
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("sses", "xes", "ches", "shes")) and len(w) > 4:
        return w[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us")) and len(w) > 3:
        return w[:-1]
    return w


# --- shapes --------------------------------------------------------------------------------------


def pattern_of(values: list[str], literal_prefix: bool = True) -> str | None:
    """`['R-1001', 'R-1007']` -> `[R]-####`: a literal prefix shared by every value, `#` per digit.

    None unless every value is an optional short letter prefix followed by digits. The prefix is
    left out (`####`) when `literal_prefix` is False.
    """
    prefixes, lengths = set(), Counter()
    for v in values:
        m = IDENT.match(v)
        if not m:
            return None
        prefixes.add(m["prefix"] or "")
        lengths[len(m["digits"])] += 1
    if not lengths:
        return None
    prefix = prefixes.pop() if len(prefixes) == 1 else ""
    digits = lengths.most_common(1)[0][0]
    body = "#" * digits
    if prefix and literal_prefix:
        return f"[{prefix[:-1]}]{prefix[-1]}{body}" if prefix[-1] in "-_/" else f"[{prefix}]{body}"
    return body


def make_from_pattern(pattern: str, n: int, rng=None) -> str:
    """A value of the pattern: `[R]-####` with n=7 -> `R-0007`. `rng` randomises the digits."""
    out, digits, i = [], pattern.count("#"), 0
    number = str(n).zfill(digits)[-digits:] if digits else ""
    if rng is not None and digits:
        number = "".join(str(rng.randint(0, 9)) for _ in range(digits))
        number = str(rng.randint(1, 9)) + number[1:]
    n_i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "[" and "]" in pattern[i:]:
            j = pattern.index("]", i)
            out.append(pattern[i + 1 : j])
            i = j + 1
            continue
        if c == "#":
            out.append(number[n_i] if n_i < len(number) else "0")
            n_i += 1
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _case(values: list[str]) -> str | None:
    letters = [v for v in values if any(c.isalpha() for c in v)]
    if not letters:
        return None
    if all(v == v.upper() for v in letters):
        return "upper"
    if all(v == v.lower() for v in letters):
        return "lower"
    if all(v == v.title() or all(w[:1].isupper() for w in v.split()) for v in letters):
        return "title"
    return "mixed"


def _round_out(lo: float, hi: float) -> list[float | int]:
    """Bounds rounded outward to two significant figures: a range, not a record's value."""

    def sig(x: float, up: bool) -> float:
        if x == 0:
            return 0
        import math

        mag = 10 ** (math.floor(math.log10(abs(x))) - 1)
        q = x / mag
        q = math.ceil(q) if (up and x > 0) or (not up and x < 0) else math.floor(q)
        return q * mag

    a, b = sig(lo, False), sig(hi, True)
    ints = float(a).is_integer() and float(b).is_integer()
    return [int(a), int(b)] if ints else [round(a, 4), round(b, 4)]


# --- the classifier --------------------------------------------------------------------------------


def is_vocab_value(value: Any) -> bool:
    """Whether one value may be kept verbatim *if* its column and company allow it."""
    if not isinstance(value, str) or not value or len(value) > MAX_VALUE_LEN:
        return False
    if EMAIL.search(value) or PHONE.match(value) or DATE.match(value) or DATETIME.match(value):
        return False
    if sum(c.isdigit() for c in value) > MAX_DIGITS:
        return False
    if re.fullmatch(r"[Xx9][Xx9 ._/-]*", value):  # a masked value (`Xxxx`), not a word
        return False
    return bool(VOCAB_VALUE.match(value))


def classify_column(name: str, values: list[Any], personal_values: frozenset[str] = frozenset(),
                    ) -> dict[str, Any]:  # fmt: skip
    """The profile entry of one column from the values seen (`None` is a null). Never holds a value
    that is not in a code list; see the module docstring."""
    col = snake(name)
    seen = [v for v in values if v is not None and v != ""]
    out: dict[str, Any] = {"seen": len(seen)}
    if values:
        out["null_share"] = round(1 - len(seen) / len(values), 2)
    if not seen:
        out["kind"] = "unknown"
        return out
    out["distinct"] = len({json.dumps(v, sort_keys=True) for v in seen})
    if all(isinstance(v, bool) for v in seen):
        out["kind"] = "boolean"
        out["true_share"] = round(sum(seen) / len(seen), 2)
        return out
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in seen):
        if re.search(r"(^|_)(id|no|nr|num|number)$", col) or (
            (PERSONAL.search(col) or SENSITIVE_ID.search(col)) and not AMOUNT_NAME.search(col)
        ):  # fmt: skip
            out["kind"] = "identifier"
            out["pattern"] = "#" * max(len(str(int(abs(v)))) for v in seen)
            return out
        out["kind"] = "number"
        out["range"] = _round_out(min(seen), max(seen))
        if any(isinstance(v, float) and not v.is_integer() for v in seen):
            out["scale"] = min(4, max(len(repr(v).partition(".")[2].rstrip("0")) for v in seen
                                      if isinstance(v, float)))  # fmt: skip
        return out
    strings = [v for v in seen if isinstance(v, str)]
    if len(strings) != len(seen):
        out["kind"] = "other"
        return out
    out["length"] = [min(map(len, strings)), max(map(len, strings))]
    if all(EMAIL.fullmatch(v) for v in strings):
        out["kind"] = "email"
        return out
    if all(DATETIME.match(v) or DATE.match(v) for v in strings) and (
        all(DATE.match(v) for v in strings) or all(DATETIME.match(v) for v in strings)
    ):  # fmt: skip
        dated = all(DATE.match(v) for v in strings)
        out["kind"] = "date" if dated else "datetime"
        out["format"] = "YYYY-MM-DD" if dated else "ISO 8601"
        if not re.search(r"birth|dob", col):  # a birth date is personal: no range
            months = sorted(v[:7] for v in strings)
            out["range"] = [months[0], months[-1]]
        return out
    if all(PHONE.match(v) and sum(c.isdigit() for c in v) >= 7 for v in strings):
        out["kind"] = "phone"
        return out
    money = [_money(v) for v in strings]
    if all(m is not None for m in money) and not PERSONAL.search(col):
        out["kind"] = "number"
        out["range"] = _round_out(min(money), max(money))
        out["scale"] = 2 if any("." in v for v in strings) else 0
        out["format"] = "currency" if any("$" in v or "€" in v or "£" in v for v in strings) else (
            "percent" if any("%" in v for v in strings) else "plain")  # fmt: skip
        return out
    personal = bool(PERSONAL.search(col))
    if personal:
        out["kind"] = "personal"
        out["case"] = _case(strings)
        return out
    pattern = pattern_of(strings, literal_prefix=not SENSITIVE_ID.search(col))
    counts = Counter(strings)
    single = len(strings) == 1 and not re.fullmatch(r"[A-Z][A-Z_]{2,}", strings[0])
    closed = (
        not single
        and len(counts) <= MAX_VOCAB
        and all(is_vocab_value(v) for v in counts)
        and (CODE_NAME.search(col) is not None or all(CODE_SHAPED.fullmatch(v) for v in counts))
        and not any(v.lower() in personal_values for v in counts)
        and (
            CODE_NAME.search(col) is not None
            or all(n >= 2 for n in counts.values())
            or (len(strings) >= 2 and len(counts) < len(strings))
        )  # fmt: skip
        and not (len(counts) == len(strings) and len(strings) > 3)  # all different: identifiers
    )
    if closed:
        out["kind"] = "code"
        total = len(strings)
        out["values"] = {
            v: round(n / total, 2) for v, n in sorted(counts.items(), key=lambda x: -x[1])
        }
        out["case"] = _case(list(counts))
        return out
    if pattern is not None:
        out["kind"] = "identifier"
        out["pattern"] = pattern
        return out
    out["kind"] = "text"
    out["case"] = _case(strings)
    out["words"] = [min(len(v.split()) for v in strings), max(len(v.split()) for v in strings)]
    return out


MONEY = re.compile(r"^[$€£]?\s?-?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?\s?%?$")


def _money(text: str) -> float | None:
    """A currency amount or percentage written with a symbol, separator or point; not a bare
    digit run (that may be an identifier)."""
    if not MONEY.match(text) or not re.search(r"[$€£,.%]", text):
        return None
    try:
        return float(re.sub(r"[^\d.-]", "", text))
    except ValueError:
        return None


def keep_anchor_id(column: str, entity: str, value: Any) -> bool:
    """Whether an identifier may stay verbatim in an anchor: a case-number shape, in a column that
    is not an account, loan or person key, of an entity that is not a person's record."""
    if not isinstance(value, str) or not ANCHOR_ID.match(value):
        return False
    if SENSITIVE_ID.search(column) or PERSONAL.search(column):
        return False
    return not any(w in key(entity) for w in PEOPLE)


# --- reading raw evidence ----------------------------------------------------------------------------


class _Html(HTMLParser):
    """Tables (headers and rows) and label/value pairs of a page. Text only; no attributes kept
    except element ids, which name a value (`<span id="reviewNo">`)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[tuple[list[str], list[list[str]]]] = []
        self.pairs: list[tuple[str, str]] = []
        self._table: list | None = None
        self._cell: list[str] | None = None
        self._row: list[str] | None = None
        self._head = False
        self._dt: list[str] | None = None
        self._dd: list[str] | None = None
        self._label: str | None = None
        self._leaf: list[list] = []  # [id, tag, text parts, holds other elements]
        self.drop = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "template"):
            self.drop += 1
        elif tag == "table":
            self._table = [[], []]
        elif tag in ("th", "td") and self._table is not None:
            self._cell, self._head = [], tag == "th"
        elif tag == "tr":
            self._row = []
        elif tag == "dt":
            self._dt = []
        elif tag == "dd":
            self._dd = []
        if self._leaf and tag not in ("span", "b", "strong", "i", "em", "small", "a", "br"):
            self._leaf[-1][3] = True
        if a.get("id") and tag in ("span", "dd", "td", "div", "strong", "b", "p", "output"):
            self._leaf.append([a["id"], tag, [], False])

    def handle_endtag(self, tag):
        if tag in ("script", "style", "template"):
            self.drop = max(0, self.drop - 1)
        elif tag in ("th", "td") and self._cell is not None and self._row is not None:
            self._row.append(("H:" if self._head else "D:") + " ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row and all(c.startswith("H:") for c in self._row):
                self._table[0] = [c[2:] for c in self._row]
            elif self._row:
                self._table[1].append([c[2:] for c in self._row])
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append((self._table[0], self._table[1]))
            self._table = None
        elif tag == "dt" and self._dt is not None:
            self._label, self._dt = " ".join("".join(self._dt).split()), None
        elif tag == "dd" and self._dd is not None:
            if self._label:
                self.pairs.append((self._label, " ".join("".join(self._dd).split())))
            self._dd = None
        if self._leaf and tag == self._leaf[-1][1]:
            ident, _, text, container = self._leaf.pop()
            joined = " ".join("".join(text).split())
            if not container and 0 < len(joined) <= 60:
                self.pairs.append((ident, joined))

    def handle_data(self, data):
        if self.drop:
            return
        for buf in (self._cell, self._dt, self._dd):
            if buf is not None:
                buf.append(data)
        for leaf in self._leaf:
            leaf[2].append(data)


def _html(text: str) -> _Html:
    p = _Html()
    try:
        p.feed(text)
        p.close()
    except Exception:  # noqa: BLE001  broken HTML is evidence too: read what parsed
        pass
    return p


def _items(data: Any) -> list[dict]:
    from labctl import har

    return har._items(data)  # an envelope's key in any case (`Items`): har.list_key


def _entity(path: str) -> str | None:
    from labctl import har

    return har._entity(path)


@dataclass
class _Obs:
    """Raw observations, in memory only: entity -> column -> values."""

    cols: dict[str, dict[str, list]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(list))
    )
    rows: dict[str, int] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    pairs: list[tuple[str, list[tuple[str, str]]]] = field(default_factory=list)
    detail: list[dict] = field(default_factory=list)  # first-look states per (entity, id)
    sources: list[str] = field(default_factory=list)
    declared: dict[str, dict[str, list[str]]] = field(default_factory=lambda: defaultdict(dict))

    def add_row(self, entity: str, row: dict[str, Any], list_len: int | None = None) -> None:
        digest = (
            entity + hashlib.sha1(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()
        )
        if digest in self.seen:
            return
        self.seen.add(digest)
        for name, value in row.items():
            if isinstance(value, (dict, list)):
                continue
            self.cols[entity][snake(name)].append(value)

    def add_rows(self, entity: str, rows: list[dict[str, Any]], is_list: bool) -> None:
        for row in rows[:MAX_ITEMS]:
            self.add_row(entity, row)
        if is_list:
            self.rows[entity] = max(self.rows.get(entity, 0), len(rows))


def _path_ids(path: str) -> list[tuple[str, str]]:
    """`/api/reviews/R-1011/items/CRD-01` -> [('reviews', 'R-1011'), ('items', 'CRD-01')]."""
    from labctl import sanitize

    out, prev = [], None
    for seg in [s for s in path.split("/") if s]:
        if sanitize.is_id_segment(seg):
            if prev:
                out.append((prev, seg))
        elif seg.lower() != "api" and not sanitize.VERSION_SEGMENT.match(seg):
            prev = re.sub(r"[^a-z0-9_]+", "_", seg.lower()).strip("_")
    return out


def _read_recording(obs: _Obs, rec_path: Path) -> None:
    from labctl import har, sanitize

    rec = har.load(rec_path)
    obs.sources.append(f"har:{rec.id}")
    entries = [e for e in har.exchanges(rec.har) if e.kind in ("api", "document", "form")]
    changed: set[tuple[str, str]] = set()  # (entity, id) a request has already changed
    first_look: set[tuple[str, str]] = set()
    for e in sorted(entries, key=lambda x: (x.started is None, x.started)):
        ids = _path_ids(e.path)
        if e.method != "GET":
            changed.update(ids)
        if not (e.method == "GET" and 200 <= e.status < 300):
            continue
        entity = _entity(sanitize.route_pattern(e.path))
        pairs: list[tuple[str, str]] = []
        if e.resp_json is not None and entity:
            rows = _items(e.resp_json)
            obs.add_rows(entity, rows, is_list=har.is_list_answer(e.resp_json))
            for row in rows[:1]:
                pairs = [(k, v) for k, v in row.items() if not isinstance(v, (dict, list))]
                for k, v in row.items():  # a nested object is a related entity
                    if isinstance(v, dict) and v:
                        obs.add_rows(_entity("/" + snake(k)) or snake(k), [v], False)
                    elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                        obs.add_rows(_entity("/" + snake(k)) or snake(k), v, True)
        elif e.resp_text and e.resp_mime == "text/html" and entity:
            page = _html(e.resp_text)
            for n, (headers, rows) in enumerate(page.tables):
                cols = [snake(h) or f"column_{i + 1}" for i, h in enumerate(headers)]
                if not cols or not rows:
                    continue
                table_rows = [dict(zip(cols, r, strict=False)) for r in rows if len(r) == len(cols)]
                # a table on a record's page is not that record's entity: resolved by its columns
                where = f"{ids[-1][0]}~{n}" if ids else entity
                obs.add_rows(where, [{k: (v if v != "" else None) for k, v in r.items()}
                                     for r in table_rows], True)  # fmt: skip
            if ids:
                pairs = [(snake(k), v) for k, v in page.pairs if v]
                digest = "pairs" + hashlib.sha1(json.dumps(pairs).encode()).hexdigest()
                if pairs and digest not in obs.seen:
                    obs.seen.add(digest)
                    obs.pairs.append((ids[-1][0], pairs))
        for ent, ident in ids[-1:]:  # the record this page or call is about
            if (ent, ident) in first_look:
                continue
            first_look.add((ent, ident))
            obs.detail.append({"entity": ent, "id": ident, "pairs": pairs,
                               "after_change": (ent, ident) in changed,
                               "route": sanitize.route_pattern(e.path)})  # fmt: skip


def _read_model(obs: _Obs, root: Path, app: str) -> None:
    """Code lists the app model already holds (committed; value lists, no free values)."""
    path = root / "apps" / app / "model.yaml"
    if not path.is_file():
        return
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return
    for ref, values in (data.get("codes") or {}).items():
        entity, _, col = str(ref).partition(".")
        if col and isinstance(values, list):
            obs.declared[entity][snake(col)] = [str(v) for v in values]
    for screen in data.get("screens") or []:
        ent = _entity(str(screen.get("path") or "")) if screen.get("path") else None
        for el in screen.get("elements") or []:
            if el.get("tag") == "select" and el.get("options"):
                col = snake(
                    re.sub(
                        r"(filter|select|dropdown|list)$",
                        "",
                        snake(el.get("name") or el.get("id") or ""),
                        flags=re.I,
                    )
                )
                if ent and col and CODE_NAME.search(col):
                    codes = el.get("option_values") or el["options"]  # the codes posted, if seen
                    obs.declared[ent].setdefault(col, [str(o) for o in codes if o])


# --- resolving names against a schema -------------------------------------------------------------------


def _resolve_table(entity: str, columns: set[str], tables: list) -> Any | None:
    """The schema table an observed entity is: same name, else the unique best column overlap."""
    if not tables:
        return None
    target = singular(entity)
    for t in tables if entity else []:
        if singular(t.name) == target:
            return t
    near = [
        t
        for t in tables
        if entity
        and len(target) >= 4
        and (
            singular(t.name).endswith("_" + target)
            or singular(t.name).startswith(target + "_")
            or key(singular(t.name)).endswith(target)
        )
    ]  # `reviews` -> `qc_reviews`; fmt: skip
    if len(near) == 1:
        return near[0]
    keys = {key(c) for c in columns}
    scored = sorted(((len(keys & {key(c.name) for c in t.columns}), t) for t in tables),
                    key=lambda x: -x[0])  # fmt: skip
    if scored and scored[0][0] >= 2 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return scored[0][1]
    return None


def _resolve_column(entity: str, col: str, table: Any | None) -> str:
    if table is None:
        return col
    names = {key(c.name): c.name for c in table.columns}
    if key(col) in names:
        return names[key(col)]
    prefix = singular(entity)
    stripped = key(col)[len(prefix) :] if key(col).startswith(prefix) else None
    if stripped and stripped in names:
        return names[stripped]
    return col


# --- building a profile ----------------------------------------------------------------------------------


def build(root: Path, app: str, tables: list | None = None) -> dict[str, Any]:
    """The observed profile of an app, from the evidence under `evidence/<app>/` and its model."""
    from labctl import factory

    obs = _Obs()
    for rec in factory.discover(root, app).recordings:
        _read_recording(obs, rec)
    _read_model(obs, root, app)
    return from_observations(app, obs, tables or [])


def _assign_pair(name: str, entity: str, schema: list) -> tuple[Any, str] | None:
    """The schema table and column a label/value pair of a record's page belongs to."""
    k = key(name)
    own = next((t for t in schema if singular(t.name) == singular(entity)), None)
    for t in [own] if own else []:
        for c in t.columns:
            if key(c.name) == k:
                return t, c.name
    for t in schema:  # `reviewStatus` -> reviews.status
        s = singular(t.name)
        if k.startswith(s):
            for c in t.columns:
                if key(c.name) == k[len(s) :]:
                    return t, c.name
    hits = [(t, c.name) for t in schema for c in t.columns if key(c.name) == k]
    return hits[0] if len(hits) == 1 else None


def _distribute(obs: _Obs, schema: list) -> None:
    """Move tables and pairs seen on a record's page to the schema table they belong to."""
    for name in [n for n in obs.cols if "~" in n]:
        cols = obs.cols.pop(name)
        table = _resolve_table("", set(cols), schema) if schema else None
        target = table.name if table is not None else name.replace("~", "_table")
        for col, values in cols.items():
            obs.cols[target][col].extend(values)
        if name in obs.rows:
            obs.rows[target] = max(obs.rows.get(target, 0), obs.rows.pop(name))
    for entity, pairs in obs.pairs:
        row: dict[str, dict[str, str]] = defaultdict(dict)
        for col, value in pairs:
            hit = _assign_pair(col, entity, schema) if schema else (None, col)
            if hit is not None:
                table, column = hit
                row[table.name if table is not None else entity][column] = value
        for table_name, values in row.items():
            for column, value in values.items():
                obs.cols[table_name][column].append(value)


def from_observations(app: str, obs: _Obs, schema: list) -> dict[str, Any]:
    _distribute(obs, schema)
    entities = set(obs.cols) | set(obs.declared)
    # values of personal columns anywhere: a vocabulary entry equal to one of them is dropped
    personal_values = frozenset(
        str(v).lower()
        for ent in obs.cols.values()
        for c, vs in ent.items()
        if PERSONAL.search(c)
        for v in vs
        if v is not None  # fmt: skip
    )
    profile_tables: dict[str, Any] = {}
    entity_table: dict[str, str] = {}
    raw: dict[str, dict[str, list]] = {}
    for entity in sorted(entities):
        cols = obs.cols.get(entity, {})
        table = _resolve_table(entity, set(cols), schema)
        name = table.name if table is not None else entity
        entity_table[entity] = name
        entry = profile_tables.setdefault(name, {"columns": {}})
        for col, values in cols.items():
            target = _resolve_column(entity, col, table)
            info = classify_column(col, values, personal_values)
            if (
                target in entry["columns"]
                and entry["columns"][target].get("seen", 0) >= info["seen"]
            ):
                continue
            entry["columns"][target] = info
            raw.setdefault(name, {})[target] = values
        for col, vocab in obs.declared.get(entity, {}).items():  # app model vocabulary
            target = _resolve_column(entity, col, table)
            have = entry["columns"].get(target)
            if have and have.get("kind") == "code":
                continue
            ok = [v for v in vocab if is_vocab_value(v) and v.lower() not in personal_values]
            if ok and not PERSONAL.search(col) and len(ok) <= MAX_VOCAB and (
                have is None or have.get("kind") in ("unknown", "text", "code")):  # fmt: skip
                entry["columns"][target] = {"kind": "code", "seen": 0, "distinct": len(ok),
                                            "values": {v: round(1 / len(ok), 2) for v in ok},
                                            "from": "app model"}  # fmt: skip
        if entity in obs.rows:
            entry["rows_seen"] = obs.rows[entity]
    # relations: an identifier column seen in two entities with values in common
    relations = []
    for a, cols_a in sorted(raw.items()):
        for b, cols_b in sorted(raw.items()):
            if a >= b:
                continue
            for ca, va in cols_a.items():
                info = profile_tables[a]["columns"][ca]
                if info.get("kind") != "identifier" or ca not in cols_b:
                    continue
                if set(map(str, va)) & set(map(str, cols_b[ca])):
                    parent, child = (a, b) if profile_tables[a].get("rows_seen", 0) >= \
                        profile_tables[b].get("rows_seen", 0) else (b, a)  # fmt: skip
                    relations.append({"from": f"{child}.{ca}", "to": f"{parent}.{ca}"})
    anchors = _anchors(obs, profile_tables, entity_table, schema)
    verbatim = {f"{t}.{c}": list(i["values"]) for t, e in profile_tables.items()
                for c, i in e["columns"].items() if i.get("values")}  # fmt: skip
    for a in anchors:
        if a["key"].get("id"):
            verbatim.setdefault(f"{a['table']}.{a['key']['column']} (anchor)", []).append(
                a["key"]["id"]
            )
    out: dict[str, Any] = {
        "version": VERSION,
        "app": app,
        "from": obs.sources,
        "review": "Read `verbatim` before use: it is every value copied from the recordings. "
        "Everything else in this file is shape only. Delete a line to drop it.",
        "verbatim": dict(sorted(verbatim.items())),
        "tables": dict(sorted(profile_tables.items())),
    }
    if relations:
        out["relations"] = relations
    if anchors:
        out["anchors"] = anchors
    return out


def _anchors(obs: _Obs, profile_tables: dict, entity_table: dict, schema: list) -> list[dict]:
    """Records a flow navigated to, with the state seen before any change it made."""
    out, done = [], set()
    for d in obs.detail:
        entity, ident = d["entity"], d["id"]
        name = entity_table.get(entity)
        if name is None:
            table = _resolve_table(entity, {snake(p[0]) for p in d["pairs"]}, schema)
            if table is None and schema:  # the table most of the page's labels belong to
                hits = Counter(
                    h[0].name for p in d["pairs"] if (h := _assign_pair(p[0], entity, schema))
                )
                table = next(
                    (t for t in schema if hits and t.name == hits.most_common(1)[0][0]), None
                )
            name = table.name if table is not None else entity
        if (name, ident) in done:
            continue
        done.add((name, ident))
        table = next((t for t in schema if t.name == name), None)
        pairs = [(snake(k), v) for k, v in d["pairs"]]
        keycol = next((c for c, v in pairs if v == ident or str(v) == ident), None)
        pk = next((c.name for c in table.columns if c.primary_key), None) if table else None
        column = _resolve_column(entity, keycol, table) if keycol else (pk or "id")
        info = {"table": name, "key": {"column": column}, "route": d["route"]}
        verbatim = keep_anchor_id(column, name, ident)
        shape = pattern_of([ident], literal_prefix=verbatim or not SENSITIVE_ID.search(column)) \
            or "".join("#" if c.isdigit() else "A" if c.isupper() else "a" if c.isalpha() else c
                       for c in ident)  # fmt: skip
        info["key"]["shape"] = shape
        info["key"]["verbatim"] = verbatim
        if verbatim:
            info["key"]["id"] = ident
        state: dict[str, str] = {}
        if not d["after_change"]:
            cols = profile_tables.get(name, {}).get("columns", {})
            for c, v in pairs:
                target = _resolve_column(entity, c, table)
                spec = cols.get(target, {})
                if spec.get("kind") == "code" and v in spec.get("values", {}):
                    state[target] = v
        info["state"] = state
        info["state_seen_before_change"] = not d["after_change"]
        out.append(info)
    return out


# --- file and summary ----------------------------------------------------------------------------------------


def write(profile: dict[str, Any], path: Path) -> None:
    check(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(profile, sort_keys=False, allow_unicode=True, width=100)
    path.write_text("# Observed profile: read before use. Shapes only except `verbatim`.\n" + text,
                    encoding="utf-8")  # fmt: skip


def check(profile: dict[str, Any]) -> None:
    """Refuse a profile that holds a value it must not. Runs on write and on load."""
    bad = []
    for t, entry in (profile.get("tables") or {}).items():
        for c, info in (entry.get("columns") or {}).items():
            if info.get("kind") != "code" and (info.get("values") or "example" in info):
                bad.append(f"{t}.{c}: values on a column that is not a code list")
            for v in info.get("values") or {}:
                if not is_vocab_value(v) or PERSONAL.search(c):
                    bad.append(f"{t}.{c}: {v!r} may not be kept verbatim")
    for a in profile.get("anchors") or []:
        ident = (a.get("key") or {}).get("id")
        if ident is not None and not keep_anchor_id(
            a["key"].get("column", ""), a.get("table", ""), ident
        ):
            bad.append(f"anchor {a.get('table')}: identifier {ident!r} may not be kept verbatim")
    if bad:
        raise ProfileError("profile refused: " + "; ".join(bad[:8]))


def summary(profile: dict[str, Any]) -> dict[str, int]:
    cols = [
        i for e in (profile.get("tables") or {}).values() for i in (e.get("columns") or {}).values()
    ]
    kept = sum(len(i.get("values") or {}) for i in cols)
    anchors = profile.get("anchors") or []
    return {
        "tables": len(profile.get("tables") or {}),
        "columns": len(cols),
        "code_columns": sum(1 for i in cols if i.get("values")),
        "values_kept_verbatim": kept + sum(1 for a in anchors if a["key"].get("id")),
        "columns_shape_only": sum(1 for i in cols if not i.get("values")),
        "anchors": len(anchors),
        "relations": len(profile.get("relations") or []),
    }


# --- using a profile -------------------------------------------------------------------------------------------


class Profile:
    """A loaded profile, looked up by schema table and column names (case, plural and `_` free)."""

    def __init__(self, data: dict[str, Any]):
        if not isinstance(data, dict) or "tables" not in data and "anchors" not in data:
            raise ProfileError("not an observed profile: no `tables`")
        check(data)
        self.data = data
        self._tables = {singular(t): e for t, e in (data.get("tables") or {}).items()}
        self._anchors: dict[str, list[dict]] = defaultdict(list)
        for a in data.get("anchors") or []:
            self._anchors[singular(a["table"])].append(a)

    @classmethod
    def load(cls, path: Path) -> Profile:
        try:
            return cls(yaml.safe_load(Path(path).read_text(encoding="utf-8-sig")))
        except (OSError, yaml.YAMLError) as exc:
            raise ProfileError(f"cannot read profile {path}: {exc}") from exc

    def column(self, table: str, column: str) -> dict[str, Any] | None:
        entry = self._tables.get(singular(table))
        if not entry:
            return None
        cols = {key(c): i for c, i in (entry.get("columns") or {}).items()}
        return cols.get(key(column))

    def rows_seen(self, table: str) -> int | None:
        entry = self._tables.get(singular(table))
        return entry.get("rows_seen") if entry else None

    def anchors(self, table: str) -> list[dict]:
        return self._anchors.get(singular(table), [])

    def suggested_counts(self, tables: list) -> dict[str, int]:
        """Row counts for tables with something observed: rows seen, at least the anchors."""
        out = {}
        for t in tables:
            n = max(self.rows_seen(t.name) or 0, len(self.anchors(t.name)))
            if n:
                out[t.name] = n
        return out
