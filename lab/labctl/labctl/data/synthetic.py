"""Synthetic rows from a schema: reproducible from a seed, standard library only.

No real personal data, ever: e-mail addresses end in example.test, telephone numbers use the
fictional 555-01xx range, identifiers such as account or card numbers start with TEST. Short code
lists (a status column with OPEN, RESOLVED, ...) are taken from `samples` when given, so the app's
own codes survive.

With an observed profile (`labctl.data.observed`, built from what a recording showed) generated rows
follow it: code columns draw from the observed vocabulary with the observed shares, identifiers
match observed patterns, numbers and dates stay inside observed ranges, and "anchor" rows exist for
the records a recorded flow navigates to. Only the profile's reviewed content is used; without a
profile nothing here changes.
"""

from __future__ import annotations

import base64
import random
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from labctl.data import Rows
from labctl.data import observed as obs
from labctl.source.sqlalchemy_models import Column, Table, ordered

__all__ = ["generate", "CODE_LIST_MAX"]

CODE_LIST_MAX = 20  # at most this many distinct sample values make a code list
NULL_SHARE = 0.1  # share of empty values in optional columns, when samples say nothing
BASE = datetime(2026, 1, 1, tzinfo=UTC)  # generated moments fall in the two years before
ATTEMPTS = 200

FIRST = ["Alex", "Blake", "Casey", "Dana", "Eli", "Frankie", "Gray", "Harper", "Indy", "Jordan",
         "Kai", "Lee", "Morgan", "Noel", "Oakley", "Parker", "Quinn", "Reese", "Sky", "Taylor",
         "Uma", "Val", "Wren", "Yael", "Zion", "Avery", "Brook", "Cameron", "Drew", "Emery"]  # fmt: skip
LAST = ["Ashdown", "Birchwood", "Calder", "Dunmore", "Elmsworth", "Fairbank", "Glenholm",
        "Hollins", "Ivers", "Juniper", "Kestrel", "Larkspur", "Merriweather", "Northcott",
        "Oakridge", "Pembrook", "Quarry", "Redfern", "Stonebridge", "Thornbury", "Underhill",
        "Vale", "Westbrook", "Yarrow", "Zephyr", "Ambler", "Brightwater", "Coldwell"]  # fmt: skip
CITIES = ["Springfield", "Riverton", "Lakeside", "Fairview", "Mapleton", "Brookfield",
          "Hillcrest", "Oakdale", "Greenville", "Westport", "Northfield", "Clearwater",
          "Ashford", "Pinehurst", "Rockport", "Elmwood"]  # fmt: skip
STREETS = ["Main", "Oak", "Maple", "Cedar", "Elm", "Pine", "Lake", "Hill", "River", "Park"]
COUNTRIES = ["Exampleland", "Testonia", "Samplestan", "Demoria"]
REGIONS = ["North", "South", "East", "West", "Central"]
SUBJECTS = ["Question about", "Problem with", "Request for", "Update on", "Change to",
            "Complaint about", "Follow-up on"]  # fmt: skip
TOPICS = ["delivery", "invoice", "account access", "billing address", "order status",
          "contract renewal", "refund", "service outage", "password reset", "plan upgrade"]  # fmt: skip
SENTENCES = ["Customer called about the {t}.", "Details checked against the {t}.",
             "Waiting for confirmation of the {t}.", "Escalated to the second line for the {t}.",
             "Agreed next steps on the {t}.", "Sent a written summary of the {t}.",
             "No further action needed on the {t}."]  # fmt: skip
