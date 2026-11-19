"""Seed rows and replay values, made together from what an application's recordings showed.

A rebuilt application is judged by replaying its recording against it: the replay types synthetic
values (never the ones a person typed, which the recording does not keep) and compares what the
pages show. That only works when the rebuild holds data the typed values fit: a search the
recording shows finding 12 rows over 3 pages must find 12 rows over 3 pages again. So the seed
and the values are generated here by one deterministic planner, from the same evidence:

- **Seed** (`apps/<app>/db/seed.sql`, the same rows as `db/seed.json`): for every table of the
  schema that a recorded list answered from, rows numbered as the application numbers them (the
  lowest to the highest identifier it showed, the next new one being the highest + 1), the columns
  in the shapes and code lists the answers showed (`labctl.data.observed`), and the rows a
  recorded search found given the word the replay will search for. A table whose first record was
  written during the recording gets no rows. Every value is synthetic; only identifiers and code
  values, which are the application's own output, are taken as they were shown.
- **Replay values** (`apps/<app>/steps/<recording>.values.yaml`, builder-safe, and the values in
  the evidence's `bindings.yaml`, marked `generated:`): per typing or choosing step the synthetic
  value the acceptance replay types and the outcome the recording showed. A search term matches
  exactly the seeded rows the recording's answer counted; a "no results" search matches none; a
  refused input breaks the rule a recorded message states and the accepted one keeps it; a value
  that travels to another application (an order number shown here, typed there) is the value
  this application will show on its seed.
- **What the pages show**: per shown data field its display format (two decimals, a date
  pattern, a closed list of words, what was typed at which step), and per value the application
  computes (a total) whether the evidence lets one derive it. A computed value with no rule in the
  evidence is marked "computed, rule unknown" and left out of required acceptance with that
  reason, instead of failing a builder for a rule nobody can know.

Nothing here reads a typed value: the recorder already replaced each one with its shape
everywhere, and generated words are checked against every word the evidence holds.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import yaml

from labctl import sanitize, stepfacts

SEED_SQL = "seed.sql"  # under apps/<app>/db/
SEED_JSON = "seed.json"
VALUES = ".values.yaml"  # apps/<app>/steps/<recording>.values.yaml
PAGE_PARAMS = {"page", "p", "offset", "start", "size", "limit", "per_page", "pagesize", "skip"}
COMPUTED_WORDS = {"total", "sum", "subtotal", "tax", "vat", "net", "gross", "balance", "due"}
MASK = re.compile(r"^[A9Xx][A9Xx .,:;@/+()#_-]*$")
TWO_DEC = re.compile(r"^-?\d+\.\d{2}$")
DECIMAL = re.compile(r"^-?\d+\.\d+$")
WHOLE = re.compile(r"^-?\d+$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
EXAMPLE = re.compile(r"\b([A-Z]{1,8})([-_/.]?)(\d{2,12})\b")
ERRORISH = re.compile(r"error|alert|invalid|warn|danger|notice|problem|fail", re.I)
PERSONAL = re.compile(r"name|customer|payer|person|user|email|mail|phone|address|note|memo|"
                      r"comment|contact|client|owner", re.I)  # fmt: skip
# Words the seed is made of (surnames for searchable columns, first names) and, apart from them so
# that a typed value never adds a hit to a seeded search, the words typed values are made of.
FIRST = ["Avery", "Blake", "Casey", "Dana", "Emery", "Frankie", "Gray", "Harper", "Indy", "Jordan",
         "Kai", "Morgan", "Noel", "Oakley", "Parker", "Quinn", "Reese", "Rowan", "Sky", "Taylor"]  # fmt: skip
LAST = ["Kestrel", "Larkspur", "Ashdown", "Birchwood", "Calder", "Dunmore", "Elmsworth",
        "Fairbank", "Glenholm", "Hollins", "Juniper", "Merriweather", "Northcott", "Oakridge",
        "Pembrook", "Redfern", "Stonebridge", "Thornbury", "Underhill", "Westbrook", "Yarrow"]  # fmt: skip
TYPED = ["Saffron", "Cobalt", "Marlow", "Tamsin", "Orrin", "Bexley", "Calloway", "Delphine",
         "Everly", "Fennick", "Garnet", "Halden", "Ingram", "Jessamy", "Kellan", "Linden",
         "Maddox", "Nerys", "Ottilie", "Perrin", "Quill", "Rosalind", "Sorrel", "Tobin"]  # fmt: skip
REFUSED_WORDS = ["abc", "xyz", "nope", "bad"]


# --- reading one recording part ------------------------------------------------------------------


@dataclass
class Ex:
    """One exchange with what the planner needs: values are masked already where they were typed."""

    started: object
    method: str
    path: str
    route: str
    query: dict[str, str]
    form: dict[str, str]
    status: int
    data: object  # the JSON answer, or None
    # For the API catalogue (labctl.apicatalog): the JSON body sent (typed values are shapes
    # already), content types, the page that sent it (Referer path), where a redirect leads,
    # and how the request was authorised BY NAME only (cookie names, the Authorization scheme).
    body: object = None
    req_type: str = ""
    resp_type: str = ""
    referer: str = ""
    location: str = ""
    cookies_sent: tuple = ()
    cookies_set: tuple = ()
    auth_scheme: str = ""


@dataclass
class Part:
    app: str
    path: Path
    raw: list[dict]
    facts: stepfacts.Facts
    exchanges: list[Ex]
    owner: dict[int, list[Ex]]  # step seq -> the exchanges it caused (last step at or before)
    doms: dict[int, str]
    befores: dict[int, str]
    words: set[str]  # every word in the evidence, lower-case: generated words must avoid them

    @property
    def name(self) -> str:
        return self.path.name

    def step(self, seq: int) -> dict:
        return next(s for s in self.raw if s.get("seq") == seq)

    def html_at(self, seq: int) -> str | None:
        """The page as the step met it, else the latest page at or before it."""
        if seq in self.befores:
            return self.befores[seq]
        best = None
        for n in sorted(self.doms):
            if n <= seq:
                best = self.doms[n]
        return best


def _raw_steps(part: Path) -> list[dict]:
    out = []
    for line in (part / "steps.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("seq"), int):
            out.append(data)
    return out


def _exchanges(har_doc: dict, hosts: set[str]) -> list[Ex]:
    from labctl import har

    out = []
    for entry in (har_doc.get("log") or {}).get("entries") or []:
        req, resp = entry.get("request") or {}, entry.get("response") or {}
        url = urlsplit(str(req.get("url") or ""))
        if url.scheme not in ("http", "https") or (hosts and url.netloc.lower() not in hosts):
            continue
        content = resp.get("content") or {}
        text = content.get("text") if isinstance(content.get("text"), str) else None
        data = None
        if text and "json" in str(content.get("mimeType") or "").lower():
            try:
                data = json.loads(text)
            except ValueError:
                data = None
        post = req.get("postData") or {}
        form = dict(parse_qsl(str(post.get("text") or ""), keep_blank_values=True))
        extras = _extras(entry, post, content)
        if isinstance(extras["body"], dict):
            # a JSON body's fields are what a form's fields are: what the page sent, by name
            form = {str(k): "" if v is None else str(v) for k, v in extras["body"].items()
                    if not isinstance(v, (dict, list, bool))}  # fmt: skip
        out.append(Ex(har._time(entry.get("startedDateTime")), str(req.get("method") or "GET").upper(),
                      url.path or "/", sanitize.route_pattern(url.path or "/"),
                      dict(parse_qsl(url.query, keep_blank_values=True)), form,
                      int(resp.get("status") or 0), data, **extras))  # fmt: skip
    return [e for e in out if e.started is not None]


def _extras(entry: dict, post: dict, content: dict) -> dict:
    """The fields of `Ex` the API catalogue reads. Header values that could be a credential are
    never kept: a cookie gives its name, an Authorization header its scheme."""
    from labctl import har

    req, resp = entry.get("request") or {}, entry.get("response") or {}

    def heads(side: dict, name: str) -> list[str]:
        return [str(h.get("value") or "") for h in side.get("headers") or []
                if str(h.get("name") or "").lower() == name]  # fmt: skip

    req_type = str(post.get("mimeType") or "").split(";")[0].strip().lower()
    body = None
    text = post.get("text")
    if isinstance(text, str) and ("json" in req_type or text.lstrip().startswith(("{", "["))):
        try:
            body = json.loads(text)
        except ValueError:
            body = None
    sent = {p.split("=", 1)[0].strip() for v in heads(req, "cookie") for p in v.split(";")
            if "=" in p}  # fmt: skip
    sent |= {str(c.get("name") or "") for c in req.get("cookies") or []}
    made = {v.split("=", 1)[0].strip() for h in heads(resp, "set-cookie") for v in h.splitlines()
            if "=" in v}  # fmt: skip
    made |= {str(c.get("name") or "") for c in resp.get("cookies") or []}
    scheme = next((v.split(" ", 1)[0] for v in heads(req, "authorization") if " " in v), "")
    referer = next(iter(heads(req, "referer")), "")
    location = next(iter(heads(resp, "location")), "") or str(resp.get("redirectURL") or "")
    return {
        "body": body,
        "req_type": req_type or ("application/json" if body is not None else ""),
        "resp_type": str(content.get("mimeType") or "").split(";")[0].strip().lower(),
        # a cross-origin call's Referer is the page's origin only: it names no page
        "referer": har.referer_path(referer, urlsplit(str(req.get("url") or "")).netloc),
        "location": urlsplit(location).path if location else "",
        "cookies_sent": tuple(sorted(n for n in sent if sanitize.COOKIE_NAME.match(n))),
        "cookies_set": tuple(sorted(n for n in made if sanitize.COOKIE_NAME.match(n))),
        "auth_scheme": scheme if re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{1,20}", scheme) else "",
    }


def load_part(app: str, part: Path) -> Part:
    from labctl import har

    raw = _raw_steps(part)
    hosts = {urlsplit(str(s.get("url") or "")).netloc.lower() for s in raw
             if str(s.get("url") or "").startswith("http")}  # fmt: skip
    doc = har.load(part).har
    if hosts:  # the pages' own API on another origin of their site
        hosts |= har.api_hosts(hosts, har.exchanges(doc))
    exs = sorted(_exchanges(doc, hosts), key=lambda e: e.started)
    timed = [(s["seq"], har._time(s.get("ts"))) for s in raw]
    owner: dict[int, list[Ex]] = {}
    # The recorder logs a typing step, the key press and the submit it causes a few ms after the
    # request they send has started: a step logged within LAG after a request still owns it.
    lag = timedelta(milliseconds=150)
    for e in exs:
        at = None
        for seq, ts in timed:
            if ts is not None and ts <= e.started + lag:
                at = seq
        if at is not None:
            owner.setdefault(at, []).append(e)

    def read(rel):
        p = (part / rel) if rel else None
        return p.read_text(encoding="utf-8", errors="replace") if p and p.is_file() else None

    doms = {s["seq"]: t for s in raw if (t := read(s.get("dom"))) is not None}
    befores = {s["seq"]: t for s in raw if (t := read(s.get("dom_before"))) is not None}
    words = set(re.findall(r"[a-z]{3,}", json.dumps(doc).lower()))
    for t in [*doms.values(), *befores.values()]:
        words |= set(re.findall(r"[a-z]{3,}", t.lower()))
    return Part(app, Path(part), raw, stepfacts.facts(part), exs, owner, doms, befores, words)


# --- small helpers ---------------------------------------------------------------------------------


def _node(html: str | None, selector: str | None):
    """The element a recorded selector names in a page (by its id, or tag and name)."""
    from labctl.behaviour import dom as domlib

    if not html or not selector:
        return None
    key = domlib.key_of(selector.split(">>>")[-1].strip())
    if key is None:
        return None
    for node in domlib._walk(domlib.parse_html(html)):
        if key.startswith("#") and node.id == key[1:]:
            return node
        m = re.match(r"(\w+)\[name='([^']+)'\]", key)
        if m and node.tag == m.group(1) and node.attrs.get("name") == m.group(2):
            return node
    return None


def _options(node) -> list[tuple[str, str]]:
    from labctl.behaviour import dom as domlib

    if node is None:
        return []
    return [(o.attrs.get("value", domlib._text(o)), domlib._text(o))
            for o in domlib._walk(node) if o.tag == "option"]  # fmt: skip


def _items(data) -> list[dict] | None:
    from labctl import har

    if isinstance(data, list) or (isinstance(data, dict) and any(
            isinstance(data.get(k), list) for k in har.ITEM_KEYS)):  # fmt: skip
        return har._items(data)
    return None


def _count(data, items: list[dict]) -> tuple[int, int | None]:
    total = data.get("total") if isinstance(data, dict) else None
    pages = data.get("pages") if isinstance(data, dict) else None
    total = total if isinstance(total, int) and not isinstance(total, bool) else len(items)
    pages = pages if isinstance(pages, int) and not isinstance(pages, bool) else None
    return total, pages


def _masked(text: str, shapes: set[str]) -> bool:
    return text in shapes or bool(MASK.match(text)) and any(c in text for c in "A9X")


def _id_of(value, pattern: tuple[str, str, int]) -> int | None:
    if not isinstance(value, str):
        return None
    m = stepfacts.ID_VALUE.match(value.strip())
    if m and (m.group(1), m.group(2), len(m.group(3))) == pattern:
        return int(m.group(3))
    return None


def _num(step: dict) -> str:
    return f"{step['seq']}" + (f" (whole recording {step['global_seq']})"
                               if step.get("global_seq") is not None else "")  # fmt: skip


def _q(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


# --- the app's data design: lists, searches, identifiers ------------------------------------------


@dataclass
class Search:
    part: str
    step: int  # the typing step whose value the search used (this part's number)
    global_step: int | None
    field: str
    param: str
    route: str
    entity: str | None
    others: dict[str, str]  # the other parameters (a mode: by=customer)
    rule: str  # contains | equals | none
    key: str | None  # the answer's field the term matched
    total: int
    pages: int | None
    page_size: int
    hits: set[int] = field(default_factory=set)  # identifiers the answers showed
    term: str | None = None


@dataclass
class ListDesign:
    route: str
    entity: str | None
    id_key: str | None = None
    pattern: tuple[str, str, int] | None = None
    lowest: int | None = None
    highest: int | None = None
    rows: int = 0
    page_size: int = 0
    first_new: int | None = None  # the first identifier made in the recording, when shown
    values: dict[str, list] = field(default_factory=dict)  # key -> values shown (masked kept out)
    searches: list[Search] = field(default_factory=list)
    note: str = ""
    before_writes: int = 0  # most rows an answer listed before the recording changed anything
    total_before: int = 0  # the highest `total` an answer counted before any change
    # Per identifier number, what the answers first showed of that record (a list row or the
    # record's own GET): read only for code values, booleans and empty fields, never copied as data.
    seen: dict[int, dict] = field(default_factory=dict)


@dataclass
class Core:
    """One application across all of its recordings."""

    app: str
    parts: list[Part]
    lists: dict[str, ListDesign]
    created: dict[tuple, list[tuple[str, int, str]]]  # pattern -> (part, step, element) in order
    words: set[str]

    def design_for(self, pattern: tuple) -> ListDesign | None:
        return next((d for d in self.lists.values() if d.pattern == pattern), None)

    def next_id(self, pattern: tuple, k: int = 0) -> int | None:
        d = self.design_for(pattern)
        if d is not None and d.rows and d.lowest is not None:
            return d.lowest + d.rows + k
        if d is not None and d.first_new is not None:
            return d.first_new + k
        return None


def _shapes(parts: list[Part]) -> set[str]:
    return {str(s["typed_shape"]) for p in parts for s in p.raw if s.get("typed_shape")}


def _sent_between(p: Part, t: dict, e: Ex) -> bool:
    from labctl import har

    at = har._time(t.get("ts"))
    if at is None:
        return False
    return any(at <= x.started < e.started and (x.method != "GET" or (
        x.data is None and not re.search(r"\.(js|css|png|svg|ico|woff2?)$", x.path)))
        for x in p.exchanges)  # fmt: skip


def _searches_of(p: Part) -> list[Search]:
    """Every list request whose parameter holds what a typing step typed (as its shape)."""
    from labctl import har

    typed = [s for s in p.raw if s.get("action") == "type" and s.get("typed_shape")]
    groups: dict[int, list[Ex]] = {}
    params: dict[int, str] = {}

    def same_page(a: int, b: int) -> bool:  # no page load between steps a and b
        return not any(a < s["seq"] <= b and s.get("action") == "goto" for s in p.raw)

    for seq in sorted(p.owner):
        for e in p.owner[seq]:
            if e.method != "GET" or _items(e.data) is None:
                continue
            # the typing step whose page sent it, with no other typing, no page load and no form
            # sent between (an answer a later page asked for is not this search's)
            before = [t for t in typed if t["seq"] <= seq and same_page(t["seq"], seq)
                      and not any(t["seq"] < x["seq"] <= seq for x in typed)
                      and not _sent_between(p, t, e)]  # fmt: skip
            hit = None
            for t in reversed(before):
                node = _node(p.html_at(t["seq"]), t.get("selector"))
                names = [n for n in ((node.attrs.get("name") if node else None),
                                     (node.id if node else None)) if n]  # fmt: skip
                keys = [k for k in e.query if e.query[k] == t["typed_shape"]]
                key = next((k for k in keys if k in names), keys[0] if keys else None)
                if key:
                    hit = (t, key)
                    break
            if hit is None:
                continue
            groups.setdefault(hit[0]["seq"], []).append(e)
            params[hit[0]["seq"]] = hit[1]
    out = []
    for seq, exs in sorted(groups.items()):
        t = p.step(seq)
        first = exs[0]
        items = _items(first.data) or []
        total, pages = _count(first.data, items)
        term = t["typed_shape"]
        size = max((len(_items(e.data) or []) for e in exs
                    if isinstance(e.data, dict) and (e.data.get("pages") or 0) > 1), default=0)  # fmt: skip
        rule, key = "none", None
        if items:
            for k in items[0]:
                vals = [str(i.get(k)) for i in items if isinstance(i.get(k), str)]
                if len(vals) != len(items):
                    continue
                if all(v == term for v in vals):
                    rule, key = "equals", k
                    break
                if all(re.search(rf"(^|\s){re.escape(term)}(\s|$)", v) for v in vals):
                    rule, key = "contains", k
        others = {k: v for k, v in first.query.items()
                  if k != params[seq] and k.lower() not in PAGE_PARAMS}  # fmt: skip
        out.append(Search(p.name, seq, t.get("global_seq"), str(t.get("selector") or ""),
                          params[seq], first.route, har._entity(first.route), others, rule, key,
                          total, pages, size))  # fmt: skip
        out[-1].exchanges = exs  # type: ignore[attr-defined]
    # a search that found nothing names its field from an earlier one with the same mode
    for s in out:
        if s.rule == "none":
            same = [o for o in out if o.key and o.param == s.param and o.others == s.others]
            s.key = same[-1].key if same else None
    return out


def core(root: Path, app: str, parts: list[Path] | None = None) -> Core:
    """The design of an application's data, from all of its recordings."""
    from labctl import har

    paths = parts if parts is not None else stepfacts.parts_of(root, app)
    loaded = [load_part(app, p) for p in paths]
    shapes = _shapes(loaded)
    lists: dict[str, ListDesign] = {}
    for p in loaded:
        wrote = False
        for e in p.exchanges:
            if e.method != "GET" and not re.search(r"log_?in|sign_?in|auth|session", e.path, re.I):
                wrote = True
            items = _items(e.data) if e.method == "GET" and 200 <= e.status < 300 else None
            if items is None:
                continue
            d = lists.setdefault(e.route, ListDesign(e.route, har._entity(e.route)))
            if not wrote:
                d.before_writes = max(d.before_writes, len(items))
                d.total_before = max(d.total_before, _count(e.data, items)[0])
            for item in items:
                for k, v in item.items():
                    if isinstance(v, (dict, list)):
                        continue
                    if isinstance(v, str) and _masked(v, shapes):
                        continue
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and re.fullmatch(
                            r"-?9(\.9)?", str(v)):  # fmt: skip
                        continue  # a number the recorder masked (typed here or in another app)
                    d.values.setdefault(k, []).append(v)
            if isinstance(e.data, dict) and (e.data.get("pages") or 0) > 1:
                d.page_size = max(d.page_size, len(items))
        for s in _searches_of(p):
            if s.route in lists:
                lists[s.route].searches.append(s)
    gen = [(p.name, g) for p in loaded for g in p.facts.generated]
    created: dict[tuple, list] = {}
    for name, g in gen:
        created.setdefault((g["prefix"], g["sep"], g["width"]), []).append(
            (name, g["after"], g["element"])
        )
    for d in lists.values():
        best = None
        for k, vals in d.values.items():
            pats = {}
            for v in vals:
                m = stepfacts.ID_VALUE.match(v.strip()) if isinstance(v, str) else None
                if m:
                    pats.setdefault((m.group(1), m.group(2), len(m.group(3))), set()).add(
                        int(m.group(3))
                    )
            for pat, nums in pats.items():
                if best is None or len(nums) > len(best[2]):
                    best = (k, pat, nums)
        if best is None:  # no identifier: as many rows as were listed before anything changed
            d.rows = max([s.total for s in d.searches] + [d.before_writes])
            continue
        d.id_key, d.pattern, nums = best
        d.lowest, d.highest = min(nums), max(nums)
        firsts = [g for _, g in gen if (g["prefix"], g["sep"], g["width"]) == d.pattern]
        shown = next((g["value"] for g in firsts if g.get("value")), None)
        if shown is not None:
            d.first_new = int(stepfacts.ID_VALUE.match(shown).group(3))
        made = _api_made(loaded, d)
        if made:  # the answer of the creating call names the new record: nothing to guess
            d.first_new = min(made)
        _records_seen(loaded, d, shapes)
        if d.first_new is not None and d.first_new == d.lowest:
            d.rows = 0
            d.note = (f"the first record was written during the recording "
                      f"({stepfacts.id_text(_pat(d.pattern), d.first_new)}) and none was listed "
                      "before: start with no seeded rows of this kind, numbering from that value")  # fmt: skip
            continue
        d.rows = max(d.highest - d.lowest + 1, *[s.total for s in d.searches], 0)
        if made and d.first_new > d.lowest:  # rows before the first record the recording made
            d.rows = d.first_new - d.lowest
        elif d.first_new is None and d.total_before > d.rows and d.highest >= d.total_before:
            # the list counted more records than the pages looked at showed: they exist, below
            # the lowest identifier seen (a hypothesis: numbered without gaps)
            d.lowest = d.highest - d.total_before + 1
            d.rows = d.total_before
    words = set().union(*(p.words for p in loaded)) if loaded else set()
    c = Core(app, loaded, lists, created, words)
    _terms(c)
    return c


