"""The API catalogue of an application: every endpoint its pages called, for the builder.

A recording's HAR holds each request and answer the pages made. The builder needs that contract
(method, address, parameters, bodies, statuses, which action sent the call, what the page did
with the answer) and must never get the data. So the catalogue carries, per endpoint:

- the method and address pattern, path parameters, query parameters with what each one was (text
  a person typed, a code of the interface, a page number) and the control that sets it;
- the request headers that matter: the content type and how the call was authorised, BY NAME only
  (a cookie's name, the scheme of an `Authorization` header), never a credential;
- a schema of the request body and of the answer per status (keys, types, formats, lengths,
  code lists, identifier patterns);
- every recorded call in order, with a SYNTHETIC request and answer: the same keys, types and
  codes, the values generated. One consistent world lies behind them: a record listed is the row
  of `db/seed.json` with that identifier, a typed value is the one `steps/*.values.yaml` gives
  that step, the record a call created is the one a later call reads, changes and deletes;
- the screen and the action that sent each call (with step numbers), and where the page showed
  the answer's fields (an element or a table column), when the page after the call lets one tell.

What is kept as recorded, by the rules the rest of the lab already follows: field names; code
values (a closed list by `labctl.data.observed.classify_column`, or the value of an option the
page snapshots keep); identifiers the application generated (`labctl.stepfacts`: its output,
kept so the demo numbers its records the same way); paging and count numbers; and the
application's own error messages (answers of status 400 and above, through
`sanitize.mask_message`). Everything else is data and is replaced. `leaks()` checks the written
files against every other value the HAR holds; `write()` refuses to leave a file that fails it.

Files: `apps/<app>/api/catalogue.json` (the index, the field map and the paging facts) and one
`apps/<app>/api/<endpoint>.json` per endpoint.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from labctl import sanitize, seedplan, stepfacts

DIR = "api"
INDEX = "catalogue.json"
FORMAT = "twinlab-api-catalogue/1"
MAX_CALLS = 60  # recorded calls carried per endpoint (the schema covers all of them)
ASSET = re.compile(r"\.(js|mjs|css|map|png|jpe?g|gif|svg|ico|woff2?|ttf|eot|webp|avif)$", re.I)
KEEP_NUMBERS = seedplan.PAGE_PARAMS | set(stepfacts.COUNT_FIELDS) | {"pages", "page", "total"}
PASSWORD = re.compile(r"pass(word|wd)?|pwd|secret|pin$", re.I)
SECRET_FIELD = re.compile(r"token|secret|jwt|api[-_]?key|bearer|credential|csrf|session_?id", re.I)
MIN_LEAK = 4  # a value shorter than this is not searched for (it would match by chance)
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
URL = re.compile(r"https?://[A-Za-z0-9.-]+(:\d+)?(/[^\s?#]*)?")
COOKIE_PAIR = re.compile(r"(?P<name>[A-Za-z0-9_.!#$%&*+^`|~-]{1,64})=(?P<value>[^;\s]*)")
HOST_LABELS = {"api", "www", "app", "hls", "cdn", "static", "media", "img", "images", "auth"}


# --- reading -------------------------------------------------------------------------------------


@dataclass
class Ctx:
    """One recording part of the application with what the generators need."""

    part: seedplan.Part
    values: dict[int, str]  # step seq -> the replay value (steps/<recording>.values.yaml)
    known: set[str]  # data words of the recording, lower-case (har.page_knowledge)
    vocab: set[str]  # interface words
    shapes: set[str]  # shapes of what was typed

    @property
    def name(self) -> str:
        return self.part.name

    def steps(self) -> list[dict]:
        return self.part.facts.steps

    def step(self, n: int) -> dict | None:
        return next((s for s in self.part.facts.steps if s.get("n") == n), None)

    def root_step(self, n: int) -> dict | None:
        """The action a person took that step `n` belongs to (a submit or a page load a click
        caused is that click)."""
        s = self.step(n)
        seen = set()
        while s is not None and s.get("caused_by") and s["caused_by"] not in seen:
            seen.add(s["n"])
            nxt = self.step(s["caused_by"])
            if nxt is None:
                break
            s = nxt
        return s


def load(root: Path, app: str) -> list[Ctx]:
    from labctl import har

    out = []
    for path in stepfacts.parts_of(Path(root), app):
        try:
            part = seedplan.load_part(app, path)
        except (OSError, ValueError):
            continue
        vfile = Path(root) / "apps" / app / "steps" / f"{path.name}{seedplan.VALUES}"
        values: dict[int, str] = {}
        if vfile.is_file():
            try:
                doc = yaml.safe_load(vfile.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                doc = {}
            for row in doc.get("steps") or []:
                if isinstance(row, dict) and isinstance(row.get("n"), int) and "value" in row:
                    values[row["n"]] = str(row["value"])
        rec = har.load(path)
        hosts = {urlsplit(str(s.get("url") or "")).netloc.lower() for s in part.raw
                 if str(s.get("url") or "").startswith("http")}  # fmt: skip
        every = har.exchanges(rec.har)
        hosts |= har.api_hosts(hosts, every)  # the pages' own API on another origin of their site
        entries = [e for e in every if not hosts or e.host in hosts]
        known, vocab = har.page_knowledge([*part.doms.values(), *part.befores.values()], entries)
        shapes = {str(s["typed_shape"]) for s in part.raw if s.get("typed_shape")}
        out.append(Ctx(part, values, known, vocab, shapes))
    return out


def is_api(e: seedplan.Ex) -> bool:
    """A call a page made for data or to change something: a JSON exchange, a form post, or any
    request that is not a GET. Page documents and assets are screens and files, not endpoints."""
    if ASSET.search(e.path) or e.status == 0:
        return False
    if e.method != "GET":
        return True
    return e.data is not None or "json" in e.resp_type


def _slug(method: str, route: str) -> str:
    text = re.sub(r"[{}]", "", route)
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return f"{method.lower()}-{text or 'root'}"[:80]


def _key(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _is_id(value) -> bool:
    return isinstance(value, str) and bool(stepfacts.ID_VALUE.match(value.strip()))


def lookfeel_image(value, kept: "Kept") -> bool:
    from labctl import lookfeel

    return lookfeel.names_image(value, kept.images)


def _plain_id(value) -> bool:
    """A record's key as a plain number or UUID (`1`, `"7"`, a GUID)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, str) and (value.isdigit() and len(value) <= 12
                                       or bool(UUID.fullmatch(value)))  # fmt: skip


def _leaves(data, path: str = ""):
    """(path, key, value) for every scalar of a JSON value; list items share the path `x[]`."""
    if isinstance(data, dict):
        for k, v in data.items():
            yield from _leaves(v, f"{path}.{k}" if path else str(k))
    elif isinstance(data, list):
        for v in data:
            yield from _leaves(v, f"{path}[]")
    else:
        yield path, re.split(r"[.\[]", path.replace("[]", ""))[-1] if path else "", data


# --- what may be kept ----------------------------------------------------------------------------


@dataclass
class Kept:
    """The values that are the interface's own, not data."""

    options: set[str] = field(default_factory=set)  # option values the page snapshots keep
    codes: dict[str, set[str]] = field(default_factory=dict)  # field key -> its code list
    # field name -> [(page file stem, selector of its control)], in the order of the snapshots
    by_page: dict[str, list] = field(default_factory=dict)
    labels: dict[tuple, str] = field(default_factory=dict)  # (field name, option value) -> label
    images: set[str] = field(default_factory=set)  # paths of the images the look carries

    def control(self, name: str, screens=()) -> str | None:
        """The control named `name`, on one of `screens` (address patterns) when it is there."""
        from labctl.model_build import screen_name

        found = self.by_page.get(name) or []
        stems = {screen_name(s) for s in screens if s}
        return next((sel for stem, sel in found if stem in stems),
                    found[0][1] if found else None)  # fmt: skip

    def is_code_word(self, word: str) -> bool:
        """An upper-case code of some code list (`OPEN`, `NOT_SET`), as a word of a sentence."""
        return bool(re.fullmatch(r"[A-Z][A-Z]+(_[A-Z]+)*", word)) and (
            word in self.options or any(word in v for v in self.codes.values()))  # fmt: skip

    def code(self, key: str, value) -> bool:
        """A code of the interface, or the address of an image the look carries (its real file
        name, so the rebuilt page shows the picture `look/assets/` holds)."""
        from labctl import lookfeel

        return isinstance(value, str) and value != "" and (
            value in self.codes.get(_key(key), ()) or value in self.options
            or lookfeel.names_image(value, self.images)
        )  # fmt: skip