FALLBACKS = {
    "status": ["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"],
    "state": ["ACTIVE", "INACTIVE"],
    "priority": ["LOW", "MEDIUM", "HIGH"],
    "severity": ["LOW", "MEDIUM", "HIGH"],
    "type": ["STANDARD", "SPECIAL"],
    "kind": ["STANDARD", "SPECIAL"],
    "event": ["CREATED", "UPDATED", "STATUS_CHANGED", "NOTE_ADDED"],
    "action": ["CREATED", "UPDATED", "DELETED"],
    "currency": ["USD", "EUR", "GBP"],
    "language": ["en", "de", "fr"],
    "locale": ["en-US", "de-DE", "fr-FR"],
    "colour": ["red", "green", "blue"],
    "color": ["red", "green", "blue"],
    "gender": ["X"],
    "role": ["USER", "ADMIN"],
    "channel": ["PHONE", "EMAIL", "WEB"],
}
PEOPLE_TABLES = ("user", "customer", "person", "people", "employee", "contact", "client",
                 "member", "staff", "agent", "patient", "student", "owner")  # fmt: skip
STAFF_WORDS = ("author", "actor", "created_by", "updated_by", "modified_by", "changed_by",
               "owner", "assignee", "assigned_to", "agent", "operator", "handler", "reporter",
               "approver", "requested_by")  # fmt: skip
LATER_WORDS = ("updated", "modified", "changed", "closed", "resolved", "ended", "finished",
               "completed", "last_", "_end", "end_")  # fmt: skip
SECRET_WORDS = ("iban", "card", "ssn", "social", "passport", "tax_id", "taxid", "vat",
                "national", "licence", "license", "bank", "routing", "swift", "bic")  # fmt: skip
INT_RANGE = {"SMALLINT": 2**15 - 1, "INTEGER": 2**31 - 1, "BIGINT": 2**63 - 1}


def generate(
    tables: list[Table], seed: int, counts: dict[str, int], samples: Rows | None = None,
    profile: obs.Profile | dict | None = None, report: dict | None = None,
) -> Rows:  # fmt: skip
    """`counts[table]` rows for each table named, parents before children.

    `profile` (an `observed.Profile` or its dict) shapes the values and adds anchor rows (their
    tables are added to `counts` when missing, and counts raised to hold them). `report`, when
    given, receives `anchors`: where each anchor went and the identifier it got.

    Foreign keys point at generated parents, or at `samples` rows for parents not generated.
    Columns the database numbers itself are left out unless some generated row refers to them.
    Raises ValueError with a plain explanation when the request cannot be met.
    """
    samples = samples or {}
    if profile is not None and not isinstance(profile, obs.Profile):
        profile = obs.Profile(profile)
    counts = dict(counts)
    if profile is not None:
        for t in tables:
            anchors = len(profile.anchors(t.name))
            if anchors and counts.get(t.name, -1) != 0:
                counts[t.name] = max(counts.get(t.name, 0), anchors)
    if report is not None:
        report.setdefault("anchors", [])
    by_name = {t.name: t for t in tables}
    unknown = sorted(set(counts) - set(by_name))
    if unknown:
        raise ValueError(f"no table named {', '.join(unknown)} in the schema")
    for name, n in sorted(counts.items()):
        if not isinstance(n, int) or n < 0:
            raise ValueError(f"count for {name} must be a whole number, 0 or more (got {n!r})")
    plan = [t for t in ordered(tables) if counts.get(t.name, 0) > 0]
    planned = {t.name for t in plan}
    needed = {
        c.references for t in plan for c in t.columns if c.references and c.references[0] in planned
    }
    out: Rows = {}
    for table in plan:
        out[table.name] = _TableGen(table, seed, counts[table.name], samples, out, needed,
                                    by_name, profile, report).rows()  # fmt: skip
    return out