def _api_made(parts: list[Part], d: ListDesign) -> list[int]:
    """Numbers of the records a POST of this entity answered with (201 and its `id`)."""
    from labctl import har

    out = []
    for p in parts:
        for e in p.exchanges:
            if (e.method == "POST" and 200 <= e.status < 300 and isinstance(e.data, dict)
                    and har._entity(e.route) == d.entity):  # fmt: skip
                n = _id_of(e.data.get(d.id_key), d.pattern)
                if n is not None:
                    out.append(n)
    return out


def _records_seen(parts: list[Part], d: ListDesign, shapes: set[str]) -> None:
    """Fill `d.seen` from the list rows and from each record's own GET (`/tickets/{id}`), and
    give the list's columns the fields only the record's own answer shows."""
    from labctl import har

    for p in parts:
        for e in p.exchanges:
            if e.method != "GET" or not (200 <= e.status < 300) or e.data is None:
                continue
            if har._entity(e.route) != d.entity:
                continue
            single = _items(e.data) is None
            for item in har._items(e.data):
                n = _id_of(item.get(d.id_key), d.pattern)
                if n is None:
                    continue
                row = d.seen.setdefault(n, {})
                for k, v in item.items():
                    if isinstance(v, (dict, list)):
                        continue
                    row.setdefault(k, v)
                    if single and not (isinstance(v, str) and _masked(v, shapes)):
                        d.values.setdefault(k, []).append(v)