def kept_values(root: Path, app: str, ctxs: list[Ctx]) -> Kept:
    from labctl import lookfeel
    from labctl.behaviour import dom as domlib
    from labctl.data import observed as obs

    kept = Kept()
    kept.images = lookfeel.interface_image_paths([c.part.path for c in ctxs])
    pages = Path(root) / "apps" / app / "pages"
    for path in sorted(pages.glob("*.html")) if pages.is_dir() else []:
        tree = domlib.parse_html(path.read_text(encoding="utf-8", errors="replace"))
        for node in domlib._walk(tree):
            # a facet's or a choice's value is the interface's code, as an option's is
            kind = (node.attrs.get("type") or "").lower()
            choice = node.tag == "input" and kind in ("checkbox", "radio")
            if (node.tag == "option" or choice) and node.attrs.get("value"):
                value = node.attrs["value"]
                if not re.fullmatch(r"[^A-Za-z0-9]*[Xx9][Xx9 ._/-]*", value):  # masked: data
                    kept.options.add(value)
                owner = next((a for a in stepfacts._ancestors(node) if a.tag == "select"), None)
                if owner is not None and owner.attrs.get("name"):
                    kept.labels.setdefault((owner.attrs["name"], value), domlib._text(node))
            if node.tag in ("input", "select", "textarea") and node.attrs.get("name"):
                sel = f"{node.tag}#{node.id}" if node.id else (
                    f'{node.tag}[name="{node.attrs["name"]}"]')  # fmt: skip
                kept.by_page.setdefault(node.attrs["name"], []).append((path.stem, sel))
    seen: dict[str, list] = {}
    for c in ctxs:
        for e in c.part.exchanges:
            if not is_api(e):
                continue
            for k, v in e.query.items():
                seen.setdefault(_key(k), []).append(v)
            for k, v in e.form.items() if e.body is None else []:
                seen.setdefault(_key(k), []).append(v)
            for data in (e.body, e.data if e.status < 400 else None):
                for _, k, v in _leaves(data):
                    if isinstance(v, str):
                        seen.setdefault(_key(k), []).append(v)
    for k, vals in seen.items():
        real = [v for v in vals if v != "" and not any(v in c.shapes for c in ctxs)]
        if not real:
            continue
        info = obs.classify_column(k, real)
        if info.get("kind") == "code" and info.get("values"):
            kept.codes[k] = set(info["values"])
    return kept


# --- synthetic values ----------------------------------------------------------------------------


class World:
    """The synthetic records behind every example: seeded rows, and what the calls did to them.

    `records[entity][id]` maps a field key to its synthetic value. A record is first taken from
    `db/seed.json` (the row with that identifier); fields the seed does not hold, and records
    the recording made, are generated once and then reused, so every call agrees.
    """

    def __init__(self, seed: dict, kept: Kept, avoid: set[str], id_keys: dict[str, str]):
        self.seed = seed
        self.kept = kept
        self.words = seedplan.Words(avoid)
        self.records: dict[str, dict] = {}
        self.alias: dict[str, str] = {}  # an observed data string -> its one synthetic stand-in
        self.id_keys = id_keys
        self.n = 0
        self.today: set[str] = set()
        self.columns: dict[frozenset, str | None] = {}  # the fields of a record -> its entity

    # -- records --
    def entity_of(self, entity: str | None, fields: dict) -> str | None:
        """The table a record belongs to: the seed table, or the first entity seen, whose
        columns are exactly these fields (so `POST /view` reads the `entries` row the list
        showed), else `entity`."""
        keys = frozenset(_key(k) for k in fields)
        if len(keys) < 2:
            return entity
        for name, rows in self.seed.items():
            if rows and isinstance(rows[0], dict) and frozenset(_key(k) for k in rows[0]) == keys:
                return name
        return self.columns.setdefault(keys, entity)

    def table_rows(self, entity: str | None) -> tuple[str | None, list[dict]]:
        from labctl.data import observed as obs

        if not entity:
            return None, []
        for name, rows in self.seed.items():
            if obs.singular(name) == obs.singular(entity):
                return name, rows
        return None, []

    def record(self, entity: str | None, rid, anon: str | None = None) -> dict:
        store = self.records.setdefault(entity or "", {})
        key = rid if rid is not None else f"anon:{anon}"
        if key in store:
            return store[key]
        rec: dict = {}
        if rid is not None:
            idk = self.id_keys.get(entity or "", "id")
            _, rows = self.table_rows(entity)
            row = next((r for r in rows if str(r.get(idk, r.get("id"))) == str(rid)), None)
            if row is None:
                row = next((r for r in rows if any(str(v) == str(rid) for v in r.values())), None)
            if row is not None:
                rec = {_key(k): v for k, v in row.items() if v is not None}
        store[key] = rec
        return rec

    # -- leaves --
    def number(self, value):
        self.n += 1
        text = repr(value) if isinstance(value, float) else str(value)
        made = "".join(str((self.n + i) % 9 + 1) if ch.isdigit() else ch
                       for i, ch in enumerate(text))  # fmt: skip
        try:
            return float(made) if isinstance(value, float) else int(made)
        except ValueError:
            return 1

    def text(self, value: str) -> str:
        """A synthetic string of the same shape; the same observed string gives the same one."""
        from labctl.behaviour import dom as domlib

        if value in self.alias:
            return self.alias[value]
        if seedplan.ISO_DATE.match(value):
            made = (date(2027, 1, 15) + timedelta(days=len(self.alias) % 28)).isoformat()
        elif seedplan.ISO_TIME.match(value):
            made = (date(2027, 1, 15) + timedelta(days=len(self.alias) % 28)).isoformat() + (
                re.sub(r"\d", "0", value[10:]).replace("T00", "T09", 1))  # fmt: skip
        elif sanitize.EMAIL.fullmatch(value):
            made = f"{self.words.take()}.{self.words.take()}@example.test".lower()
        elif UUID.fullmatch(value):  # a UUID stays one
            made = f"00000000-0000-4000-8000-{len(self.alias) + 1:012d}"
        elif URL.fullmatch(value):  # an address stays one, at a reserved example host
            parts = urlsplit(value)
            label = parts.netloc.split(".", 1)[0].lower()
            label = label if label in HOST_LABELS else "app"
            path = sanitize.route_pattern(parts.path) if parts.path not in ("", "/") else ""
            made = f"{parts.scheme}://{label}.example.test{path}"
        else:
            # word by word, so a code of the interface inside a sentence stays where it is
            # ("... OPEN ... CLOSED"): codes are kept anyway, and they say what the text reports
            parts = re.split(r"(\s+)", value[:240])
            if len(parts) > 1 and any(self.kept.is_code_word(w) for w in parts):
                made = "".join(w if w.isspace() or self.kept.is_code_word(w)
                               else seedplan._fill(domlib.shape(w) or "A", self.words, False)
                               for w in parts if w)  # fmt: skip
            else:
                shape = domlib.shape(value) or "A"
                made = seedplan._fill(shape[:120], self.words, False)
        self.alias[value] = made
        return made


