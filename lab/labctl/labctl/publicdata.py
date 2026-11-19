"""Public catalogue data, declared by the owner (`public-data.yaml` beside the recording).

Off by default: office applications show customer data, and every value a recording holds is
masked or replaced. An application whose shown content is a public catalogue (product names,
descriptions, prices, category codes, product photos) may have it carried as recorded, when its
owner declares it and a person approves the declaration:

```yaml
version: 1
recording: trial-1
approved_by: J. Owner                  # who decided this data is public (required)
approved_on: 2026-11-19
reason: the product catalogue is published on the open web site
public:
  endpoints:                           # answers of these calls, every field or the ones named
    - {method: GET, path: /entries}
    - {method: POST, path: /view, fields: [title, price, desc, img, cat]}
  regions: ["#more-information"]       # page elements whose text is public (CSS: tag#id.class)
  images: [/imgs/]                     # image path prefixes: product photos, carried as images
```

Nothing is used without `approved_by`. `labctl evidence public-data <recording>` drafts the
file unapproved from the recording: every endpoint and its fields under `seen:`, nothing public.

What a usable declaration changes: a declared answer field keeps its recorded value in the API
examples and the seed rows; a page text that is, as a whole, a declared value (or a number one
is: `$360`), or that sits in a declared region, keeps its text in the page snapshots, outside data
cells (`td`, `dd`: a cart's rows stay masked); declared image prefixes are carried as interface
images; the brief and `BUILD_TASK.md` say which data is real and who approved it. Everything not
declared stays masked. The secret checks still apply inside declared data: a value that looks
like an e-mail address, a token, a key, a session id or a UUID, a cookie (`name=value`), anything
in a field named like a credential, and anything a person typed (its shape) is never carried.
"""

from __future__ import annotations

import contextlib
import contextvars
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from labctl import sanitize

FILE = "public-data.yaml"
SECRET_KEY = re.compile(
    r"token|secret|passw|pwd|api[-_]?key|session|cookie|auth|credential|signature|csrf|jwt|"
    r"email|mail|phone|iban|card|ssn|user",
    re.IGNORECASE,
)
TOKEN = re.compile(r"^(?=.*\d)(?=.*[A-Za-z])[A-Za-z0-9+/=_.-]{20,}$")  # one long word: a token
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
JWT = re.compile(r"eyJ[\w-]{5,}\.[\w-]{5,}")


@dataclass
class Endpoint:
    method: str
    path: str
    fields: list[str] | None = None  # None: every field


@dataclass
class Declaration:
    approved_by: str | None = None
    approved_on: str | None = None
    reason: str = ""
    endpoints: list[Endpoint] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    path: Path | None = None
    problems: list[str] = field(default_factory=list)
    # what the recordings hold under the declared names (set by `Public.collect`)

    @property
    def usable(self) -> bool:
        return bool(self.approved_by) and bool(self.endpoints or self.regions or self.images)


def load(path: Path) -> Declaration | None:
    """The declaration in a file, or None when there is none. A file without `approved_by` is
    read (and says so in `problems`) but is not usable."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return Declaration(path=path, problems=[f"{path.name}: not YAML ({exc})"])
    if not isinstance(data, dict):
        return Declaration(path=path, problems=[f"{path.name}: not a mapping"])
    pub = data.get("public") or {}
    out = Declaration(approved_by=_str(data.get("approved_by")),
                      approved_on=_str(data.get("approved_on")), reason=_str(data.get("reason")) or "",
                      path=path)  # fmt: skip
    for item in pub.get("endpoints") or []:
        if isinstance(item, str) and " " in item:
            method, _, route = item.partition(" ")
            item = {"method": method, "path": route}
        if not isinstance(item, dict) or not item.get("method") or not item.get("path"):
            out.problems.append(f"{path.name}: an endpoint needs `method` and `path`: {item!r}")
            continue
        fields = item.get("fields")
        out.endpoints.append(Endpoint(str(item["method"]).upper(),
                                      sanitize.route_pattern(str(item["path"])),
                                      [str(f) for f in fields] if fields else None))  # fmt: skip
    out.regions = [str(r) for r in pub.get("regions") or [] if str(r).strip()]
    out.images = [
        "/" + str(p).strip().lstrip("/") for p in pub.get("images") or [] if str(p).strip()
    ]
    if not out.approved_by:
        out.problems.append(f"{path.name}: no `approved_by`: not used")
    if not out.reason and out.approved_by:
        out.problems.append(f"{path.name}: no `reason` given")
    return out


def _str(v) -> str | None:
    if v is None:
        return None
    text = str(v).strip()
    return text or None


def refused(key: str, value) -> bool:
    """Whether a value stays masked although it is declared public: a field named like a
    credential or personal contact, a value shaped like an e-mail address, a token, a key, a
    JWT, a UUID or a cookie, or the recorder's shape of a typed value."""
    if SECRET_KEY.search(str(key)):
        return True
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return False
    text = str(value)
    if sanitize.EMAIL.search(text) or JWT.search(text) or UUID.search(text):
        return True
    if TOKEN.match(text.strip()) and not sanitize.STATIC_FILE.search(text.strip()):
        return True
    if re.match(r"^[A-Za-z0-9_.-]{1,64}=[^\s]+$", text.strip()):  # name=value: a cookie
        return True
    return bool(re.fullmatch(r"[A9][A9 .,:;@/+()#_-]*", text.strip()))  # a typed value's shape