def _pat(pattern: tuple) -> dict:
    return {"prefix": pattern[0], "sep": pattern[1], "width": pattern[2]}


def _pool(names: list[str], avoid: set[str]) -> list[str]:
    return [n for n in names if n.lower() not in avoid]


def _terms(c: Core) -> None:
    """A synthetic search word for each recorded search (none for one that found nothing)."""
    pool = _pool(LAST, c.words)
    used: list[str] = []
    for d in c.lists.values():
        for s in d.searches:
            if s.rule == "contains" and s.total > 0:
                s.term = next((w for w in pool if w not in used), f"Term{len(used) + 1}")
                used.append(s.term)
                for e in getattr(s, "exchanges", []):
                    for item in _items(e.data) or []:
                        n = _id_of(item.get(d.id_key), d.pattern) if d.pattern else None
                        if n is not None:
                            s.hits.add(n)


# --- the seed ------------------------------------------------------------------------------------


def _column_values(col: str, d: ListDesign) -> list:
    for k, v in d.values.items():
        if _key(k) == _key(col):
            return v
    return []


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def seed_rows(
    c: Core, tables: list, scales: dict[str, int] | None = None
) -> tuple[dict, list[str]]:
    """{table: rows} and notes, for the schema's tables (labctl.source.sqlalchemy_models.Table)."""
    from labctl.data import observed as obs

    scales = scales or {}
    rows: dict[str, list[dict]] = {}
    notes: list[str] = []
    first_pool = _pool(FIRST, c.words)
    terms = {s.term for d in c.lists.values() for s in d.searches if s.term}
    others = [w for w in _pool(LAST, c.words) if w not in terms]
    done: set[str] = set()
    for d in c.lists.values():
        if not d.entity:
            continue
        table = obs._resolve_table(d.entity, {_key(k) for k in d.values}, tables)
        if table is None or table.name in done:
            continue
        done.add(table.name)
        out = []
        for i in range(d.rows):
            n = (d.lowest or 1) + i
            row: dict = {}
            for col in table.columns:
                row[col.name] = _cell(c, d, col, i, n, first_pool, others, scales)
            out.append(row)
        rows[table.name] = out
        what = f"{d.rows} row(s)"
        if d.pattern and d.rows:
            what += (f", `{col_of(table, d.id_key)}` from `{stepfacts.id_text(_pat(d.pattern), d.lowest)}`"
                     f" to `{stepfacts.id_text(_pat(d.pattern), d.lowest + d.rows - 1)}` without gaps")  # fmt: skip
        notes.append(f"`{table.name}`: {what} ({d.note or 'from ' + d.route})")
    for t in tables:
        if t.name not in rows:
            rows[t.name] = []
            notes.append(f"`{t.name}`: no rows (no recorded list showed its records)")
    return rows, notes


def col_of(table, key: str | None) -> str:
    return next((c.name for c in table.columns if _key(c.name) == _key(key or "")), key or "?")