class Synth:
    """Turns one part's exchanges into synthetic requests and answers, in recorded order."""

    def __init__(self, ctx: Ctx, world: World, id_patterns: set[tuple]):
        self.c = ctx
        self.w = world
        self.id_patterns = id_patterns
        self.owner_of = {id(e): seq for seq, exs in ctx.part.owner.items() for e in exs}
        dates = {str(s.get("ts") or "")[:10] for s in ctx.part.raw}
        self.rec_dates = {d for d in dates if seedplan.ISO_DATE.match(d)}
        # A message the page showed is interface text as the page snapshot keeps it: an error
        # answer holding the same message is written the same way.
        self.messages = {
            sanitize.mask_message(m["text"], ctx.known): m["text"]
            for s in ctx.part.facts.steps
            for m in s.get("messages") or []
        }

    def typed_value(self, key: str, shape: str, e: seedplan.Ex) -> str | None:
        """The replay value of the typing step this masked value came from: the latest step
        before the call on the field named `key`, else the latest one with this shape."""
        at = self.owner_of.get(id(e))
        if at is None:
            return None
        named = shaped = None
        for s in self.c.part.raw:
            if s["seq"] > at or s.get("action") != "type" or s["seq"] not in self.c.values:
                continue
            node = seedplan._node(self.c.part.html_at(s["seq"]), s.get("selector"))
            names = {_key(n) for n in ((node.attrs.get("name") if node else None),
                                       (node.id if node else None)) if n}  # fmt: skip
            sel = _key(str(s.get("selector") or "").split("#")[-1])
            if _key(key) in names or _key(key) == sel:
                named = s
            if s.get("typed_shape") == shape:
                shaped = s
        hit = named or shaped
        return self.c.values[hit["seq"]] if hit else None

    def is_identifier(self, value) -> bool:
        m = stepfacts.ID_VALUE.match(value.strip()) if isinstance(value, str) else None
        return bool(m) and (m.group(1), m.group(2), len(m.group(3))) in self.id_patterns

    def leaf(self, key: str, value, e: seedplan.Ex, rec: dict | None, error: bool = False):
        from labctl import publicdata

        k = _key(key)
        if value is None or isinstance(value, bool):
            return value
        pub = publicdata.current()
        if pub is not None and not error and pub.field_public(e.method, e.route, key, value):
            return value  # declared public catalogue data, carried as recorded
        if isinstance(value, (int, float)):
            if k in {_key(x) for x in KEEP_NUMBERS} or stepfacts.ID_FIELD.search(str(key)):
                return value
            if rec is not None and isinstance(rec.get(k), (int, float)):
                return rec[k]
            if rec is not None and isinstance(rec.get(k), str) and re.fullmatch(
                    r"-?\d+(\.\d+)?", rec[k]):  # a seeded amount written as text  # fmt: skip
                return float(rec[k]) if isinstance(value, float) else int(float(rec[k]))
            made = self.w.number(value)
            if rec is not None:
                rec[k] = made
            return made
        if not isinstance(value, str):
            return None
        if value == "":
            if rec is not None:
                rec.setdefault(k, "")
            return ""
        if rec is not None and lookfeel_image(rec.get(k), self.w.kept):
            return str(rec[k])  # the record's carried photo, as its seed row has it
        if self.is_identifier(value) or self.w.kept.code(key, value):
            if rec is not None and lookfeel_image(value, self.w.kept):
                rec[k] = value
            return value
        if k in {_key(x) for x in KEEP_NUMBERS} and value.isdigit() and len(value) <= 6:
            return value  # a page number or a count sent as text
        if "cookie" in k and all(COOKIE_PAIR.fullmatch(p.strip()) for p in value.split(";")
                                  if p.strip()):  # fmt: skip
            pairs = [COOKIE_PAIR.fullmatch(p.strip()) for p in value.split(";") if p.strip()]
            return "; ".join(f"{m['name']}={self.w.text(m['value']) if m['value'] else ''}"
                             for m in pairs)  # fmt: skip
        if error:  # the application's own message; any value in it masked
            made = sanitize.mask_message(value, self.c.known)
            return self.messages.get(made, made)
        if rec is not None and rec.get(k) not in (None, ""):
            return str(rec[k])
        made = None
        masked = value in self.c.shapes or (PASSWORD.search(key) and seedplan.MASK.match(value))
        if masked:
            made = self.typed_value(key, value, e)
        if made is None and SECRET_FIELD.search(key):
            made = "synthetic-" + re.sub(r"[^a-z]", "", key.lower()) + "-0001"
        if made is None:
            if value in self.rec_dates:
                self.w.today.add(key)
            made = self.w.text(value)
        if rec is not None:
            rec[k] = made
        return made

    def path_id(self, e: seedplan.Ex) -> str | None:
        segs = [s for s in e.path.split("/") if s]
        pats = [s for s in e.route.split("/") if s]
        ids = [s for s, p in zip(segs, pats, strict=False) if p.startswith("{")]
        return ids[-1] if ids else None

    def obj(self, data, e: seedplan.Ex, entity: str | None, error: bool, top: bool = False):
        """A JSON value of the answer or the request, made synthetic."""
        from labctl import har

        if isinstance(data, list):
            return [self.obj(x, e, entity, error) for x in data]
        if not isinstance(data, dict):
            return self.leaf("", data, e, None, error)
        scalars = {k: v for k, v in data.items() if not isinstance(v, (dict, list))}
        entity = self.w.entity_of(entity, scalars)  # `/view` and `/bycat` show `entries` rows
        idk = self.w.id_keys.get(entity or "", "id")
        rid = data.get(idk) if self.is_identifier(data.get(idk)) or _plain_id(
            data.get(idk)) else None  # fmt: skip
        wrapper = any(isinstance(v, (list, dict)) for v in data.values()) and rid is None
        rec = None
        if not error and not wrapper:
            if rid is None and top and har._entity(e.route) == entity:
                last = e.route.rstrip("/").split("/")[-1]
                rid = self.path_id(e) if last.startswith("{") else None
            rec = self.w.record(entity, rid, json.dumps(scalars, sort_keys=True, default=str))
        out = {}
        for k, v in data.items():
            name = k if har._field(str(k)) != "{key}" else self.w.text(str(k))
            if isinstance(v, list):
                sub = entity if str(k).lower() in har.ITEM_KEYS else har._entity("/" + str(k))
                out[name] = [self.obj(x, e, sub, error) if isinstance(x, (dict, list))
                             else self.leaf(str(k), x, e, None, error) for x in v]  # fmt: skip
            elif isinstance(v, dict):
                out[name] = self.obj(v, e, har._entity("/" + str(k)), error)
            else:
                out[name] = self.leaf(str(k), v, e, rec, error)
        return out

    def request(self, e: seedplan.Ex, entity: str | None):
        """The synthetic body sent, and the record it writes to (changed before the answer is
        made, so the answer shows the change)."""
        from labctl import har

        body = e.body if e.body is not None else (dict(e.form) if e.form and e.method != "GET"
                                                  else None)  # fmt: skip
        if body is None:
            return None
        if not isinstance(body, dict):
            return self.obj(body, e, entity, False)
        out = {}
        for k, v in body.items():
            if isinstance(v, (dict, list)):
                out[k] = self.obj(v, e, har._entity("/" + str(k)), False)
            else:
                out[k] = self.leaf(str(k), v, e, None)
        ok = 200 <= e.status < 400
        last = e.route.rstrip("/").split("/")[-1]
        rid = self.path_id(e) if last.startswith("{") else None
        if rid is None and isinstance(e.data, dict):
            idk = self.w.id_keys.get(entity or "", "id")
            rid = e.data.get(idk) if self.is_identifier(e.data.get(idk)) else None
        if ok and rid is not None and har._entity(e.route) == entity and not PASSWORD.search(
                " ".join(map(str, body))):  # fmt: skip
            rec = self.w.record(entity, rid)
            for k, v in out.items():
                if not isinstance(v, (dict, list)):
                    rec[_key(k)] = v
        return out

    def query(self, e: seedplan.Ex) -> dict:
        return {k: self.leaf(k, v, e, None) for k, v in e.query.items()}

    def path(self, e: seedplan.Ex) -> str:
        segs = e.path.split("/")
        pats = e.route.split("/")
        out = []
        for s, p in zip(segs, pats, strict=False):
            if p.startswith("{") and not (self.is_identifier(s) or s.isdigit()):
                out.append(self.w.text(s))
            else:
                out.append(s)
        return "/".join(out) if len(segs) == len(pats) else e.route


# --- schemas -------------------------------------------------------------------------------------