class _TableGen:
    def __init__(self, table: Table, seed: int, n: int, samples: Rows, done: Rows,
                 needed: set, by_name: dict[str, Table], profile: obs.Profile | None = None,
                 report: dict | None = None):  # fmt: skip
        self.profile, self.report = profile, report
        self.table, self.seed, self.n = table, seed, n
        self.samples, self.done, self.needed, self.by_name = samples, done, needed, by_name
        self.rngs: dict[str, random.Random] = {}
        self.used: dict[str, set] = {c.name: set() for c in table.columns if _unique(table, c)}
        self.staff = self._staff()
        self.anchors = self._anchor_plan()
        self.rows_out: list[dict[str, Any]] = []

    def rng(self, key: str) -> random.Random:
        if key not in self.rngs:
            self.rngs[key] = random.Random(f"{self.seed}:{self.table.name}:{key}")
        return self.rngs[key]

    def _staff(self) -> list[str]:
        r = random.Random(f"{self.seed}:staff")
        return [f"{r.choice(FIRST)[0]}{r.choice(LAST)}".lower() for _ in range(6)]

    # -- observed profile --

    def _info(self, col: Column) -> dict[str, Any] | None:
        return self.profile.column(self.table.name, col.name) if self.profile else None

    def _anchor_plan(self) -> list[dict[str, Any]]:
        """Anchor rows for this table: key column, identifier (kept or a stand-in) and state."""
        if self.profile is None:
            return []
        plan = []
        cols = {obs.key(c.name): c for c in self.table.columns}
        pk = next((c for c in self.table.columns if c.primary_key), None)
        for k, a in enumerate(self.profile.anchors(self.table.name)):
            spec = a.get("key") or {}
            col = cols.get(obs.key(spec.get("column", ""))) or pk
            if col is None or col.references:
                continue
            numeric = col.type in INT_RANGE or col.type.startswith("NUMERIC")
            limit = _length(col.type)
            kept = spec.get("id") if spec.get("verbatim") and not numeric else None
            if kept is not None and limit is not None and len(kept) > limit:
                kept = None
            if kept is not None:
                ident: Any = kept
            elif spec.get("stand_in") is not None:
                ident = spec["stand_in"]
            elif numeric:
                ident = k + 1
            else:
                width = max(3, str(spec.get("shape", "")).count("#"))
                ident = f"TEST{k + 1:0{width}d}"
                if limit is not None and len(ident) > limit:
                    ident = ident[-limit:]
            plan.append({"column": col, "id": ident, "kept": kept is not None,
                         "state": a.get("state") or {}, "shape": spec.get("shape")})  # fmt: skip
        return plan

    def _anchor_rows(self) -> None:
        if self.report is not None:
            for i, a in enumerate(self.anchors):
                self.report["anchors"].append({
                    "table": self.table.name, "column": a["column"].name, "row": i + 1,
                    "identifier": a["id"], "identifier_kept_from_recording": a["kept"],
                    "stand_in_for_shape": None if a["kept"] else a["shape"],
                    "starting_state": dict(a["state"]),
                })  # fmt: skip

    def _profile_choice(self, col: Column, info: dict, rng: random.Random) -> str | None:
        limit = _length(col.type)
        values = [v for v in (info.get("values") or {}) if limit is None or len(v) <= limit]
        if not values:
            return None
        weights = [float(info["values"][v]) or 0.01 for v in values]
        return rng.choices(values, weights=weights, k=1)[0]

    def _profile_value(self, col: Column, i: int, attempt: int, rng: random.Random) -> Any:
        """A value that follows the profile's shape for this column, or None to fall through."""
        info = self._info(col)
        if not info:
            return None
        kind, ctype = info.get("kind"), col.type
        if kind == "identifier" and info.get("pattern") and _is_text(ctype):
            unique = col.name in self.used
            made = obs.make_from_pattern(info["pattern"], i + 1 + 1000 * (attempt > 0) + attempt,
                                         None if unique and attempt == 0 else rng)  # fmt: skip
            return _fit(made, _length(ctype), 0, rng, i)
        lo_hi = info.get("range")
        if kind == "number" and lo_hi and col.name not in self.used:
            lo, hi = lo_hi
            if ctype in INT_RANGE:
                return rng.randint(int(lo), max(int(lo), int(hi)))
            if ctype.startswith("NUMERIC") or ctype == "DOUBLE PRECISION":
                scale = int(info.get("scale", 2))
                m = re.fullmatch(r"NUMERIC\((\d+),(\d+)\)", ctype)
                if m:
                    scale = int(m.group(2))
                    hi = min(hi, 10 ** (int(m.group(1)) - scale) - 1)
                    lo = min(lo, hi)
                value = round(rng.uniform(lo, hi), scale)
                return format(Decimal(str(value)), "f") if ctype.startswith("NUMERIC") else value
        if kind in ("date", "datetime") and lo_hi and ctype in ("DATE", "TIMESTAMP",
                                                                  "TIMESTAMP WITH TIME ZONE"):
            first = date.fromisoformat(lo_hi[0] + "-01")
            y, m = int(lo_hi[1][:4]), int(lo_hi[1][5:7])
            last = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
            day = first + timedelta(days=rng.randint(0, max(0, (last - first).days)))
            if ctype == "DATE":
                return day.isoformat()
            moment = datetime(day.year, day.month, day.day, rng.randint(6, 20), rng.randint(0, 59),
                              tzinfo=UTC)  # fmt: skip
            return moment.replace(tzinfo=None).isoformat() if ctype == "TIMESTAMP" \
                else moment.isoformat()  # fmt: skip
        return None

    # -- rows --

    def rows(self) -> list[dict[str, Any]]:
        table = self.table
        pk = [c for c in table.columns if c.primary_key]
        seen_pk: set[tuple] = set()
        for a in self.anchors:  # no generated row may take an anchor's identifier
            if a["column"].name in self.used:
                self.used[a["column"].name].add(a["id"])
        self._anchor_rows()
        for i in range(self.n):
            for attempt in range(ATTEMPTS):
                row = self.row(i, attempt)
                if len(pk) < 2:
                    break
                key = tuple(row.get(c.name) for c in pk)
                if key not in seen_pk:
                    seen_pk.add(key)
                    break
            else:
                raise ValueError(f"cannot make {self.n} different primary keys for {table.name} "
                                 f"({', '.join(c.name for c in pk)}); ask for fewer rows")  # fmt: skip
            self.rows_out.append(row)
        return self.rows_out

    def _apply_anchor(self, i: int, values: dict[str, Any]) -> None:
        a = self.anchors[i]
        values[a["column"].name] = a["id"]
        for name, state in a["state"].items():
            col = next((c for c in self.table.columns if obs.key(c.name) == obs.key(name)), None)
            limit = _length(col.type) if col is not None else None
            if col is not None and not col.references and (limit is None or len(state) <= limit):
                values[col.name] = state

    def row(self, i: int, attempt: int) -> dict[str, Any]:
        table = self.table
        person = self.rng("#person")
        ctx: dict[str, Any] = {"first": person.choice(FIRST), "last": person.choice(LAST)}
        values: dict[str, Any] = {}
        for col in self._fk_order():
            if col.name not in values:
                self.foreign(col, i, values)
        for col in table.columns:
            if col.name in values or col.references:
                continue
            if col.autoincrement:
                if (table.name, col.name) in self.needed or any(
                    a["column"] is col for a in self.anchors
                ):
                    values[col.name] = i + 1
                continue
            values[col.name] = self.value(col, i, attempt, ctx)
        if i < len(self.anchors):
            self._apply_anchor(i, values)
        return {c.name: values[c.name] for c in table.columns if c.name in values}

    def _fk_order(self) -> list[Column]:
        """Foreign keys whose parent row decides another foreign key go first."""
        fks = [c for c in self.table.columns if c.references]
        targets = {c.references for c in fks}

        def decides(col: Column) -> int:
            parent = self.by_name.get(col.references[0])
            if parent is None:
                return 0
            return -sum(1 for p in parent.columns if p.references in targets)

        return sorted(fks, key=decides)

    def foreign(self, col: Column, i: int, values: dict[str, Any]) -> None:
        parent_name, parent_col = col.references
        rng = self.rng(col.name)
        if col.nullable and rng.random() < self._null_share(col):
            values[col.name] = None
            return
        if parent_name == self.table.name:
            pool = list(self.rows_out)
            if not pool:  # the first row of a self-referencing table
                values[col.name] = None if col.nullable else i + 1
                return
        elif parent_name in self.done:
            pool = self.done[parent_name]
        elif parent_name in self.samples:
            pool = self.samples[parent_name]
        else:
            if col.nullable:
                values[col.name] = None
                return
            raise ValueError(
                f"{self.table.name}.{col.name} must refer to a {parent_name} row: add "
                f"{parent_name} to the counts, or give samples with {parent_name} rows"
            )
        pool = [r for r in pool if r.get(parent_col) is not None]
        if col.name in self.used:
            pool = [r for r in pool if r[parent_col] not in self.used[col.name]]
        if not pool:
            if col.nullable:
                values[col.name] = None
                return
            raise ValueError(f"not enough {parent_name} rows for {self.table.name}.{col.name}: "
                             "each must be used once; generate more parents")  # fmt: skip
        chosen = rng.choice(pool)
        values[col.name] = chosen[parent_col]
        if col.name in self.used:
            self.used[col.name].add(chosen[parent_col])
        parent = self.by_name.get(parent_name)
        for other in self.table.columns:  # keep sibling references consistent
            if other is col or not other.references or other.name in values or parent is None:
                continue
            via = next((p for p in parent.columns if p.references == other.references), None)
            if via is not None and chosen.get(via.name) is not None:
                values[other.name] = chosen[via.name]

    # -- one value --

    def _null_share(self, col: Column) -> float:
        rows = self.samples.get(self.table.name) or []
        seen = [r[col.name] for r in rows if col.name in r]
        if seen:
            return sum(1 for v in seen if v is None) / len(seen)
        info = self._info(col)
        if info and "null_share" in info and info.get("seen", 0) >= 3:
            return float(info["null_share"])
        return NULL_SHARE

    def _code_list(self, col: Column) -> list[str] | None:
        rows = self.samples.get(self.table.name) or []
        values = sorted({str(r[col.name]) for r in rows if r.get(col.name) is not None})
        limit = _length(col.type)
        values = [v for v in values if limit is None or len(v) <= limit]
        return values if 0 < len(values) <= CODE_LIST_MAX else None

    def value(self, col: Column, i: int, attempt: int, ctx: dict[str, Any]) -> Any:
        rng = self.rng(col.name)
        if col.nullable and not col.primary_key and rng.random() < self._null_share(col):
            return None
        unique = col.name in self.used
        info = self._info(col)
        if info and info.get("kind") == "code" and _is_text(col.type) and not unique:
            chosen = self._profile_choice(col, info, rng)
            if chosen is not None:
                return chosen
        if _is_text(col.type) and not unique:
            codes = self._code_list(col)
            if codes:
                return rng.choice(codes)
        for tries in range(ATTEMPTS):
            candidate = self._profile_value(col, i, attempt + tries, rng)
            if candidate is None:
                candidate = self.fresh(col, i, attempt + tries, ctx, rng)
            if not unique:
                return candidate
            if candidate not in self.used[col.name]:
                self.used[col.name].add(candidate)
                return candidate
        raise ValueError(f"cannot make {self.n} different values for {self.table.name}."
                         f"{col.name} ({col.type}); ask for fewer rows")  # fmt: skip

    def fresh(self, col: Column, i: int, attempt: int, ctx: dict, rng: random.Random) -> Any:
        kind = col.type
        name = col.name.lower()
        if kind in INT_RANGE:
            return _integer(kind, name, i, attempt, rng, col.name in self.used, self.n)
        if kind.startswith("NUMERIC"):
            return _decimal(kind, name, rng)
        if kind == "DOUBLE PRECISION":
            return round(rng.uniform(0, 1000), 2)
        if kind == "BOOLEAN":
            share = 0.8 if name.startswith(("is_", "has_", "active", "enabled")) else 0.5
            return rng.random() < share
        if kind == "DATE":
            if "birth" in name or name in ("dob", "born"):
                return (date(1950, 1, 1) + timedelta(days=rng.randint(0, 20000))).isoformat()
            return (BASE.date() - timedelta(days=rng.randint(0, 730))).isoformat()
        if kind in ("TIMESTAMP", "TIMESTAMP WITH TIME ZONE"):
            return _moment(kind, name, ctx, rng)
        if kind == "TIME":
            return f"{rng.randint(7, 18):02d}:{rng.choice((0, 15, 30, 45)):02d}:00"
        if kind == "UUID":
            return str(uuid.UUID(int=rng.getrandbits(128), version=4))
        if kind == "JSON":
            return {}
        if kind == "BLOB":
            return base64.b64encode(bytes(rng.getrandbits(8) for _ in range(8))).decode()
        text = self.text(col, name, i, ctx, rng)
        return _fit(text, _length(kind), attempt, rng, i)

    def text(self, col: Column, name: str, i: int, ctx: dict, rng: random.Random) -> str:
        table = self.table.name.lower()
        first, last = ctx["first"], ctx["last"]
        limit = _length(col.type)
        n = i + 1
        if "email" in name or name in ("mail", "e_mail"):
            address = f"{first}.{last}{n}@example.test".lower()
            return address if limit is None or len(address) <= limit else f"u{n}@example.test"
        if (
            "phone" in name
            or "mobile" in name
            or name in ("tel", "fax", "telephone")
            or (name.endswith(("_tel", "_fax")))
        ):
            full = f"+1 {rng.randint(201, 989)} 555 01{rng.randint(0, 99):02d}"
            return (
                full if limit is None or len(full) <= limit else f"555-01{rng.randint(0, 99):02d}"
            )
        if name in ("first_name", "firstname", "given_name", "fname", "forename"):
            return first
        if name in ("last_name", "lastname", "surname", "family_name", "lname"):
            return last
        if _person_name(name, table):
            return f"{first} {last}"
        if name in ("username", "user_name", "login", "userid", "user_id", "login_name"):
            return f"{first[0]}{last}{n}".lower()
        if any(w in name for w in STAFF_WORDS):
            return rng.choice(self.staff)
        if any(w in name for w in ("password", "hash", "secret", "token", "salt")):
            return "".join(rng.choice("0123456789abcdef") for _ in range(min(limit or 64, 64)))
        if any(w in name for w in SECRET_WORDS):
            return f"TEST{n:06d}"
        if "city" in name or "town" in name:
            return rng.choice(CITIES)
        if "country" in name:
            return rng.choice(COUNTRIES)
        if name in ("region", "province", "county") or name.endswith("_region"):
            return rng.choice(REGIONS)
        if "street" in name or "address" in name:
            return f"{rng.randint(1, 999)} {rng.choice(STREETS)} Street"
        if "zip" in name or "postal" in name or "postcode" in name:
            return f"{rng.randint(10000, 99999)}"
        if "url" in name or "website" in name or "link" in name:
            return f"https://www.example.test/{table}/{n}"
        for word, options in FALLBACKS.items():
            if name == word or name.endswith("_" + word):
                fitting = [o for o in options if limit is None or len(o) <= limit]
                if fitting:
                    return rng.choice(fitting)
        if name.endswith(("_no", "_nr", "_num", "_number", "_ref", "reference")) or name in (
            "no", "nr", "number", "ref", "num"
        ):  # fmt: skip
            return f"{_initial(table)}-{1000 + n}"
        if name == "code" or name.endswith("_code") or name.startswith("code_"):
            return f"{_initial(table, 3)}-{n:03d}"
        if name in ("title", "subject", "summary", "headline", "topic"):
            return f"{rng.choice(SUBJECTS)} {rng.choice(TOPICS)}"
        if any(w in name for w in ("desc", "note", "comment", "text", "body", "message", "reason",
                                   "detail", "remark", "content", "explanation")):  # fmt: skip
            count = rng.randint(1, 3)
            return " ".join(
                rng.choice(SENTENCES).format(t=rng.choice(TOPICS)) for _ in range(count)
            )
        if name == "name" or name.endswith("_name"):
            return f"{_title(self.table.name)} {n}"
        if limit is not None and limit <= 12:
            return f"{_initial(name, 3)}{n}"
        return f"{_title(col.name)} {n}"