def _cell(c: Core, d: ListDesign, col, i: int, n: int, first: list[str], others: list[str],
          scales: dict[str, int]):  # fmt: skip
    from labctl.data import observed as obs

    k = _key(col.name)
    if d.id_key and k == _key(d.id_key) and d.pattern:
        return stepfacts.id_text(_pat(d.pattern), n)
    words = []
    for s in d.searches:
        if s.key and _key(s.key) == k and s.term and s.rule in ("contains", "equals"):
            hits = sorted(s.hits) or []
            extra = [x for x in range(d.lowest or 1, (d.lowest or 1) + d.rows) if x not in hits]
            chosen = set((hits + extra)[: s.total])
            if n in chosen:
                words.append(s.term)
    values = _column_values(col.name, d)
    info = obs.classify_column(col.name, values) if values else {"kind": "unknown"}
    kind = info.get("kind")
    fn, ln = (
        first[i % len(first)] if first else "Avery",
        others[i % len(others)] if others else "Vale",
    )
    if any(_key(s.key or "") == k and s.term for s in d.searches):
        return f"{fn} {' '.join(words) if words else ln}"
    was = next((v for kk, v in d.seen.get(n, {}).items() if _key(kk) == k), None)
    if was == "" and kind not in ("code", "boolean"):
        return ""  # the record showed this field empty: empty here too
    if kind == "code" and info.get("values"):
        vals = list(info["values"])
        if isinstance(was, str) and was in vals:
            return was  # this record's own code, so a filter finds the rows the recording found
        return vals[i % len(vals)]
    if kind == "date" and info.get("range"):
        lo = date.fromisoformat(info["range"][0] + "-01")
        y, m = int(info["range"][1][:4]), int(info["range"][1][5:7])
        hi = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
        return (lo + timedelta(days=(i * 3) % max(1, (hi - lo).days + 1))).isoformat()
    if kind == "datetime" and info.get("range"):
        return info["range"][0] + f"-{1 + i % 28:02d}T09:{i % 60:02d}:00"
    if kind == "number" and info.get("range"):
        lo, hi = (float(x) for x in info["range"])
        scale = scales.get(col.name, int(info.get("scale", 0) or 0))
        span = max(1.0, hi - lo)
        value = lo + ((i * 61.37) % span)
        if scale:
            return format(
                Decimal(str(round(value, scale))).quantize(Decimal(1).scaleb(-scale)), "f"
            )
        return int(value)
    if kind == "boolean":
        return was if isinstance(was, bool) else i % 2 == 0
    if kind == "identifier" and info.get("pattern"):
        return obs.make_from_pattern(info["pattern"], i + 1)
    if kind == "email":
        return f"{fn}.{ln}{i + 1}@example.test".lower()
    if kind == "phone":
        return f"0100 555 {i + 1:04d}"
    if kind in ("personal", "text"):
        return f"{fn} {ln}"
    return None


def seed_sql(app: str, rows: dict, tables: list, engine: str, notes: list[str]) -> str:
    from labctl.source.sqlalchemy_models import ALIASES, ordered

    eng = ALIASES.get(engine, engine)
    qc = {"mssql": ("[", "]"), "mysql": ("`", "`")}.get(eng, ('"', '"'))

    def q(name: str) -> str:
        return qc[0] + name.replace(qc[1], qc[1] * 2) + qc[1]

    def lit(value, col) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return ("TRUE" if value else "FALSE") if eng == "postgres" else ("1" if value else "0")
        if isinstance(value, (int, float)) or (col.type.startswith(("NUMERIC", "DECIMAL"))
                                               and re.fullmatch(r"-?\d+(\.\d+)?", str(value))):  # fmt: skip
            return str(value)
        return _q(value)

    lines = [f"-- Seed data for the {app} demo app. Generated by labctl brief with the replay values "
             "(steps/*.values.yaml);", "-- do not edit by hand. Synthetic values only, in the shapes "
             "the recordings showed. Load exactly these rows,",
             "-- once, into the empty tables of db/schema.sql: the acceptance replay depends on "
             f"them. Dialect: {engine}.", *[f"-- {n}" for n in notes]]  # fmt: skip
    for table in ordered(tables):
        data = rows.get(table.name) or []
        if not data:
            continue
        cols = [c for c in table.columns if c.name in data[0]]
        lines.append(f"\n-- {table.name}: {len(data)} row(s)")
        for r in data:
            lines.append(f"INSERT INTO {q(table.name)} ({', '.join(q(c.name) for c in cols)}) "
                         f"VALUES ({', '.join(lit(r[c.name], c) for c in cols)});")  # fmt: skip
    return "\n".join(lines) + "\n"


# --- replay values -------------------------------------------------------------------------------


@dataclass
class Value:
    app: str
    part: str
    seq: int
    global_seq: int | None
    action: str
    field: str | None
    shape: str | None
    value: str
    why: str
    outcome: str = ""