def _leaf_schema(key: str, vals: list, kept: Kept, id_patterns: set, shapes: set[str]) -> dict:
    from labctl.behaviour import dom as domlib

    types = set()
    for v in vals:
        types.add("null" if v is None else "boolean" if isinstance(v, bool) else "integer"
                  if isinstance(v, int) else "number" if isinstance(v, float) else "string")  # fmt: skip
    out: dict = {"type": sorted(types)[0] if len(types) == 1 else sorted(types)}
    strings = [v for v in vals if isinstance(v, str)]
    numbers = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if numbers and _key(key) in {_key(x) for x in KEEP_NUMBERS}:
        out["minimum"], out["maximum"] = min(numbers), max(numbers)
    elif numbers:
        out["x-digits"] = sorted({len(re.sub(r"\D", "", str(v).split(".")[0])) for v in numbers})
        dec = {len(repr(v).split(".")[1]) for v in numbers if isinstance(v, float)}
        if dec:
            out["x-decimals"] = max(dec)
    real = [v for v in strings if v != ""]
    if not real:
        if strings:
            out["maxLength"] = 0
        return out
    if "" in strings:
        out["x-may-be-empty"] = True
    pats = {(m.group(1), m.group(2), len(m.group(3))) for v in real
            if (m := stepfacts.ID_VALUE.match(v.strip()))}  # fmt: skip
    if len(pats) == 1 and pats <= id_patterns and all(_is_id(v) for v in real):
        prefix, sep, width = next(iter(pats))
        out["pattern"] = f"^{re.escape(prefix + sep)}\\d{{{width}}}$"
        out["x-kind"] = "identifier the application generates"
        return out
    if all(kept.code(key, v) for v in real):
        out["enum"] = sorted(set(real)) + ([""] if "" in strings else [])
        out.pop("x-may-be-empty", None)
        return out
    if all(seedplan.ISO_DATE.match(v) for v in real):
        out["format"] = "date"
    elif all(seedplan.ISO_TIME.match(v) for v in real):
        out["format"] = "date-time"
    elif all(sanitize.EMAIL.fullmatch(v) for v in real if v not in shapes) and any(
            v not in shapes for v in real):  # fmt: skip
        out["format"] = "email"
    # lengths seen in the recording: a fact about its data, not a limit of the field
    out["x-length-seen"] = [min(map(len, strings)), max(map(len, strings))]
    out["x-shapes"] = sorted({domlib.shape(v) or "" for v in real})[:6]
    if any(v in shapes for v in real):
        out["x-source"] = "holds what a person typed (the recording kept its shape only)"
        out.pop("x-length-seen", None)
    return out


def schema_of(samples: list, kept: Kept, id_patterns: set, shapes: set[str],
              key: str = "", error: bool = False) -> dict:  # fmt: skip
    """A JSON-Schema-like description of the values observed at one place."""
    from labctl import har

    samples = list(samples)
    dicts = [s for s in samples if isinstance(s, dict)]
    lists = [s for s in samples if isinstance(s, list)]
    rest = [s for s in samples if not isinstance(s, (dict, list))]
    if dicts and not lists and not rest:
        props: dict[str, list] = {}
        dynamic: list = []
        for d in dicts:
            for k, v in d.items():
                if har._field(str(k)) == "{key}":
                    dynamic.append(v)
                else:
                    props.setdefault(str(k), []).append(v)
        out: dict = {"type": "object", "properties": {
            k: schema_of(v, kept, id_patterns, shapes, k, error) for k, v in props.items()}}  # fmt: skip
        req = [k for k in props if all(k in d for d in dicts)]
        if req:
            out["required"] = req
        if dynamic:
            out["additionalProperties"] = schema_of(dynamic, kept, id_patterns, shapes, key, error)
            out["x-keys"] = "the keys are values themselves (shown here as synthetic words)"
        return out
    if lists and not dicts and not rest:
        items = [x for s in lists for x in s]
        out = {"type": "array", "x-items-seen": [min(map(len, lists)), max(map(len, lists))]}
        if items:
            out["items"] = schema_of(items, kept, id_patterns, shapes, key, error)
        return out
    if rest and not dicts and not lists:
        if error and all(isinstance(v, str) for v in rest):
            return {"type": "string", "x-kind": "a message of the application, as written"}
        return _leaf_schema(key, rest, kept, id_patterns, shapes)
    return {"x-mixed": sorted({type(s).__name__ for s in samples})}


# --- where the page showed an answer -------------------------------------------------------------


def _shown(c: Ctx, e: seedplan.Ex, at: int | None) -> dict[str, list[str]]:
    """Answer field path -> the places of the page that showed its value after the call."""
    from labctl.behaviour import dom as domlib

    if at is None or e.data is None or e.status >= 400:
        return {}
    html = next((c.part.doms[n] for n in sorted(c.part.doms) if n >= at), None)
    if html is None:
        return {}
    texts: dict[str, list[str]] = {}
    tree = domlib.parse_html(html)
    for node in domlib._walk(tree):
        if node.tag in ("script", "style", "option", "html", "body", "head", "title"):
            continue
        place = None
        if node.tag in ("input", "textarea", "select"):
            value = node.attrs.get("value")
            if node.tag == "select":
                chosen = [
                    o for o in domlib._walk(node) if o.tag == "option" and "selected" in o.attrs
                ]
                value = chosen[0].attrs.get("value") if chosen else None
            if node.tag == "textarea":
                value = domlib._text(node)
            if value and (node.id or node.attrs.get("name")):
                place = f"{node.tag}#{node.id}" if node.id else (
                    f'{node.tag}[name="{node.attrs["name"]}"]')  # fmt: skip
                texts.setdefault(str(value).strip(), []).append(place)
            continue
        own = domlib._text(node)
        if not own or len(own) > 200 or any(domlib._text(ch) == own for ch in node.children):
            continue
        if node.tag in ("td", "th"):
            place = _cell_place(c, node)
        elif node.id:
            place = f"{node.tag}#{node.id}"
        elif node.classes:
            place = f"{node.tag}.{node.classes[0]}"
        if place:
            texts.setdefault(own, []).append(place)
    out: dict[str, list[str]] = {}
    for path, _, v in _leaves(e.data):
        if v is None or isinstance(v, bool) or v == "":
            continue
        text = str(v).strip()
        if len(text) < 2 and not isinstance(v, str):
            continue
        for place in texts.get(text, []):
            if place not in out.setdefault(path, []):
                out[path].append(place)
    return out


def _cell_place(c: Ctx, node) -> str | None:
    from labctl.behaviour import dom as domlib

    row = node.parent
    table = row
    while table is not None and table.tag != "table":
        table = table.parent
    if row is None or table is None:
        return None
    cells = [ch for ch in row.children if ch.tag in ("td", "th")]
    index = cells.index(node) if node in cells else -1
    heads = [h for h in domlib._walk(table) if h.tag == "th"
             and any(a.tag == "thead" for a in stepfacts._ancestors(h))]  # fmt: skip
    name = f"table#{table.id}" if table.id else (
        f"table.{table.classes[0]}" if table.classes else "table")  # fmt: skip
    if 0 <= index < len(heads):
        label = sanitize.interface_text(domlib._text(heads[index]), c.known, c.vocab, label=True)
        return f'{name} column "{label}"'
    return f"{name} column {index + 1}"


# --- building ------------------------------------------------------------------------------------


def _trigger(c: Ctx, e: seedplan.Ex, at: int | None) -> dict | None:
    if at is None:
        return None
    s = c.root_step(at)
    if s is None:
        return None
    page = sanitize.route_pattern(e.referer) if e.referer else None
    out = {"recording": c.name, "step": s["n"], "screen": page or s.get("screen")}
    if s.get("global") is not None:
        out["global_step"] = s["global"]
    took = s.get("screen")
    moved = page and took and page != took
    if s.get("action") == "goto" or moved:
        out["action"] = "page load"
        if moved and s.get("action") != "goto":
            out["after"] = f"{s['action']} {s.get('selector') or ''}".strip() + f" on {took}"
    else:
        out["action"] = s["action"]
        if s.get("selector"):
            out["selector"] = s["selector"]
        if s.get("key"):
            out["key"] = s["key"]
    return out