# --- what a usable declaration covers -----------------------------------------------------------


@dataclass
class Public:
    """The usable declarations of one application, with the values they cover."""

    decls: list[Declaration] = field(default_factory=list)
    phrases: set[str] = field(default_factory=set)  # sanitize.phrase() of declared text values
    numbers: set[str] = field(default_factory=set)  # declared numbers, as written plainly
    shapes: set[str] = field(default_factory=set)  # typed shapes of the recordings
    # words declared values write with a capital (`Mac` of `MacBook air`), as the sanitiser
    # reads names (`sanitize.names_in`): they are no undeclared name where a declared text shows
    capitals: set[str] = field(default_factory=set)

    def endpoint(self, method: str, route: str) -> Endpoint | None:
        for d in self.decls:
            for e in d.endpoints:
                if e.method == method.upper() and e.path == route:
                    return e
        return None

    def field_public(self, method: str, route: str, key: str, value) -> bool:
        """A field of a declared answer, carried as recorded (the secret checks applied)."""
        e = self.endpoint(method, route)
        if e is None or (e.fields is not None and str(key) not in e.fields):
            return False
        return not refused(key, value) and str(value) not in self.shapes

    @property
    def regions(self) -> list[str]:
        return [r for d in self.decls for r in d.regions]

    @property
    def images(self) -> list[str]:
        return [p for d in self.decls for p in d.images]

    def region(self, tag: str, attrs: dict) -> bool:
        """Whether an element is one of the declared regions (`tag`, `#id`, `.class`, combined:
        every part given must match)."""
        classes = set((attrs.get("class") or "").split())
        for sel in self.regions:
            m = re.fullmatch(r"([A-Za-z][\w-]*)?((?:[#.][\w-]+)*)", sel.strip())
            if not m or not (m.group(1) or m.group(2)):
                continue
            if m.group(1) and m.group(1).lower() != tag:
                continue
            parts = re.findall(r"([#.])([\w-]+)", m.group(2))
            if all((attrs.get("id") == v) if k == "#" else (v in classes) for k, v in parts):
                return True
        return False

    def image(self, path: str) -> bool:
        return any(path.startswith(p) for p in self.images)

    def text(self, data: str) -> bool:
        """A page text carried as recorded: a declared value as a whole, or a number one is."""
        if refused("", data):
            return False
        p = sanitize.phrase(data)
        if p and p in self.phrases:
            return True
        num = re.sub(r"[$€£,\s]|\*?includes tax", "", data.strip().lower())
        return bool(num) and _plain_number(num) in self.numbers

    def words(self) -> set[str]:
        """Every word of the declared values (lower-case): what the look's privacy check lets
        through."""
        out = set().union(*(sanitize.words(p) for p in self.phrases)) if self.phrases else set()
        return out | self.capitals

    def tokens(self) -> set[str]:
        """Every token (between spaces, punctuation around it dropped) of the declared values,
        lower-case, and the declared numbers."""
        out = set(self.numbers)
        for p in self.phrases:
            out.update(t.strip(".,;:!?()[]{}\"'<>*/") for t in p.split())
        out.discard("")
        return out

    def collect(self, recordings: list[Path]) -> None:
        """Read the values the declared fields hold in the recordings (in memory only)."""
        from labctl import har

        for path in recordings:
            try:
                rec = har.load(path)
            except (OSError, ValueError):
                continue
            self.shapes |= {
                str(s.typed_shape) for s in rec.steps if getattr(s, "typed_shape", None)
            }
            for e in har.exchanges(rec.har):
                if e.resp_json is None or not (200 <= e.status < 300):
                    continue
                route = sanitize.route_pattern(e.path)
                if self.endpoint(e.method, route) is None:
                    continue
                for item in har._items(e.resp_json):
                    for k, v in item.items():
                        if isinstance(v, (dict, list)) or not self.field_public(
                            e.method, route, k, v
                        ):
                            continue
                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                            self.numbers.add(_plain_number(str(v)))
                        elif isinstance(v, str) and len(v) <= 2000:
                            self.phrases.add(sanitize.phrase(v))
                            self.capitals |= sanitize.names_in(v)

    def note(self) -> str:
        """Who approved what, for the brief and the task."""
        if not self.decls:
            return ""
        who = "; ".join(f"{d.approved_by}" + (f" on {d.approved_on}" if d.approved_on else "")
                        + (f" ({d.reason})" if d.reason else "") for d in self.decls)  # fmt: skip
        eps = sorted({f"`{e.method} {e.path}`" + (f" ({', '.join(e.fields)})" if e.fields else "")
                      for d in self.decls for e in d.endpoints})  # fmt: skip
        out = [
            f"**Real public catalogue data** (declared public, approved by {who}). These values "
            "are carried as recorded: the answers of " + (", ".join(eps) or "no endpoint")
        ]
        if self.regions:
            out[0] += "; the text of " + ", ".join(f"`{r}`" for r in self.regions)
        if self.images:
            out[0] += "; the images under " + ", ".join(f"`{p}`" for p in self.images)
        out[0] += (
            ". They show in the page snapshots, the API examples, `db/seed.json` / "
            "`db/seed.sql` and `look/`. Everything else is synthetic or masked as usual "
            "(other answers, data cells such as a cart's rows, cookies, typed values)."
        )
        return out[0] + "\n"