# --- helpers -----------------------------------------------------------------------------------


def _unique(table: Table, col: Column) -> bool:
    keys = [c for c in table.columns if c.primary_key]
    return col.unique or (col.primary_key and len(keys) == 1)


def _is_text(kind: str) -> bool:
    return kind in ("TEXT", "VARCHAR") or kind.startswith("VARCHAR(")


def _length(kind: str) -> int | None:
    m = re.fullmatch(r"VARCHAR\((\d+)\)", kind)
    return int(m.group(1)) if m else None


def _person_name(name: str, table: str) -> bool:
    if name in ("full_name", "fullname", "display_name", "person_name", "contact_name",
                "customer_name", "employee_name", "client_name", "member_name", "owner_name",
                "contact", "user_full_name"):  # fmt: skip
        return True
    return name == "name" and any(w in table for w in PEOPLE_TABLES)


def _initial(text: str, size: int = 1) -> str:
    letters = re.sub(r"[^A-Za-z]", "", text).upper()
    return (letters or "X")[:size]


def _title(text: str) -> str:
    return re.sub(r"[_\s]+", " ", text).strip().capitalize() or "Item"


def _fit(text: str, limit: int | None, attempt: int, rng: random.Random, i: int) -> str:
    """Cut to the column length; later attempts add a suffix to get past a duplicate."""
    if attempt:
        suffix = str(i + 1) if attempt == 1 else str(rng.randint(0, 10**6))
        text = text + suffix
        if limit is not None and len(text) > limit:
            text = text[: max(0, limit - len(suffix))] + suffix[-limit:]
    if limit is not None:
        text = text[:limit]
    return text