def _auth(e: seedplan.Ex, session_cookies: set[str]) -> str:
    if e.auth_scheme:
        return f"header `Authorization: {e.auth_scheme} <token>`"
    sent = sorted(set(e.cookies_sent) & session_cookies)
    if sent:
        return "cookie " + ", ".join(f"`{n}`" for n in sent)
    return "none"


def session_cookies(ctxs: list[Ctx]) -> set[str]:
    """Names of the cookies the application set during the recording."""
    return {n for c in ctxs for e in c.part.exchanges for n in e.cookies_set}


def build(root: Path, app: str, ctxs: list[Ctx] | None = None) -> dict:
    """{"index": the catalogue document, "endpoints": {file name: document}, "problems": [...]}."""
    from labctl import har

    root = Path(root)
    ctxs = load(root, app) if ctxs is None else ctxs
    kept = kept_values(root, app, ctxs)
    seed_file = root / "apps" / app / "db" / seedplan.SEED_JSON
    try:
        seed = json.loads(seed_file.read_text(encoding="utf-8")) if seed_file.is_file() else {}
    except ValueError:
        seed = {}
    id_patterns = {(i["prefix"], i["sep"], i["width"]) for c in ctxs
                   for i in [*c.part.facts.ids, *c.part.facts.generated] if i.get("width")}  # fmt: skip
    id_keys: dict[str, str] = {}
    for c in ctxs:
        for e in c.part.exchanges:
            if not is_api(e) or e.data is None or e.status >= 400:
                continue
            entity = har._entity(e.route) or ""
            for item in har._items(e.data):
                for k, v in item.items():
                    m = stepfacts.ID_VALUE.match(v.strip()) if isinstance(v, str) else None
                    if m and (m.group(1), m.group(2), len(m.group(3))) in id_patterns:
                        id_keys.setdefault(entity, str(k))
                        break
    avoid = set().union(*(c.part.words for c in ctxs)) if ctxs else set()
    world = World(seed if isinstance(seed, dict) else {}, kept, avoid, id_keys)
    cookies = session_cookies(ctxs)
    eps: dict[tuple[str, str], dict] = {}
    for c in ctxs:
        synth = Synth(c, world, id_patterns)
        for e in c.part.exchanges:
            if not is_api(e):
                continue
            ep = eps.setdefault((e.method, e.route), {"exs": [], "calls": [], "triggers": [],
                                                      "shown": {}, "auth": set()})  # fmt: skip
            entity = har._entity(e.route)
            at = synth.owner_of.get(id(e))
            error = e.status >= 400
            call: dict = {"recording": c.name}
            trig = _trigger(c, e, at)
            if trig:
                call["step"] = trig["step"]
                key = (trig.get("screen"), trig["action"], trig.get("selector"), trig.get("after"))
                have = next((t for t in ep["triggers"] if t["_key"] == key), None)
                if have is None:
                    have = {"_key": key, **{k: v for k, v in trig.items() if k not in (
                        "step", "global_step", "recording")}, "steps": []}  # fmt: skip
                    ep["triggers"].append(have)
                if trig["step"] not in have["steps"]:
                    have["steps"].append(trig["step"])
            call["path"] = synth.path(e)
            if e.query:
                call["query"] = synth.query(e)
            req = synth.request(e, entity)
            if req is not None:
                call["request"] = req
            call["status"] = e.status
            if e.location:
                call["location"] = sanitize.route_pattern(e.location)
            if e.data is not None:
                call["response"] = synth.obj(e.data, e, entity, error, top=True)
            if e.method == "DELETE" and 200 <= e.status < 300:
                call["note"] = "the record is gone afterwards: later lists do not show it"
            ep["exs"].append((c, e))
            ep["calls"].append(call)
            ep["auth"].add(_auth(e, cookies))
            for path, places in _shown(c, e, at).items():
                have = ep["shown"].setdefault(path, {})
                for p in places:
                    have[p] = have.get(p, 0) + 1
    # A place of the page belongs to the answer field that matched it most often: a date or a
    # name that happens to equal another answer's value is not shown there.
    best: dict[str, int] = {}
    for ep in eps.values():
        for places in ep["shown"].values():
            for p, n in places.items():
                best[p] = max(best.get(p, 0), n)
    for ep in eps.values():
        ep["shown"] = {path: [p for p, n in places.items() if n == best[p]]
                       for path, places in ep["shown"].items()}  # fmt: skip
        ep["shown"] = {path: places for path, places in ep["shown"].items() if places}
    endpoints: dict[str, dict] = {}
    index_rows = []
    problems: list[str] = []
    shapes = set().union(*(c.shapes for c in ctxs)) if ctxs else set()
    for (method, route), ep in eps.items():
        exs = [e for _, e in ep["exs"]]
        doc = _endpoint_doc(method, route, ep, exs, kept, id_patterns, shapes, world)
        name = _slug(method, route) + ".json"
        n = 2
        while name in endpoints:
            name = f"{_slug(method, route)}-{n}.json"
            n += 1
        endpoints[name] = doc
        for status, r in doc["responses"].items():
            if r.get("content_type", "").endswith("json") and "schema" not in r:
                problems.append(
                    f"{method} {route} {status}: the answer's body was not recorded "
                    "(the page did not read it): no schema"
                )
        index_rows.append({
            "id": doc["id"], "method": method, "path": route, "file": f"{DIR}/{name}",
            "entity": doc.get("entity"), "table": doc.get("table"),
            "auth": doc["request"]["auth"],
            "query": sorted(doc.get("query") or {}),
            "request_fields": sorted((doc["request"].get("schema") or {}).get("properties") or {}),
            "statuses": sorted(doc["responses"]),
            "triggered_by": [_trigger_text(t) for t in doc["triggered_by"]],
            "calls_recorded": doc["calls_recorded"],
        })  # fmt: skip
    index = {
        "format": FORMAT,
        "app": app,
        "about": ABOUT,
        "endpoints": index_rows,
        "lists": list_facts(ctxs, kept),
        "field_map": field_map(root, app, endpoints, kept, world),
        "privacy": "Every example value is synthetic. Kept as recorded: field names, code values, "
                   "identifiers the application generated, paging and count numbers, and the "
                   "application's own error messages. Checked by labctl.apicatalog.leaks.",
    }  # fmt: skip
    return {"index": index, "endpoints": endpoints, "problems": problems}


ABOUT = (
    "Every endpoint the recorded pages called. Each file under api/ gives one endpoint: its "
    "parameters, the schema and a synthetic example of the request and of each answer by status, "
    "the screen and action that sent it (step numbers of steps/<recording>.json), where the page "
    "showed the answer's fields, and every recorded call in order with synthetic values. The "
    "examples describe one consistent set of records: db/seed.json holds the rows, "
    "steps/<recording>.values.yaml the typed values. Build each endpoint to answer on the same "
    "method and path with the same keys, types, codes and statuses."
)


def _trigger_text(t: dict) -> str:
    if t["action"] == "page load":
        text = f"page load of {t.get('screen')}"
        return text + (f" (after {t['after']})" if t.get("after") else "")
    text = f"{t['action']} {t.get('selector') or ''}".strip()
    if t.get("key"):
        text += f" (key {t['key']})"
    return f"{text} on {t.get('screen')}"


def _parent_of(route: str) -> str | None:
    """`/api/tickets/{id}/history` -> `tickets`: the record a nested list belongs to."""
    segs = [s for s in route.split("/") if s]
    if len(segs) >= 3 and segs[-2].startswith("{") and not segs[-1].startswith("{"):
        return re.sub(r"[^a-z0-9_]+", "_", segs[-3].lower()).strip("_") or None
    return None