def _plain_number(text: str) -> str:
    try:
        f = float(text)
    except ValueError:
        return ""
    return str(int(f)) if f.is_integer() else repr(f)


_CURRENT: contextvars.ContextVar[Public | None] = contextvars.ContextVar(
    "public_data", default=None
)


def current() -> Public | None:
    """The declaration in force (`active`), or None: then nothing is public."""
    return _CURRENT.get()


def declarations(recordings: list[Path]) -> list[Declaration]:
    found = []
    for rec in recordings:
        d = load(Path(rec) / FILE)
        if d is not None:
            found.append(d)
    return found


@contextlib.contextmanager
def active(recordings: list[Path]):
    """Run with the usable declarations of these recordings in force (none: nothing changes)."""
    usable = [d for d in declarations(recordings) if d.usable]
    if not usable:
        yield None
        return
    pub = Public(usable)
    pub.collect(recordings)
    token = _CURRENT.set(pub)
    try:
        yield pub
    finally:
        _CURRENT.reset(token)


@contextlib.contextmanager
def active_for(root: Path, app_id: str):
    """`active` for an application's recordings (`factory.discover`)."""
    from labctl import factory

    try:
        recs = factory.discover(Path(root), app_id).recordings
    except Exception:  # noqa: BLE001 - no evidence folder: nothing declared
        recs = []
    with active(recs) as pub:
        yield pub


# --- the draft ----------------------------------------------------------------------------------


def draft(recording: Path) -> str:
    """An unapproved declaration listing what the recording holds: every API endpoint with its
    answer fields, and the folders of its images. Nothing is marked public."""
    from labctl import har, lookfeel

    rec = har.load(Path(recording))
    seen: dict[tuple[str, str], list[str]] = {}
    for e in har.exchanges(rec.har):
        if e.resp_json is None and e.kind != "api":
            continue
        route = sanitize.route_pattern(e.path)
        cols = seen.setdefault((e.method, route), [])
        for item in har._items(e.resp_json) if e.resp_json is not None else []:
            cols += [str(k) for k in item if str(k) not in cols]
    folders = sorted({r.path.rsplit("/", 1)[0] + "/" for r in lookfeel.resources(rec.har)
                      if r.kind == "image" and "/" in r.path.strip("/")})  # fmt: skip
    doc = {
        "version": 1,
        "recording": Path(recording).name,
        "approved_by": None,
        "approved_on": None,
        "reason": None,
        "public": {"endpoints": [], "regions": [], "images": []},
        "seen": {
            "endpoints": [
                {"method": m, "path": p, "fields": f} for (m, p), f in sorted(seen.items())
            ],
            "image_folders": folders,
        },
    }
    head = ("# Public catalogue data: DRAFT, not approved, nothing is public yet.\n"
            "# Move what the owner declares public from `seen:` into `public:` (endpoints with\n"
            "# their fields, page regions, image folders), then fill approved_by, approved_on and\n"
            "# reason. Without approved_by nothing is used. Secrets, cookies, e-mail addresses\n"
            "# and typed values are never carried, declared or not.\n")  # fmt: skip
    return head + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)
