"""Whether a list's order is shown to be stable, from what was recorded.

A list's order is a rule only when the evidence shows it: two or more recorded answers (or page
views) of the list agree on an order that follows a sortable key that is not random, or the page
offers a sort control and the recording used it. A key that holds random values (a UUID, a
random token, a hash) orders nothing: rows keyed by it come back in whatever order the server or
the page's parallel requests produce. One answer shows one order, not a rule.

Used by the API catalogue (`labctl.apicatalog`: the order a builder answers a list in) and by
the acceptance drafts (`labctl.behaviour.drafts`: a list case compares in any order,
`unordered: true`, unless its order is stable). Only key names leave this module, never values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

UUID = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.I)
HEX = re.compile(r"[0-9a-f]{16,}", re.I)  # a hash or an object id
TOKEN = re.compile(r"[A-Za-z0-9_-]{16,}")  # a random token: letters and digits mixed

NOT_STABLE = "not stable"


def is_random(value) -> bool:
    """A value that orders nothing: a UUID, a hash, a random token (anywhere in the text, so
    `user=<uuid>` and `deleteItem('<uuid>')` count)."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return False
    text = str(value).strip()
    if not text or re.fullmatch(r"-?\d+(\.\d+)?", text):
        return False
    if UUID.search(text):
        return True
    for m in TOKEN.finditer(text):
        tok = m.group(0)
        if HEX.fullmatch(tok) and re.search(r"\d", tok) and re.search(r"[a-f]", tok, re.I):
            return True
        if (re.search(r"\d", tok) and re.search(r"[a-z]", tok) and re.search(r"[A-Z]", tok)
                and len(re.findall(r"\d", tok)) >= 3):  # fmt: skip
            return True
    return False


def sortable(v):
    """A value as it sorts (numbers as numbers, text without case); None when it cannot."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return (0, float(v))
    if isinstance(v, str) and re.fullmatch(r"-?\d+(\.\d+)?", v.strip()):
        return (0, float(v))
    return (1, " ".join(v.split()).lower()) if isinstance(v, str) else None


@dataclass
class Order:
    stable: bool
    key: str | None = None  # the key the rows follow, when stable
    direction: str | None = None  # ascending | descending
    label: str = ""  # `rows ordered by ...` or `not stable (...)`
    reason: str = ""  # why, in a sentence (key names only)
    followed: list[str] = field(default_factory=list)  # `id` ascending, ... in the views

    @property
    def unordered(self) -> bool:
        return not self.stable


def _ordering_keys(views: list[list[dict]]) -> list[tuple[str, str, bool]]:
    """(key, direction, random) of every key that orders every view with several rows."""
    multi = [v for v in views if len(v) > 1]
    if not multi:
        return []
    keys = [k for k in multi[0][0] if all(sortable(r.get(k)) is not None for v in multi for r in v)]
    out = []
    for k in sorted(keys, key=lambda k: (str(k).lower() != "id", str(k))):
        for name, rev in (("ascending", False), ("descending", True)):
            if all([sortable(r[k]) for r in v] == sorted((sortable(r[k]) for r in v), reverse=rev)
                   and len({sortable(r[k]) for r in v}) > 1 for v in multi):  # fmt: skip
                out.append((k, name, all(is_random(r[k]) for v in multi for r in v)))
                break
    return out


def judge(views: list[list[dict]], *, what: str = "answer", sort_used: bool = False) -> Order:
    """The order of a list, from its recorded `views` (each a list of rows: key -> value, in
    the order shown). `what` names a view (`answer`, `page view`). `sort_used`: the page offers a
    sort control and the recording used it before these views."""
    multi = [v for v in views if len(v) > 1]
    found = _ordering_keys(views)
    followed = [f"`{k}` {d}" + (" (random values)" if rnd else "") for k, d, rnd in found]
    plain = [(k, d) for k, d, rnd in found if not rnd]
    if sort_used and multi:
        k, d = plain[0] if plain else (None, None)
        return Order(True, k, d, "rows ordered by the sort control the recording used"
                     + (f" (`{k}` {d})" if k else ""),
                     "the page offers a sort control and the recording used it", followed)  # fmt: skip
    if not multi:
        return Order(False, label=f"{NOT_STABLE} (no {what} with several rows)",
                     reason=f"no recorded {what} showed the list with two rows or more")  # fmt: skip
    if found and not plain:
        return Order(False, label=f"{NOT_STABLE} (random keys)",
                     reason=f"the rows followed {', '.join(followed)}, a key with random values "
                            "(a UUID, a token or a hash) that orders nothing", followed=followed)  # fmt: skip
    if len(multi) < 2:
        said = (
            f"the one recorded {what} with several rows followed {', '.join(followed)}"
            if (followed)
            else f"no key orders the rows of the one recorded {what} with several rows"
        )
        return Order(False, label=f"{NOT_STABLE} (one {what})",
                     reason=said + f"; one {what} shows one order, not the list's rule",
                     followed=followed)  # fmt: skip
    if not plain:
        return Order(False, label=f"{NOT_STABLE} (no key orders the rows)",
                     reason=f"no key orders the rows in every recorded {what}: answer them in "
                            "the order they were stored", followed=followed)  # fmt: skip
    sets = {tuple(sorted(str(sorted(r.items(), key=str)) for r in v)) for v in multi}
    if max(len(v) for v in multi) < 3 and len(sets) < 2:
        # two rows always follow some key one way or the other: the same two rows shown again
        # in the same order do not show which key, if any, orders the list
        return Order(False, label=f"{NOT_STABLE} (too few rows)",
                     reason=f"every recorded {what} showed the same two rows, which any key "
                            "orders one way or the other", followed=followed)  # fmt: skip
    k, d = plain[0]
    return Order(True, k, d, f"rows ordered by `{k}` {d} in every recorded {what}",
                 f"{len(multi)} recorded {what}s agree on `{k}` {d}", followed)  # fmt: skip