def _endpoint_doc(method, route, ep, exs, kept, id_patterns, shapes, world: World) -> dict:
    from labctl import har

    entity = har._entity(route)
    table, _ = world.table_rows(entity)
    doc: dict = {"id": _slug(method, route), "method": method, "path": route, "basis": "observed"}
    if entity:
        doc["entity"] = entity
    if table:
        doc["table"] = table
    screens = [t.get("screen") for t in ep["triggers"]]
    params = [s[1:-1] for s in route.split("/") if s.startswith("{")]
    if params:
        doc["path_params"] = {}
        for i, name in enumerate(params):
            seen = []
            for e in exs:
                segs = [s for s, p in zip(e.path.split("/"), e.route.split("/"), strict=False)
                        if p.startswith("{")]  # fmt: skip
                if i < len(segs):
                    seen.append(segs[i])
            info = _leaf_schema(name, seen, kept, id_patterns, shapes)
            info.pop("x-length-seen", None)
            info.pop("x-length-seen", None)
            doc["path_params"][name] = info
    query: dict[str, dict] = {}
    for e in exs:
        for k in e.query:
            query.setdefault(k, {})
    for k, info in query.items():
        vals = [e.query[k] for e in exs if k in e.query]
        info.update(_leaf_schema(k, vals, kept, id_patterns, shapes))
        info["sent_in"] = f"{len(vals)} of {len(exs)} call(s)"
        if _key(k) in {_key(x) for x in seedplan.PAGE_PARAMS}:
            info["x-kind"] = "paging"
            numbers = sorted({int(v) for v in vals if re.fullmatch(r"\d{1,6}", v)})
            if numbers:
                info["x-values-seen"] = numbers[:20]
            info.pop("x-shapes", None)
            info.pop("x-length-seen", None)
            info.pop("x-length-seen", None)
        if kept.control(k, screens):
            info["control"] = kept.control(k, screens)
    if query:
        doc["query"] = query
    bodies = [e.body if e.body is not None else (dict(e.form) if e.form and method != "GET" else None)
              for e in exs]  # fmt: skip
    bodies = [b for b in bodies if b is not None]
    types = sorted({e.req_type for e in exs if e.req_type})
    request: dict = {"auth": " or ".join(sorted(ep["auth"] - {"none"})) or "none"}
    if types:
        request["content_type"] = types[0] if len(types) == 1 else types
    if bodies:
        request["schema"] = schema_of(bodies, kept, id_patterns, shapes)
        for name, info in (request["schema"].get("properties") or {}).items():
            if kept.control(name, screens):
                info["control"] = kept.control(name, screens)
        first_ok = next((c for c, e in zip(ep["calls"], exs, strict=True)
                         if "request" in c and (300 <= e.status < 400 or (
                             200 <= e.status < 300 and "html" not in e.resp_type))),
                        None)  # the example is a call that was accepted, when one was  # fmt: skip
        for name, info in (request["schema"].get("properties") or {}).items():
            if PASSWORD.search(name):  # the recording kept neither the value nor its length
                for k in ("x-length-seen", "x-shapes"):
                    info.pop(k, None)
                info["x-source"] = "a password a person typed (never kept)"
        any_call = next((c for c in ep["calls"] if "request" in c), None)
        request["example"] = (first_ok or any_call or {}).get("request")
    doc["request"] = request
    responses: dict[str, dict] = {}
    for status in sorted({e.status for e in exs}):
        group = [(c, e) for c, e in zip(ep["calls"], exs, strict=True) if e.status == status]
        r: dict = {"count": len(group)}
        ctype = sorted({e.resp_type for _, e in group if e.resp_type})
        if ctype:
            r["content_type"] = ctype[0]
        datas = [e.data for _, e in group if e.data is not None]
        if datas:
            r["schema"] = schema_of(datas, kept, id_patterns, shapes, error=status >= 400)
            r["example"] = next(c["response"] for c, _ in group if "response" in c)
        else:
            empty = all(e.resp_empty for _, e in group)
            r["body"] = (
                "empty body: the answer had no content (status only); answer the same"
                if empty
                else "not recorded: the page did not read it"
                + "".join(
                    sorted(
                        {
                            f" (the answer had {e.resp_length} bytes)"
                            for _, e in group
                            if e.resp_length
                        }
                    )[:1]
                )
                + ". Answer a JSON object; where "
                "another status or method of this address shows a body, use its keys "
                "(a hypothesis: say so in BUILD-NOTES.md)"
                if any("json" in e.resp_type for _, e in group)
                else "an HTML page: the screen shown again, with its messages (see the brief's "
                "*What each recorded action did* for the step)"
                if any("html" in e.resp_type for _, e in group)
                else "none"
            )
        loc = sorted({sanitize.route_pattern(e.location) for _, e in group if e.location})
        if loc:
            r["location"] = loc[0] if len(loc) == 1 else loc
        made = sorted({n for _, e in group for n in e.cookies_set})
        if made:
            r["sets_cookie"] = made
        responses[str(status)] = r
    doc["responses"] = responses
    doc["triggered_by"] = [{k: v for k, v in t.items() if k != "_key"} for t in ep["triggers"]]
    if ep["shown"]:
        doc["shown"] = {k: v[:6] for k, v in sorted(ep["shown"].items())}
    doc["calls_recorded"] = len(ep["calls"])
    doc["calls"] = ep["calls"][:MAX_CALLS]
    notes = []
    if any(seedplan._items(e.data) is not None for e in exs if e.method == "GET"):
        notes.append("Each example lists the records the recorded answer listed, in the recorded "
                     "order, with synthetic values. An order that followed a masked field (a "
                     "title, a name) therefore does not hold in the example: order the rows by "
                     "the rule in api/catalogue.json `lists`, not by the example.")  # fmt: skip
    parent = _parent_of(route)
    if parent and table:
        notes.append(f"A row of `{table}` belongs to one `{parent}` record (the `{{...}}` of the "
                     f"path). `db/schema.sql` has no column for that link: keep it in a column or "
                     "table of the app's own and leave `db/schema.sql` unchanged (an assumption "
                     "to report).")  # fmt: skip
    if notes:
        doc["notes"] = notes
    if world.today:
        mine = sorted(
            k
            for k in world.today
            if any(k == key for d in [e.data for e in exs] for _, key, _ in _leaves(d))
        )
        if mine:
            doc["notes"] = doc.get("notes", []) + [f"`{k}` held the date of the day the call was made (hypothesis: the "
                            "server's current date); the examples show a synthetic date"
                            for k in mine]  # fmt: skip
    return doc


# --- lists: search, filter, sort, paging ---------------------------------------------------------


def list_facts(ctxs: list[Ctx], kept: Kept) -> list[dict]:
    """Per list endpoint, what its parameters did to the answers, checked on every recorded
    answer (no value is kept: only whether the rule held)."""
    from labctl import har

    groups: dict[str, list] = {}
    for c in ctxs:
        for e in c.part.exchanges:
            if (e.method == "GET" and 200 <= e.status < 300 and is_api(e)
                    and seedplan._items(e.data) is not None):  # fmt: skip
                groups.setdefault(e.route, []).append((c, e))
    out = []
    for route, calls in groups.items():
        fact: dict = {"endpoint": f"GET {route}", "answers": len(calls)}
        sizes = [len(har._items(e.data)) for _, e in calls]
        paged = [len(har._items(e.data)) for _, e in calls
                 if isinstance(e.data, dict) and (e.data.get("pages") or 0) > 1]  # fmt: skip
        if paged:
            fact["page_size"] = max(paged)
        fact["rows_per_answer"] = [min(sizes), max(sizes)]
        wrapper = sorted({k for _, e in calls if isinstance(e.data, dict) for k in e.data
                          if not isinstance(e.data[k], list)})  # fmt: skip
        if wrapper:
            fact["answer_fields_beside_the_rows"] = wrapper
        params: dict[str, dict] = {}
        for _, e in calls:
            for k in e.query:
                params.setdefault(
                    k,
                    {
                        "control": kept.control(
                            k, [sanitize.route_pattern(x.referer) for _, x in calls if x.referer]
                        )
                    },
                )
        for k, info in params.items():
            if not info["control"]:
                info.pop("control")
            vals = [e.query.get(k, "") for _, e in calls]
            if _key(k) in {_key(x) for x in seedplan.PAGE_PARAMS}:
                info["role"] = "paging"
                continue
            rules = []
            for value in sorted(set(vals)):
                mine = [(c, e) for c, e in calls if e.query.get(k, "") == value]
                if value == "":
                    continue
                rule = _param_rule(k, value, mine, kept)
                if rule:
                    rules.append(rule)
            typed = any(v in c.shapes for (c, _), v in zip(calls, vals, strict=True) if v)
            # An order seen with the one value this parameter ever had is the list's own order,
            # not shown to be this parameter's doing.
            orders = [r for r in rules if ": rows ordered by " in r]
            if orders and len(orders) == len(rules) and len({v for v in vals if v}) < 2:
                fact["order"] = orders[0].split(": ", 1)[1]
                rules = []
            info["role"] = ("search text a person typed" if typed else "filter or sort"
                            if rules else "a choice sent with the request (its effect is not "
                            "shown by the answers alone)")  # fmt: skip
            if "" in vals:
                info["empty"] = "sent empty when not used"
            if rules:
                info["observed"] = rules
        fact["parameters"] = params
        out.append(fact)
    return out