class Words:
    def __init__(self, avoid: set[str]):
        self.pool = _pool(TYPED, avoid) or ["Synthetic"]
        self.n = 0

    def take(self) -> str:
        w = self.pool[self.n % len(self.pool)]
        self.n += 1
        return w + ("" if self.n <= len(self.pool) else str(self.n // len(self.pool)))


def _from_shape(shape: str | None, node, words: Words, k: int, refused: bool = False) -> str:
    """A synthetic value of the shape (letters A, digits 9), fitting the field's own attributes."""
    attrs = node.attrs if node is not None else {}
    kind = (attrs.get("type") or "").lower() if node is not None else ""
    name = " ".join(sanitize.name_words(attrs.get("name") or "", attrs.get("id") or ""))
    if kind == "password" or "pass" in name:
        return f"Synthetic-pass-{k + 1}"
    if kind == "date" or (shape or "").strip() in ("9-9-9",):
        return (date(2027, 1, 15) + timedelta(days=7 * k)).isoformat()
    if kind == "number":
        lo = int(attrs["min"]) if re.fullmatch(r"-?\d+", attrs.get("min") or "") else 1
        hi = int(attrs["max"]) if re.fullmatch(r"-?\d+", attrs.get("max") or "") else lo + 9
        if refused:
            return str(lo - 1)
        return str(min(hi, lo + 1))
    if shape is None:
        return "abc" if refused else words.take()
    if kind == "email" or "@" in shape:
        local = shape.split("@", 1)[0]
        made = _fill(local, words, refused, lower=True)
        return made if refused and "@" not in shape else f"{made}@example.test"
    lower = any(w in name for w in ("user", "login", "mail"))
    return _fill(shape, words, refused, lower=lower, decimals=True)


def _fill(shape: str, words: Words, refused: bool, lower: bool = False,
          decimals: bool = False) -> str:  # fmt: skip
    out, digits = [], 0
    runs = re.findall(r"A+|9+|[^A9]", shape)
    for i, r in enumerate(runs):
        if r.startswith("A"):
            w = REFUSED_WORDS[len(out) % len(REFUSED_WORDS)] if refused else words.take()
            out.append(w.lower() if lower else w)
        elif r.startswith("9"):
            after_point = i > 0 and runs[i - 1] == "." and decimals
            if after_point:
                out.append("50")
            elif decimals and i + 1 < len(runs) and runs[i + 1] == ".":
                out.append(str(40 + 7 * words.n))
            else:
                out.append(["0100", "555", f"{101 + digits:04d}"][min(digits, 2)])
            digits += 1
        else:
            out.append(r)
    return "".join(out)


@dataclass
class Plan:
    """The values of one recording across its applications, and each app's core."""

    recording: str
    cores: dict[str, Core]
    values: dict[tuple[str, int], Value] = field(default_factory=dict)  # (app, part seq)
    across: list[dict] = field(default_factory=list)
    validations: dict[str, list[dict]] = field(default_factory=dict)
    display: dict[str, list[dict]] = field(default_factory=dict)


def _sequence(root: Path, rec_name: str) -> dict:
    path = Path(root) / "evidence" / "_sequences" / f"{rec_name}.flow.yaml"
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.is_file() else {}
    except (OSError, yaml.YAMLError):
        return {}


def plan(root: Path, rec_name: str, app: str) -> Plan:
    """The values for recording `rec_name` (the part of `app`, and of every application the same
    recording went through, for the values that travel between them)."""
    root = Path(root)
    seq = _sequence(root, rec_name)
    apps = [a for a in (seq.get("apps") or []) if (root / "evidence" / a / rec_name).is_dir()]
    if app not in apps:
        apps = [app]
    cores = {a: core(root, a) for a in apps}
    p = Plan(rec_name, cores)
    parts = {a: next((x for x in cores[a].parts if x.name == rec_name), None) for a in apps}
    parts = {a: x for a, x in parts.items() if x is not None}
    _assign(p, parts, seq.get("travels") or [])
    for a, part in parts.items():
        p.display[a] = display(part, p)
        p.validations[a] = validations(part, p)
    return p


def _needs(part: Part) -> list:
    from labctl.behaviour import bindings as bnd
    from labctl.botlog.recording import load as load_rec

    return [n for n in bnd.needs(load_rec(part.path)) if n.kind == "value"]


def _refused(part: Part, step: dict) -> bool:
    """Typed again into the same field before the form was accepted: this value was refused."""
    effects = part.facts.effects
    nxt = next((e for e in effects if e["n"] > step["seq"]), None)
    if nxt is None:
        return False
    if any(r.split()[-1][:1] == "3" for r in nxt["requests"]) or (
            nxt.get("then") and nxt["then"] != nxt.get("screen")):  # fmt: skip
        return False
    later = [s for s in part.raw if s["seq"] > step["seq"] and s.get("action") == "type"
             and s.get("selector") == step.get("selector")]  # fmt: skip
    if not later:
        return False
    leave = next((e for e in effects if e["n"] > step["seq"] and (
        any(r.split()[-1][:1] == "3" for r in e["requests"]) or
        (e.get("then") and e["then"] != e.get("screen")))), None)  # fmt: skip
    return leave is None or later[0]["seq"] < leave["n"]


def _outcome(part: Part, step: dict, search: Search | None) -> str:
    nxt = next((e for e in part.facts.effects if e["n"] > step["seq"]
                or (e["n"] == step["seq"] and step.get("action") != "type")), None)  # fmt: skip
    if nxt is None:
        return ""
    bits = []
    if nxt["requests"]:
        bits.append(", ".join(nxt["requests"]))
    if nxt.get("then"):
        bits.append(f"then {nxt['then']}")
    if nxt["messages"]:
        bits.append("; ".join(f'{m["selector"]} "{m["text"]}"' for m in nxt["messages"]))
    if search is not None:
        if search.total:
            bits.append(f"the answer counted {search.total} row(s)" + (
                f" over {search.pages} page(s) of up to {search.page_size}"
                if search.pages and search.pages > 1 else ""))  # fmt: skip
        else:
            bits.append("the answer was empty (no rows)")
    return f"after step {nxt['n']}: " + "; ".join(bits) if bits else ""


def _select_value(part: Part, step: dict) -> tuple[str | None, str]:
    """The label of the option chosen, read from the request that followed (its value)."""
    node = _node(part.html_at(step["seq"]), step.get("selector"))
    opts = _options(node)
    name = node.attrs.get("name") if node is not None else None
    for seq in sorted(part.owner):
        if seq < step["seq"]:
            continue
        for e in part.owner[seq]:
            sent = {**e.query, **e.form}
            if name and name in sent:
                label = next((lab for v, lab in opts if v == sent[name]), None)
                if label:
                    return label, f"the option whose value `{sent[name]}` the next request sent"
    idx = step.get("option_index")
    if isinstance(idx, int) and 0 <= idx < len(opts):
        return opts[idx][1], "the option at the recorded position"
    real = [lab for v, lab in opts if v]
    return (real[0] if real else None), "the first option (the evidence does not show which)"


def _assign(p: Plan, parts: dict[str, Part], travels: list[dict]) -> None:
    rows = []
    for a, part in parts.items():
        for n in _needs(part):
            st = part.step(n.seq)
            rows.append((st.get("global_seq") if st.get("global_seq") is not None else n.seq, a, n))
    rows.sort(key=lambda r: (r[0], r[1]))
    avoid = set().union(*(c.words for c in p.cores.values()))
    words = Words(avoid)
    by_global: dict[tuple[str, int], Value] = {}
    into = {(t["to"]["app"], t["to"]["step"]): t for t in travels}
    searches = {(a, s.step): (d, s) for a, c in p.cores.items() for d in c.lists.values()
                for s in d.searches if s.part == p.recording}  # fmt: skip
    for k, (g, a, need) in enumerate(rows):
        part = parts[a]
        st = part.step(need.seq)
        node = _node(part.html_at(st["seq"]), st.get("selector"))
        value, why = None, ""
        travel = into.get((a, g))
        found = searches.get((a, need.seq))
        if need.action == "select":
            value, why = _select_value(part, st)
        if value is None and travel is not None:
            value, why = _travelled(p, travel, by_global, parts)
        if found is not None:
            d, s = found
            if s.rule == "none":
                value = _absent(d, s, words)
                why = "matches no seeded row: the recorded answer was empty"
            elif s.rule == "contains" and s.term:
                value = s.term
                why = (f"{s.total} seeded row(s) have it in `{s.key}` (db/seed.sql), as many as the "
                       "recorded answer counted")  # fmt: skip
            elif (s.rule == "equals" and value is None and d.pattern and d.id_key
                  and _key(s.key or "") == _key(d.id_key)):  # fmt: skip
                value, why = _existing_or_new(p, a, d, st)
        if value is None and _refused(part, st):
            value = _from_shape(need.shape, node, words, k, refused=True)
            why = "refused in the recording (typed again before the form was accepted): a value that breaks the rule the message states"
        if value is None:
            same = [v for v in by_global.values() if v.app == a and v.field == need.field
                    and v.shape == need.shape and "refused" not in v.why]  # fmt: skip
            if same and need.shape:
                value, why = same[-1].value, f"the same input as step {same[-1].seq} again"
        if value is None:
            value = _from_shape(need.shape, node, words, k)
            why = "a synthetic value of the recorded shape"
        v = Value(a, part.name, need.seq, st.get("global_seq"), need.action, need.field,
                  need.shape, str(value), why, _outcome(part, st, found[1] if found else None))  # fmt: skip
        p.values[(a, need.seq)] = v
        by_global[(a, g)] = v
    for t in travels:
        f, to = t["from"], t["to"]
        dest = by_global.get((to["app"], to["step"]))
        if dest is None:
            continue
        src = (f"what was typed into `{f.get('field')}` on `{f['app']}` (step {f['step']})"
               if t.get("how") == "typed" else
               f"what `{f['app']}` shows in `{f.get('element')}` (step {f['step']})")  # fmt: skip
        why = dest.why
        part = parts.get(f["app"])
        if t.get("how") != "typed" and part is not None and any(
                d["element"] == f.get("element") and d.get("computed") for d in display(part, p)):  # fmt: skip
            why = (f"that value is computed by `{f['app']}` by a rule the evidence does not hold, so "
                   f"the replay types a synthetic amount instead; it need not equal what "
                   f"`{f['app']}` shows")  # fmt: skip
        p.across.append({"from": f, "to": to, "value": dest.value, "said": (
            f"`{to['app']}` step {to['step']} types `{dest.value}` into `{to.get('field')}`: in "
            f"the recording it was {src}; {why}")})  # fmt: skip


def _absent(d: ListDesign, s: Search, words: Words) -> str:
    if s.key and d.id_key and _key(s.key) == _key(d.id_key) and d.pattern:
        return stepfacts.id_text(_pat(d.pattern), 10 ** d.pattern[2] - 2)
    return "Nomatch" + words.take()


def _existing_or_new(p: Plan, a: str, d: ListDesign, st: dict) -> tuple[str, str]:
    made = [x for x in p.cores[a].created.get(d.pattern, []) if x[1] < st["seq"]]
    if made:
        n = p.cores[a].next_id(d.pattern, len(made) - 1)
        return (
            stepfacts.id_text(_pat(d.pattern), n),
            "the record this application made earlier in the flow (next number after the seed)",
        )
    return (stepfacts.id_text(_pat(d.pattern), d.lowest or 1),
            "an identifier that db/seed.sql holds")  # fmt: skip


def _travelled(p: Plan, t: dict, done: dict, parts: dict[str, Part]) -> tuple[str | None, str]:
    f = t["from"]
    src = done.get((f["app"], f["step"]))
    if t.get("how") == "typed":
        if src is not None:
            return (
                src.value,
                f"the value typed into `{f.get('field')}` on `{f['app']}` (step {f['step']})",
            )
        return None, ""
    c = p.cores.get(f["app"])
    part = parts.get(f["app"])
    if c is None or part is None:
        return None, ""
    local = next((s["seq"] for s in part.raw if s.get("global_seq") == f["step"]), f["step"])
    for pattern, made in c.created.items():
        mine = [
            m for m in made if m[0] == p.recording and m[2] == f.get("element") and m[1] <= local
        ]
        if mine:
            k = [m for m in made if m[0] == p.recording].index(mine[-1])
            n = c.next_id(pattern, k)
            if n is not None:
                return (stepfacts.id_text(_pat(pattern), n),
                        f"the number `{f['app']}` gives its new record on its seed (the highest "
                        f"seeded + 1), shown in `{f.get('element')}`")  # fmt: skip
    echo = next((e for e in display(part, p, shallow=True) if e["element"] == f.get("element")
                 and e.get("echo")), None)  # fmt: skip
    if echo is not None:
        src = done.get((f["app"], echo["echo_global"])) if echo.get("echo_global") else None
        if src is not None:
            return (
                src.value,
                f"what `{f['app']}` shows there: the value typed at its step {echo['echo']}",
            )
    return None, ""


# --- what the pages show -------------------------------------------------------------------------


def _data_elements(html: str):
    """(element, text, node) of the data a page shows: description values, `data-field`
    elements, table cells under their column header, and identifier-like leaves."""
    from labctl.behaviour import dom as domlib

    root = domlib.parse_html(html)
    for node in domlib._walk(root):
        if node.tag in ("script", "style") or any(a.tag == "tl-frame" for a in _up(node)):
            continue
        text = domlib._text(node)
        if node.tag == "dd" and node.id:
            yield f"dd#{node.id}", text, node
        elif "data-field" in node.attrs:
            yield f"[data-field={node.attrs['data-field']}]", text, node
        elif node.tag == "td":
            table = next((a for a in _up(node) if a.tag == "table"), None)
            if table is None or not table.id:
                continue
            heads = [domlib._text(h) for h in domlib._walk(table) if h.tag == "th"]
            row = node.parent
            cells = [x for x in (row.children if row else []) if x.tag == "td"]
            i = next((j for j, x in enumerate(cells) if x is node), -1)
            if 0 <= i < len(heads):
                yield f"table#{table.id} column {heads[i]}", text, node
        elif node.tag in ("b", "strong", "span", "output") and (node.id or node.classes) and text \
                and not node.children and stepfacts.ID_VALUE.match(text):  # fmt: skip
            yield (
                (f"{node.tag}#{node.id}" if node.id else f"{node.tag}.{node.classes[0]}"),
                text,
                node,
            )


def _up(node):
    cur = node.parent
    while cur is not None:
        yield cur
        cur = cur.parent


def _format(texts: list[str]) -> tuple[str, str | None]:
    """(format rule, decimals or kind) of unmasked texts."""
    from labctl.data import observed as obs

    if not texts:
        return "", None
    if all(TWO_DEC.match(t) for t in texts):
        return "a number with two decimals (`999.99`)", "2"
    if all(DECIMAL.match(t) for t in texts):
        n = {len(t.split(".")[1]) for t in texts}
        return (f"a number with {n.pop()} decimals" if len(n) == 1 else "a decimal number"), "d"
    if all(WHOLE.match(t) for t in texts):
        return "a whole number", "0"
    if all(ISO_DATE.match(t) for t in texts):
        return "a date `YYYY-MM-DD`", "date"
    if all(ISO_TIME.match(t) for t in texts):
        return "a date and time (ISO 8601)", "datetime"
    ids = [stepfacts.ID_VALUE.match(t) for t in texts]
    if all(ids):
        m = ids[0]
        return f"an identifier `{m.group(1)}{m.group(2)}` + {len(m.group(3))} digits", "id"
    distinct = sorted(set(texts))
    if len(distinct) <= 6 and all(obs.is_vocab_value(t) for t in distinct):
        return "one of " + ", ".join(f"`{t}`" for t in distinct) + " (the values seen)", "words"
    return "text (masked in the snapshots: no format beyond its shape)", None


def display(part: Part, p: Plan | None = None, shallow: bool = False) -> list[dict]:
    """The display rule of each data field this part's pages showed."""
    seen: dict[str, dict] = {}
    shapes = _shapes([part])
    later = _shapes([x for c in (p.cores.values() if p else []) for x in c.parts])
    fields = [s for s in part.raw if s.get("action") in ("type", "select", "check", "uncheck")]
    for seq in sorted(part.doms):
        html = part.doms[seq]
        screen = stepfacts._pattern(part.step(seq).get("url"))
        for element, text, node in _data_elements(html):
            e = seen.setdefault(element, {"element": element, "screen": screen, "texts": [],
                                          "masked": [], "first": seq, "node": node,
                                          "html": html})  # fmt: skip
            if text and (_masked(text, shapes | later)):
                e["masked"].append((seq, text))
            elif text:
                e["texts"].append(text)
    out = []
    for element, e in seen.items():
        words_el = sanitize.name_words(re.sub(r"^(\w+)?[#.\[]|data-field=|\]|table#\S+ column ",
                                              " ", element))  # fmt: skip
        words_el -= {"saved", "summary", "value", "field", "data", "dd", "td", "table", "column"}
        listed = element.startswith("table#")  # a list shows records, not what was just typed
        until = e["masked"][0][0] if e["masked"] else e["first"] + 10**6
        related = [] if listed else [s for s in fields if s["seq"] < until
                                     and words_el & _field_words(part, s)]  # fmt: skip
        personal = bool(PERSONAL.search(" ".join(words_el)))
        rule, kind = _format([] if personal else e["texts"])
        if personal and e["texts"]:
            rule = "text (a name or other personal data: masked in the snapshots)"
        row = {"element": element, "screen": e["screen"],
               "seen_at": e["masked"][0][0] if e["masked"] else e["first"]}  # fmt: skip
        typed_rel = [s for s in related if s.get("action") == "type"]
        if e["masked"] and not listed:
            text = e["masked"][0][1]
            same = [s for s in typed_rel if s.get("typed_shape") == text]
            prior = [s for s in part.raw if s.get("action") == "type" and s["seq"] < until
                     and s.get("typed_shape") == text]  # fmt: skip
            unshaped = [s for s in typed_rel if not s.get("typed_shape")]
            src = (same or prior or unshaped or [None])[-1]
            if src is not None:
                row["echo"] = src["seq"]
                row["echo_global"] = src.get("global_seq")
                row["shows"] = f"what step {_num(src)} typed into `{src.get('selector')}`"
                row["format"] = "as typed"
            elif not e["texts"]:
                row["shows"] = "a value masked in the recording (it was typed later in the flow)"
        sel = [s for s in related if s.get("action") == "select"]
        if sel and e["texts"]:
            node = _node(part.html_at(sel[-1]["seq"]), sel[-1].get("selector"))
            opts = _options(node)
            if all(t in [lab for _, lab in opts] for t in e["texts"]):
                row["shows"] = (
                    f"the label of the option chosen at step {_num(sel[-1])} (`{sel[-1].get('selector')}`)"
                )
            elif all(t in [v for v, _ in opts] for t in e["texts"]):
                row["shows"] = (f"the option **value** (code) chosen at step {_num(sel[-1])} "
                                f"(`{sel[-1].get('selector')}`: "
                                + ", ".join(f"`{v}` = {lab}" for v, lab in opts if v)
                                + "), not its label")  # fmt: skip
        ticks = [s for s in related if s.get("action") in ("check", "uncheck")]
        if ticks and kind == "words":
            row["shows"] = f"a word for whether `{ticks[-1].get('selector')}` was ticked"
        if rule:
            row["format"] = rule
        numeric = e["masked"] and set(e["masked"][0][1]) <= set("9.,-")
        if numeric and kind is None and row.get("echo") is not None and not e["texts"]:
            sib = _sibling_decimals(e["html"], element)
            if sib:
                row["format"] = (f"a number with two decimals, like `{sib}` on the same page "
                                 "(inferred: the typed value itself is masked)")  # fmt: skip
        computed = (words_el & COMPUTED_WORDS) and row.get("echo") is None and kind not in ("words",) \
            and (kind in ("2", "d", "0") or e["masked"])  # fmt: skip
        if computed:
            row["computed"] = True
            row["shows"] = ("a value the application computes; the rule is unknown (the evidence "
                            "shows no price or factor it could be derived from)")  # fmt: skip
            if e["texts"] and element.startswith("table#"):
                row["shows"] += "; seeded rows show the value db/seed.sql gives them"
        if not shallow or row.get("echo") is not None:
            out.append(row)
    return out


def _field_words(part: Part, step: dict) -> set[str]:
    node = _node(part.html_at(step["seq"]), step.get("selector"))
    names = [step.get("selector") or ""]
    if node is not None:
        names += [node.attrs.get("name") or "", node.id or ""]
    return sanitize.name_words(*[re.sub(r"^\w+[#.]|\[name=\"?|\"?\]", " ", n) for n in names]) - {
        "input",
        "select",
        "textarea",
        "name",
    }


def _sibling_decimals(html: str, element: str) -> str | None:
    for other, text, _ in _data_elements(html):
        if other != element and other[:6] == element[:6] and TWO_DEC.match(text or ""):
            return other
    return None


# --- validations ---------------------------------------------------------------------------------


def _visible_messages(part: Part, html: str | None) -> list[tuple[str, str]]:
    from labctl import har

    if not html:
        return []
    known, vocab = har.page_knowledge([html], [])
    info = sanitize.page(html, known, vocab)
    return [(m["selector"], m["text"]) for m in info.messages if not m["hidden"]]


def validations(part: Part, p: Plan) -> list[dict]:
    """Per recorded message: what was typed (shape and the replay's value), what was left empty,
    what was accepted later, and the rule when the evidence supports one."""
    out = []
    effects = part.facts.effects
    for i, e in enumerate(effects):
        if not e["messages"] or any(r.split()[-1][:1] == "3" for r in e["requests"]):
            continue  # nothing said, or the form was taken (a redirect followed)
        start = max([x["n"] for x in effects[:i] if x.get("then")] + [0])
        typed = [s for s in part.raw if start < s["seq"] < e["n"] and s.get("action") == "type"]
        html = part.html_at(e["n"])
        form_fields = _form_fields(html, part.step(e["n"]).get("selector"))
        for m in e["messages"]:
            if not ERRORISH.search(m["selector"]):
                continue  # a page counter or a "nothing found": an outcome, not a refusal
            mid = m["selector"].split("#", 1)[1] if "#" in m["selector"] else ""
            target = [
                f for f in form_fields if mid and f[0] and mid.lower().startswith(f[0].lower())
            ]
            shown = next((r["n"] for r in part.facts.steps if r["n"] >= e["n"]
                          and m in (r.get("messages") or [])), e["n"])  # fmt: skip
            entry = {"message": m["text"], "element": m["selector"], "screen": e.get("screen"),
                     "step": e["n"], "global": e.get("global"), "shown_at": shown}  # fmt: skip
            refused, accepted, empty = [], [], []
            fields = target or form_fields
            for fid, sel, node in fields:
                mine = [s for s in typed if _node(html, s.get("selector")) is not None
                        and _same_field(s.get("selector"), sel, fid)]  # fmt: skip
                if mine:
                    v = p.values.get((part.app, mine[-1]["seq"]))
                    if v and "refused" in v.why:
                        refused.append({"field": sel, "step": mine[-1]["seq"],
                                        "global": mine[-1].get("global_seq"),
                                        "shape": mine[-1].get("typed_shape"), "value": v.value})  # fmt: skip
                elif node is not None and node.tag != "select" and (node.attrs.get("type") or "") \
                        not in ("checkbox", "radio") and (target or len(fields) <= 3):  # fmt: skip
                    empty.append(sel)
                later = [s for s in part.raw if s["seq"] > e["n"] and s.get("action") == "type"
                         and _same_field(s.get("selector"), sel, fid)]  # fmt: skip
                ok = [s for s in later if (v := p.values.get((part.app, s["seq"])))
                      and "refused" not in v.why]  # fmt: skip
                if ok and (mine or target):
                    v = p.values[(part.app, ok[0]["seq"])]
                    accepted.append({"field": sel, "step": ok[0]["seq"],
                                     "global": ok[0].get("global_seq"),
                                     "shape": ok[0].get("typed_shape"), "value": v.value})  # fmt: skip
                rule = _rule(node, m["text"], bool(empty and sel in empty), bool(refused))
                if rule:
                    entry.setdefault("rule", []).append(f"`{sel}`: {rule}")
            if refused:
                entry["refused"] = refused
            if empty:
                entry["left_empty"] = empty
            if accepted:
                entry["accepted"] = accepted
            if refused or empty or accepted:
                out.append(entry)
    # a refused input whose submit brought up no new message (the field's message stayed on the
    # page): it belongs to the message named after that field
    told = {(r["step"]) for x in out for r in x.get("refused") or []}
    for v in sorted((v for v in p.values.values() if v.app == part.app and "refused" in v.why
                     and v.seq not in told), key=lambda v: v.seq):  # fmt: skip
        for x in out:
            mid = x["element"].split("#", 1)[1] if "#" in x["element"] else ""
            fid = re.sub(r"(?i)(error|message|msg|hint)$", "", mid)
            if fid and _same_field(v.field, "", fid) and x["step"] < v.seq:
                x.setdefault("refused", []).append({"field": v.field, "step": v.seq,
                                                    "global": v.global_seq, "shape": v.shape,
                                                    "value": v.value})  # fmt: skip
                break
    return out


def _form_fields(html: str | None, selector: str | None) -> list[tuple[str | None, str, object]]:
    from labctl.behaviour import dom as domlib

    if not html:
        return []
    root = domlib.parse_html(html)
    forms = [n for n in domlib._walk(root) if n.tag == "form"]
    target = _node(html, selector) if selector else None
    form = next((f for f in forms if target is not None and (f is target or any(
        x is target for x in domlib._walk(f)))), forms[0] if len(forms) == 1 else None)  # fmt: skip
    if form is None:
        return []
    out = []
    for n in domlib._walk(form):
        if n.tag in ("input", "select", "textarea") and (n.attrs.get("type") or "").lower() not in (
                "hidden", "submit", "button"):  # fmt: skip
            sel = f"{n.tag}#{n.id}" if n.id else f'{n.tag}[name="{n.attrs.get("name")}"]'
            out.append((n.id or n.attrs.get("name"), sel, n))
    return out


def _same_field(selector: str | None, sel: str, fid: str | None) -> bool:
    if not selector:
        return False
    return selector == sel or (
        fid is not None
        and bool(
            re.search(
                rf"#{re.escape(fid)}(?![\w-])|\[name=[\"']?{re.escape(fid)}[\"']?\]", selector
            )
        )
    )


def _rule(node, message: str, empty: bool, refused: bool) -> str | None:
    """A hypothesis only where the field's attributes, the message's example or an empty refusal
    support one."""
    if node is None:
        return None
    a = node.attrs
    bits = []
    if empty:
        bits.append("required (refused while empty)")
    if a.get("min") or a.get("max"):
        bits.append(f"a number from {a.get('min', '?')} to {a.get('max', '?')} (the field's own "
                    "min and max)")  # fmt: skip
    if (a.get("type") or "").lower() == "email":
        bits.append("an e-mail address (the field's type)")
    if (a.get("inputmode") or "").lower() in ("decimal", "numeric") and refused:
        bits.append(f"a number (the field's inputmode is {a['inputmode']})")
    if a.get("maxlength"):
        bits.append(f"at most {a['maxlength']} characters (its maxlength)")
    m = EXAMPLE.search(message)
    if m and refused:
        bits.append(f"the pattern `{m.group(1)}{m.group(2)}` + {len(m.group(3))} digits (the "
                    f"message's example `{m.group(0)}`)")  # fmt: skip
    return ("hypothesis: " + "; ".join(bits)) if bits else None


# --- writing -------------------------------------------------------------------------------------


def values_doc(p: Plan, app: str) -> dict:
    vals = sorted((v for v in p.values.values() if v.app == app), key=lambda v: v.seq)
    c = p.cores[app]
    searches = []
    for d in c.lists.values():
        for s in d.searches:
            if s.part != p.recording:
                continue
            v = p.values.get((app, s.step))
            entry = {"step": s.step, "global": s.global_step, "field": s.field,
                     "request": f"GET {s.route}", "parameter": s.param,
                     "value": v.value if v else s.term,
                     "with": dict(s.others) or None, "recorded_rows": s.total,
                     "recorded_pages": s.pages, "page_size": d.page_size or None,
                     "match": {"contains": f"rows whose `{s.key}` holds the word (hypothesis: "
                                           "case-insensitive, as a whole word or part of the text)",
                               "equals": f"rows whose `{s.key}` is exactly the value",
                               "none": "no row"}[s.rule]}  # fmt: skip
            if s.rule == "contains" and s.term and d.pattern:
                hits = sorted(s.hits)
                extra = [
                    x for x in range((d.lowest or 1), (d.lowest or 1) + d.rows) if x not in hits
                ]
                chosen = sorted((hits + extra)[: s.total])
                entry["seeded_matches"] = [stepfacts.id_text(_pat(d.pattern), x) for x in chosen]
            searches.append({k: x for k, x in entry.items() if x is not None})
    return {
        "recording": p.recording,
        "app": app,
        "about": "The synthetic values the acceptance replay types or chooses at each step of this "
        "application (this application's step numbers; `global` is the whole recording's), with "
        "what the recording showed after it. Generated with db/seed.sql from the same evidence, "
        "so the recorded outcomes happen again on that seed. No value a person typed is here.",
        "steps": [
            {
                k: x
                for k, x in {
                    "n": v.seq,
                    "global": v.global_seq,
                    "action": v.action,
                    "field": v.field,
                    "shape": v.shape,
                    "value": v.value,
                    "why": v.why,
                    "recorded": v.outcome or None,
                }.items()
                if x is not None
            }
            for v in vals
        ],  # fmt: skip
        "searches": searches,
        "validations": p.validations.get(app) or [],
        "display": [
            {k: x for k, x in d.items() if k not in ("echo_global",)}
            for d in p.display.get(app) or []
        ],  # fmt: skip
        "across": [a["said"] for a in p.across if app in (a["from"]["app"], a["to"]["app"])],
    }


def write_app(root: Path, app: str, tables: list, engine: str) -> dict:
    """Write the seed and the values files of one application; returns what the brief says."""
    root = Path(root)
    out = root / "apps" / app
    steps_dir = out / "steps"
    for old in steps_dir.glob(f"*{VALUES}") if steps_dir.is_dir() else []:
        old.unlink()
    parts = stepfacts.parts_of(root, app)
    if not parts:
        for name in (SEED_SQL, SEED_JSON):
            (out / "db" / name).unlink(missing_ok=True)
        return {}
    c = core(root, app)
    docs = []
    for part in parts:
        p = plan(root, part.name, app)
        doc = values_doc(p, app)
        docs.append(doc)
        steps_dir.mkdir(parents=True, exist_ok=True)
        head = ("# Replay values and recorded outcomes (generated by labctl brief with db/seed.sql)."
                "\n# Synthetic only: never a value a person typed.\n")  # fmt: skip
        _write(
            steps_dir / f"{part.name}{VALUES}",
            head + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100),
        )
    scales = {}
    for doc in docs:
        for d in doc["display"]:
            m = re.match(r"table#\S+ column (.+)$", d["element"])
            if m and "two decimals" in d.get("format", ""):
                scales[re.sub(r"[^a-z0-9]+", "_", m.group(1).lower()).strip("_")] = 2
    rows, notes = seed_rows(c, tables, scales)
    (out / "db").mkdir(parents=True, exist_ok=True)
    _write(out / "db" / SEED_SQL, seed_sql(app, rows, tables, engine, notes))
    _write(
        out / "db" / SEED_JSON, json.dumps(rows, indent=1, ensure_ascii=False, default=str) + "\n"
    )
    return {"rows": rows, "notes": notes, "docs": docs, "core": c}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))