def _integer(kind: str, name: str, i: int, attempt: int, rng: random.Random, unique: bool,
             n: int) -> int:  # fmt: skip
    top = INT_RANGE[kind]
    if unique:
        return i + 1 if attempt == 0 else rng.randint(1, min(top, 10 * n + 1000))
    if any(w in name for w in ("qty", "quantity", "count", "number_of", "num_")):
        return rng.randint(1, 20)
    if "age" == name or name.endswith("_age"):
        return rng.randint(18, 90)
    if "year" in name:
        return rng.randint(2015, 2026)
    if any(w in name for w in ("sort", "order", "position", "rank", "seq", "level")):
        return rng.randint(1, 10)
    return rng.randint(1, min(top, 1000))


def _decimal(kind: str, name: str, rng: random.Random) -> str:
    m = re.fullmatch(r"NUMERIC\((\d+),(\d+)\)", kind)
    precision, scale = (int(m.group(1)), int(m.group(2))) if m else (12, 2)
    top = Decimal(10) ** (precision - scale) - 1 if precision > scale else Decimal(0)
    if any(w in name for w in ("rate", "percent", "pct", "ratio")):
        high = 100
    elif any(w in name for w in ("amount", "price", "total", "cost", "fee", "balance", "salary")):
        high = 5000
    else:
        high = 1000
    high = min(Decimal(high), top) if top else Decimal(1) - Decimal(1).scaleb(-scale)
    units = rng.randint(0, int(high * (10**scale)))
    value = (Decimal(units) / (Decimal(10) ** scale)).quantize(
        Decimal(1).scaleb(-scale), rounding=ROUND_HALF_EVEN
    )
    return format(value, "f")


def _moment(kind: str, name: str, ctx: dict, rng: random.Random) -> str:
    earliest = ctx.get("moment")
    if earliest is not None and any(w in name for w in LATER_WORDS):
        moment = earliest + timedelta(seconds=rng.randint(0, 30 * 86400))
    else:
        moment = BASE - timedelta(seconds=rng.randint(0, 730 * 86400))
        if earliest is None or moment < earliest:
            ctx["moment"] = moment
    if kind == "TIMESTAMP":
        return moment.replace(tzinfo=None).isoformat()
    return moment.isoformat()