def _param_rule(name: str, value: str, calls: list, kept: Kept) -> str | None:
    """What one value of a parameter did, when every answer it was sent with agrees. A value
    that is not provably a code is never written: it is named by its option's label."""
    from labctl import har

    answers = [har._items(e.data) for _, e in calls]
    full = [a for a in answers if a]
    typed = any(value in c.shapes for c, _ in calls)
    code = kept.code(name, value)
    label = kept.labels.get((name, value)) or kept.labels.get((name, sanitize.mask(value)))
    if typed:
        shown = "the typed text"
    elif code:
        shown = f"`{value}`" + (f' ("{label}")' if label else "")
    elif label:
        shown = f'the option "{label}" (its code is not kept: not provably a code)'
    else:
        return None
    if not full:
        return f"{shown}: the answer listed no rows" if typed else None
    keys = list(full[0][0])
    if typed:
        for k in keys:
            vals = [str(i.get(k)) for a in full for i in a]
            if all(v == value for v in vals):
                return f"{shown}: every row's `{k}` equalled it"
            if all(re.search(rf"(^|\W){re.escape(value)}(\W|$)", v) for v in vals):
                return (
                    f"{shown}: every row's `{k}` held it as a word (hypothesis: contains, any case)"
                )
        return None
    for k in keys if code else []:
        if all(i.get(k) == value for a in full for i in a):
            return f"{shown}: every row had `{k}` = `{value}` ({len(full)} answer(s)): a filter"
    long = [a for a in full if len(a) >= 3]
    if not long:
        return None
    ups, downs = [], []
    for k in keys:
        cols = [[str(i.get(k) if i.get(k) is not None else "").lower() for i in a] for a in long]
        if all(len(set(col)) == 1 for col in cols):
            continue
        if all(col == sorted(col) for col in cols):
            ups.append(k)
        if all(col == sorted(col, reverse=True) for col in cols):
            downs.append(k)
    for found, way in ((ups, "lowest first"), (downs, "highest first")):
        if found:
            names = " and ".join(f"`{k}`" for k in found[:3])
            unsure = " (the answers do not tell which)" if len(found) > 1 else ""
            return f"{shown}: rows ordered by {names}{unsure}, {way} ({len(long)} answer(s))"
    return None


# --- the field map -------------------------------------------------------------------------------


def field_map(root: Path, app: str, endpoints: dict, kept: Kept, world: World) -> list[dict]:
    """Which table and column each API field and each screen field is, and how sure that is."""
    schema = Path(root) / "apps" / app / "db" / "schema.sql"
    columns: dict[str, list[str]] = {}
    if schema.is_file():
        from labctl.builder.package import _columns, _create_tables

        for name, body in _create_tables(schema.read_text(encoding="utf-8", errors="replace")):
            columns[name] = _columns(body)
    rows: dict[tuple, dict] = {}
    for doc in endpoints.values():
        table = doc.get("table")
        cols = {_key(c): c for c in columns.get((table or "").lower(), [])}
        ep = f"{doc['method']} {doc['path']}"

        def add(field_path: str, key: str, side: str, cols=cols, table=table, ep=ep, doc=doc):
            col = cols.get(_key(key))
            row = rows.setdefault((table, _key(key)), {
                "api_field": key, "table": table, "column": col, "in": [], "screen": []})  # fmt: skip
            where = f"{ep} {side} `{field_path}`"
            if where not in row["in"]:
                row["in"].append(where)
            for place in (doc.get("shown") or {}).get(field_path, []) if side == "answer" else []:
                if place not in row["screen"]:
                    row["screen"].append(place)
            control = kept.control(key, [t.get("screen") for t in doc["triggered_by"]])
            if side == "request" and control and control not in row["screen"]:
                row["screen"].append(control)

        if not table:
            continue
        for key in (doc["request"].get("schema") or {}).get("properties") or {}:
            add(key, key, "request")
        for status, r in doc["responses"].items():
            for path, key in _schema_leaves(r.get("schema") or {} if status < "4" else {}):
                wrapper = "." not in path and "[]" not in path
                if not (wrapper and _key(key) in {_key(x) for x in KEEP_NUMBERS}):
                    add(path, key, "answer")
    out = []
    for (table, _), row in rows.items():
        if row["column"]:
            row["basis"] = ("observed" if any(" answer " in w for w in row["in"])
                            else "hypothesis: the same name as a field the form sends")  # fmt: skip
        else:
            row["basis"] = (f"hypothesis: no column of `{table}` has this name; keep it with the "
                            "record in a table of the app's own")  # fmt: skip
        row["in"] = row["in"][:6]
        out.append(row)
    return out


def _schema_leaves(schema: dict, path: str = ""):
    if schema.get("type") == "object":
        for k, v in (schema.get("properties") or {}).items():
            yield from _schema_leaves(v, f"{path}.{k}" if path else k)
    elif schema.get("type") == "array":
        if "items" in schema:
            yield from _schema_leaves(schema["items"], f"{path}[]")
    elif path:
        yield path, re.split(r"[.\[]", path.replace("[]", ""))[-1]


# --- privacy -------------------------------------------------------------------------------------


def observed_values(ctxs: list[Ctx], kept: Kept) -> set[str]:
    """Every data value the HAR holds in a call: what must never be in a carried API file.

    Left out, because they are kept on purpose: code values, identifiers the application
    generated, the application's error messages, shapes of typed values (already masked), and
    strings too short to tell from chance."""
    id_patterns = {(i["prefix"], i["sep"], i["width"]) for c in ctxs
                   for i in [*c.part.facts.ids, *c.part.facts.generated] if i.get("width")}  # fmt: skip
    from labctl import publicdata

    out: set[str] = set()
    pub = publicdata.current()
    here: list = [None]  # the exchange being read

    def take(key: str, v, c: Ctx) -> None:
        if isinstance(v, bool) or v is None:
            return
        e = here[0]
        if pub is not None and e is not None and pub.field_public(e.method, e.route, key, v):
            return  # declared public: carried on purpose
        if isinstance(v, (int, float)):
            if _key(key) in {_key(x) for x in KEEP_NUMBERS} or stepfacts.ID_FIELD.search(key):
                return
            text = repr(v) if isinstance(v, float) else str(v)
            if len(re.sub(r"\D", "", text)) >= 5:
                out.add(text)
            return
        text = str(v).strip()
        if len(text) < MIN_LEAK or text in c.shapes or kept.code(key, text):
            return
        if seedplan.MASK.match(text) and set(text) <= set("A9Xx .,:;@/+()#_-"):
            return
        m = stepfacts.ID_VALUE.match(text)
        if m and (m.group(1), m.group(2), len(m.group(3))) in id_patterns:
            return
        out.add(text)

    for c in ctxs:
        for e in c.part.exchanges:
            if not is_api(e):
                continue
            here[0] = e
            for k, v in e.query.items():
                take(k, v, c)
            for k, v in e.form.items() if e.body is None else []:
                take(k, v, c)
            for path, k, v in _leaves(e.body):
                if path in _protocol_keys(e.body):  # a GraphQL document, a JSON-RPC method: code
                    continue
                take(k, v, c)
            if e.status < 400:
                for _, k, v in _leaves(e.data):
                    take(k, v, c)
            for seg, pat in zip(e.path.split("/"), e.route.split("/"), strict=False):
                if pat.startswith("{"):
                    take("id", seg, c)
    return out