def apply_bindings(root: Path, app: str) -> int:
    """Give the generated values to the evidence's bindings (`bindings.yaml` of each of the app's
    recording parts), marked `generated:` so a value a person wrote is never replaced. Returns how
    many steps were given a value."""
    from labctl.behaviour import bindings as bnd
    from labctl.botlog.recording import load as load_rec

    root = Path(root)
    given = 0
    for part in stepfacts.parts_of(root, app):
        doc_path = root / "apps" / app / "steps" / f"{part.name}{VALUES}"
        if not doc_path.is_file():
            continue
        doc = yaml.safe_load(doc_path.read_text(encoding="utf-8")) or {}
        have = bnd.load(part / bnd.FILE) if (part / bnd.FILE).is_file() else bnd.Bindings()
        for row in doc.get("steps") or []:
            seq = row["n"]
            if seq in have.values and seq not in have.generated:
                continue
            have.values[seq] = str(row["value"])
            have.generated.add(seq)
            given += 1
        rec = load_rec(part)
        (part / bnd.FILE).write_text(bnd.template(rec, have), encoding="utf-8")
    return given


def apply_whole(root: Path, recording: Path) -> int:
    """The same values in the whole recording's bindings (its own step numbers), for a replay of
    the recording across all of its applications."""
    from labctl.behaviour import bindings as bnd
    from labctl.botlog.recording import load as load_rec

    root, recording = Path(root), Path(recording)
    have = bnd.load(recording / bnd.FILE) if (recording / bnd.FILE).is_file() else bnd.Bindings()
    given = 0
    for path in sorted((root / "apps").glob(f"*/steps/{recording.name}{VALUES}")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for row in doc.get("steps") or []:
            seq = row.get("global")
            if seq is None or (seq in have.values and seq not in have.generated):
                continue
            have.values[seq] = str(row["value"])
            have.generated.add(seq)
            given += 1
    if given:
        (recording / bnd.FILE).write_text(bnd.template(load_rec(recording), have), encoding="utf-8")
    return given


def acceptance(root: Path, app: str, rec_name: str) -> dict | None:
    """Draft acceptance cases from the values file: only what the package states is required;
    a value the application computes by a rule nobody stated is excluded, with the reason."""
    path = Path(root) / "apps" / app / "steps" / f"{rec_name}{VALUES}"
    if not path.is_file():
        return None
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases, excluded = [], []
    for d in doc.get("display") or []:
        if d.get("computed"):
            excluded.append(f"{d['element']} on {d['screen']}: computed by the application, rule "
                            "unknown (not in the package), so not judged")  # fmt: skip
        elif d.get("echo") is not None and not d["element"].startswith("table#"):
            saved = next((s for s in (doc.get("steps") or []) if s["n"] == d["echo"]), None)
            if saved is None:
                continue
            cases.append({"id": f"shows-{_slug(d['element'])}", "after": d["seen_at"],
                          "check": "text", "locator": d["element"], "equals": {"binding": d["echo"]},
                          "note": f"{d['screen']} shows {d.get('shows')}"})  # fmt: skip
    for v in doc.get("validations") or []:
        # the message as the application wrote it: only one with no masked data in it
        if v.get("refused") and not re.search(r"\b9[9.,]*\b|\bX+\b|\bx+\b", v["message"]):
            cases.append({"id": f"refused-{v['step']}", "after": v.get("shown_at", v["step"]),
                          "check": "texts", "locator": v["element"], "contains": v["message"],
                          "note": f"the replay types {', '.join(r['value'] for r in v['refused'])}"})  # fmt: skip
    return {"version": 1, "recording": rec_name,
            "scope": [f"{app}: values the package states (steps/{rec_name}{VALUES})"],
            "excluded": excluded, "cases": cases}  # fmt: skip


def acceptance_whole(root: Path, recording: Path) -> dict | None:
    """The applications' draft cases in the whole recording's step numbers."""
    root, recording = Path(root), Path(recording)
    cases, excluded, scope = [], [], []
    for path in sorted((root / "apps").glob(f"*/steps/{recording.name}{VALUES}")):
        app = path.parent.parent.name
        part = root / "evidence" / app / recording.name
        if not (part / "steps.jsonl").is_file():
            continue
        glob = {s["seq"]: s.get("global_seq") for s in _raw_steps(part)}
        draft = acceptance(root, app, recording.name) or {}
        for c in draft.get("cases") or []:
            c = dict(c)
            if isinstance(c.get("after"), int):
                if glob.get(c["after"]) is None:
                    continue
                c["after"] = glob[c["after"]]
            ref = c.get("equals")
            if isinstance(ref, dict) and isinstance(ref.get("binding"), int):
                if glob.get(ref["binding"]) is None:
                    continue
                c["equals"] = {"binding": glob[ref["binding"]]}
            c["id"] = f"{app}-{c['id']}"
            cases.append(c)
        excluded += [f"{app}: {x}" for x in draft.get("excluded") or []]
        scope += draft.get("scope") or []
    if not cases and not excluded:
        return None
    return {"version": 1, "recording": recording.name, "scope": scope, "excluded": excluded,
            "cases": cases}  # fmt: skip


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


# --- the brief -----------------------------------------------------------------------------------


def _shape(row: dict) -> str:
    return f"`{row['shape']}`" if row.get("shape") else "not recorded"


def brief_section(app: str, done: dict) -> str:
    if not done or not done.get("docs"):
        return ""
    rows, notes, docs = done["rows"], done["notes"], done["docs"]
    out = ["## Seed data and replay values\n",
           "**Load exactly [`db/seed.sql`](db/seed.sql) at start-up**, once, into the empty tables "
           "of `db/schema.sql` (the same rows as JSON: `db/seed.json`). Add no other rows and change "
           "none: the acceptance replay types the values in "
           + ", ".join(f"`apps/{app}/steps/{d['recording']}{VALUES}`" for d in docs)
           + " and expects the outcomes the recording showed on exactly these rows. Every value is "
           "synthetic; identifiers and code values are the application's own, as it showed them.\n"]  # fmt: skip
    out += [f"- {n}" for n in notes]
    out.append("")
    for d in docs:
        if len(docs) > 1:
            out.append(f"Recording `{d['recording']}`:\n")
        if d["searches"]:
            out.append("**Searches.** What the replay searches for, how the recorded answer matched "
                       "it, and the seeded rows that match:\n")  # fmt: skip
            for s in d["searches"]:
                mode = (
                    (" with " + ", ".join(f"`{k}={v}`" for k, v in s["with"].items()))
                    if s.get("with")
                    else ""
                )
                pages = (f" over {s['recorded_pages']} page(s) of up to {s['page_size']}"
                         if s.get("recorded_pages") and s.get("recorded_pages", 0) > 1 else "")  # fmt: skip
                hits = (
                    (": " + ", ".join(f"`{x}`" for x in s["seeded_matches"]))
                    if s.get("seeded_matches")
                    else ""
                )
                out.append(f"- step {s['step']}" + (f" (whole recording {s['global']})" if s.get("global") else "")
                           + f": `{s['field']}` = `{s['value']}` → `{s['request']}` `{s['parameter']}`{mode}: "
                           f"the recording's answer counted {s['recorded_rows']} row(s){pages}; "
                           f"matches {s['match']}{hits}.")  # fmt: skip
            out.append("")
        if d["validations"]:
            out.append("**Validation messages: a refused and an accepted input for each.** The "
                       "replay types these values; the rule is stated only where the evidence "
                       "supports it (a hypothesis):\n")  # fmt: skip
            for v in d["validations"]:
                bits = []
                for r in v.get("refused") or []:
                    bits.append(f"refused `{r['value']}` in `{r['field']}` (step {r['step']}; the "
                                f"recorded input had the shape {_shape(r)})")  # fmt: skip
                if v.get("left_empty"):
                    bits.append(
                        "refused with "
                        + ", ".join(f"`{x}`" for x in v["left_empty"])
                        + " left empty"
                    )
                for r in v.get("accepted") or []:
                    bits.append(f"accepted `{r['value']}` in `{r['field']}` (step {r['step']}, shape "
                                f"{_shape(r)})")  # fmt: skip
                rule = (
                    ("; " + "; ".join(v["rule"]))
                    if v.get("rule")
                    else "; rule: as the message words it"
                )
                out.append(f"- `{v['element']}` \"{v['message']}\" (step {v['step']}, `{v['screen']}`): "
                           + "; ".join(bits) + rule + ".")  # fmt: skip
            out.append("")
        if d["display"]:
            out.append("**What the pages show, and in which format.** Show each value this way:\n")
            out.append("| Screen | Element | Shows | Format |\n|---|---|---|---|")
            for x in d["display"]:
                out.append(f"| `{x['screen']}` | `{x['element']}` | {x.get('shows') or 'data (see the seed)'} | "
                           f"{x.get('format') or '-'} |")  # fmt: skip
            out.append("")
            comp = [x for x in d["display"] if x.get("computed")]
            if comp:
                out.append("Computed values: " + ", ".join(f"`{x['element']}`" for x in comp)
                           + ". The evidence holds no rule for them (prices and factors are masked "
                           "or never shown): choose one, say which in BUILD-NOTES.md; the acceptance "
                           "replay does not judge these values (they are excluded with this "
                           "reason).\n")  # fmt: skip
        if d["across"]:
            out.append("**Values shared with the other applications** (their packages use the "
                       "same values):\n")  # fmt: skip
            out += [f"- {a}" for a in d["across"]]
            out.append("")
        out.append(f"Every typing step, its value and what followed: `apps/{app}/steps/"
                   f"{d['recording']}{VALUES}`.\n")  # fmt: skip
    if not any(rows.values()):
        out.append("No table gets seeded rows: start with empty tables.\n")
    return "\n".join(out) + "\n"