def _protocol_keys(body) -> set[str]:
    """Paths of a request body that are the protocol's, not data: the document and operation
    name of a GraphQL request, the method of a JSON-RPC call."""
    if isinstance(body, dict) and isinstance(body.get("query"), str) and re.match(
            r"\s*(query|mutation|subscription|fragment|\{)", body["query"]):  # fmt: skip
        return {"query", "operationName"}
    if isinstance(body, dict) and "jsonrpc" in body and isinstance(body.get("method"), str):
        return {"method", "jsonrpc"}
    return set()


def _seed_dates(root: Path, app: str) -> set[str]:
    """Dates of the generated seed. The seed spreads its dates over the months the recording
    showed, so one can fall on a recorded day; a date alone, in another record, is not that
    record's value."""
    path = Path(root) / "apps" / app / "db" / seedplan.SEED_JSON
    try:
        doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        return set()
    return {v for _, _, v in _leaves(doc) if isinstance(v, str)
            and (seedplan.ISO_DATE.match(v) or seedplan.ISO_TIME.match(v))}  # fmt: skip


def leaks(root: Path, app: str, ctxs: list[Ctx] | None = None,
          texts: dict[str, str] | None = None) -> list[str]:  # fmt: skip
    """Observed data values found in the carried API files ("file: value shape"), [] when clean.

    The finding names the file and the value's SHAPE, never the value."""
    from labctl.behaviour import dom as domlib

    root = Path(root)
    ctxs = load(root, app) if ctxs is None else ctxs
    kept = kept_values(root, app, ctxs)
    values = observed_values(ctxs, kept) - _seed_dates(root, app)
    if texts is None:
        folder = root / "apps" / app / DIR
        texts = {p.name: p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.json"))
                 } if folder.is_dir() else {}  # fmt: skip
    found = []
    for name, text in texts.items():
        try:
            strings = {str(v) for path, _, v in _leaves(json.loads(text))
                       if isinstance(v, (str, int, float)) and not isinstance(v, bool)
                       and not path.endswith("required[]")}  # a schema's field names  # fmt: skip
        except ValueError:
            strings = {text}
        for v in sorted(values):
            if v in strings or (len(v) >= 8 and any(v in s for s in strings)):
                found.append(f"{name}: an observed value of shape `{domlib.shape(v)}`")
    return found


# --- writing -------------------------------------------------------------------------------------


def write(root: Path, app: str) -> dict:
    """Write `apps/<app>/api/`. Returns {"files": [...], "index": doc, "problems": [...]}; an
    application whose pages called nothing gets no folder."""
    root = Path(root)
    folder = root / "apps" / app / DIR
    if folder.is_dir():
        for old in folder.glob("*.json"):
            old.unlink()
    ctxs = load(root, app)
    built = build(root, app, ctxs)
    from labctl import netfacts  # headers, caching, timing, streams, third parties

    net_problems = netfacts.extend(root, app, ctxs, built)
    if not built["endpoints"]:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
        return {"files": [], "index": None, "problems": [], "ctxs": ctxs}
    texts = {INDEX: _dump(built["index"])}
    for name, doc in built["endpoints"].items():
        texts[name] = _dump(doc)
    if built.get("network"):
        texts[netfacts.FILE] = _dump(built["network"])
    problems = list(built["problems"]) + net_problems
    bad = leaks(root, app, ctxs, texts)
    if bad:  # never leave a file that holds an observed value
        for name in {b.split(":", 1)[0] for b in bad}:
            texts.pop(name, None)
        problems += [f"NOT WRITTEN, privacy check failed: {b}" for b in bad]
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for name, text in texts.items():
        (folder / name).write_bytes(text.encode("utf-8"))
        files.append(folder / name)
    return {"files": files, "index": built["index"] if INDEX in texts else None,
            "endpoints": built["endpoints"], "problems": problems, "ctxs": ctxs,
            "network": built.get("network") if netfacts.FILE in texts else None}  # fmt: skip


def _dump(doc) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False).replace("\r\n", "\n") + "\n"


# --- the brief -----------------------------------------------------------------------------------


def brief_section(app: str, done: dict) -> str:
    index = done.get("index")
    if not index:
        return ""
    eps = done.get("endpoints") or {}
    out = ["## API contract\n",
           f"The pages called {len(index['endpoints'])} endpoint(s). Each has a file under "
           f"`apps/{app}/api/` (index: `apps/{app}/api/catalogue.json`) with its parameters, the "
           "schema of the request and of each answer by status, and every recorded call in order "
           "with synthetic values. Answer every endpoint on the same method and path, with the "
           "same keys, types, codes and statuses. The examples are one consistent set of records: "
           "the rows of `db/seed.json`, the typed values of `steps/*.values.yaml`, and the "
           "records the calls made. No example value is a recorded one.\n",
           "| Endpoint | Sent by | Request | Answers | Authorised by | File |",
           "|---|---|---|---|---|---|"]  # fmt: skip
    for row in index["endpoints"]:
        doc = eps.get(Path(row["file"]).name, {})
        req = ", ".join(f"`{f}`" for f in row["request_fields"]) or "-"
        if row["query"]:
            req = ("query " + ", ".join(f"`{q}`" for q in row["query"])
                   + ("; body " + req if req != "-" else ""))  # fmt: skip
        answers = []
        for status, r in (doc.get("responses") or {}).items():
            props = (r.get("schema") or {}).get("properties") or {}
            what = ", ".join(f"`{k}`" for k in list(props)[:8]) or (
                f"redirect to `{r['location']}`"
                if r.get("location")
                else "body not recorded"
                if str(r.get("body", "")).startswith("not rec")
                else "the page again (HTML)"
                if str(r.get("body", "")).startswith("an HTML")
                else "no body"
            )
            answers.append(f"`{status}`: {what}")
        sent = "; ".join(row["triggered_by"][:10]) or "-"
        out.append(f"| `{row['method']} {row['path']}` | {sent} | {req} | {'<br>'.join(answers)} | "
                   f"{row['auth']} | `{row['file']}` |")  # fmt: skip
    out.append("")
    nested = [(d["method"], d["path"], n) for d in eps.values() for n in d.get("notes") or []
              if "belongs to one" in n]  # fmt: skip
    if nested:
        out.append("**Records inside a record.**\n")
        out += [f"- `{m} {path}`: {n}" for m, path, n in nested]
        out.append("")
    lists = [x for x in index.get("lists") or [] if x.get("parameters")]
    if lists:
        out.append("**Search, filter, sort and paging.** Checked on every recorded answer:\n")
        for x in lists:
            size = f", {x['page_size']} row(s) per page" if x.get("page_size") else ""
            beside = x.get("answer_fields_beside_the_rows")
            out.append(f"- `{x['endpoint']}` ({x['answers']} answer(s){size}"
                       + (f"; beside the rows it answers {', '.join(f'`{b}`' for b in beside)}"
                          if beside else "") + "):")  # fmt: skip
            if x.get("order"):
                out.append(f"  - every answer: {x['order']}.")
            for name, info in x["parameters"].items():
                line = f"  - `{name}`"
                if info.get("control"):
                    line += f" (set by `{info['control']}`)"
                line += f": {info.get('role')}"
                if info.get("empty"):
                    line += f"; {info['empty']}"
                out.append(line + ("." if not info.get("observed") else ":"))
                out += [f"    - {r}" for r in info.get("observed") or []]
        out.append("")
    fmap = [r for r in index.get("field_map") or [] if r.get("table")]
    if fmap:
        out += ["**Where each field lives.** API field, the screen element that shows or sets it, "
                "and its column:\n",
                "| Table.column | API field | On the screen | Basis |", "|---|---|---|---|"]  # fmt: skip
        for r in fmap:
            col = f"`{r['table']}.{r['column']}`" if r["column"] else f"(`{r['table']}`: no column)"
            screen = ", ".join(f"`{s}`" for s in r["screen"][:4]) or "-"
            out.append(f"| {col} | `{r['api_field']}` | {screen} | {r['basis']} |")
        out.append("")
    if done.get("problems"):
        out += ["Problems found while writing the catalogue:\n",
                *[f"- {p}" for p in done["problems"]], ""]  # fmt: skip
    return "\n".join(out) + "\n"
