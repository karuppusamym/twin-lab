"""The cookies an application uses, by name only: for the builder, never a value.

A rebuilt application must set and read the same cookies, under the same names, or its pages and
its API do not work together (Demoblaze keys the cart by a `user=<guid>` cookie its own script
sets, and sends it back in the body field `cookie`). Observed, per cookie name:

- who sets it: a response (`Set-Cookie`, with the attribute names it gave: Path, HttpOnly...)
  or the application's own script (`document.cookie = "user=" + ...`, with the attributes the
  statement writes);
- how many recorded requests sent it back, and to which addresses;
- the request body fields that carry it (`cookie: "user=..."`), so the server can key records
  by it;
- the shape of its value (a UUID, a number, a token): never the value.

`facts()` joins these with the `Set-Cookie` facts of `signin.py` (`sanitize.cookie_attributes`:
the response that set or cleared each cookie and its attributes) into one row per cookie name,
which the brief's *Sign-in and session*, `session.json` and `spec/ARCHITECTURE.md` all show.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from labctl import sanitize

SET_BY_SCRIPT = re.compile(
    r"document\.cookie\s*=\s*(?P<q>[\"'])(?P<name>[A-Za-z0-9_.!#$%&*+^`|~-]{1,64})"
    r"(?:=|(?P=q)\s*\+\s*[\"']=)(?P<rest>[^\n;]*(?:;[^\n]*)?)"
)
ATTRIBUTES = ("Path", "Domain", "Expires", "Max-Age", "Secure", "HttpOnly", "SameSite")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def value_kind(value: str) -> str:
    """The shape of a cookie value, as the brief may say it (never the value)."""
    v = value.strip().strip('"')
    if not v:
        return "empty"
    if UUID.match(v):
        return "a UUID"
    if v.isdigit():
        return f"a number ({len(v)} digits)"
    if re.fullmatch(r"[0-9a-f]{16,}", v, re.I):
        return f"a hexadecimal token ({len(v)} characters)"
    if re.fullmatch(r"[A-Za-z0-9+/=_.-]{16,}", v):
        return f"a token ({len(v)} characters)"
    return "text"


def _attrs(text: str) -> list[str]:
    found = []
    for a in ATTRIBUTES:
        if re.search(rf"(?:^|;)\s*{re.escape(a)}\b", text, re.IGNORECASE) and a not in found:
            found.append(a)
    return found


def _headers(side: dict, name: str) -> list[str]:
    return [str(h.get("value") or "") for h in side.get("headers") or []
            if str(h.get("name") or "").lower() == name]  # fmt: skip


def observe(hars: list[dict], scripts: list[tuple[str, str]]) -> list[dict]:
    """Per cookie name: `set_by` (response / script and where), `attributes`, `sent` (requests),
    `to` (address patterns), `in_body` (fields that carry it), `value`: its shape.

    `hars`: the recordings' HAR documents; `scripts`: (address path, text) of the application's
    own scripts. Values are read here only to tell their shape and the body fields they are in."""
    out: dict[str, dict] = {}

    def item(name: str) -> dict:
        return out.setdefault(name, {"name": name, "set_by": [], "attributes": [], "sent": 0,
                                     "to": [], "in_body": [], "value": None})  # fmt: skip

    for doc in hars:
        for entry in (doc.get("log") or {}).get("entries") or []:
            req, resp = entry.get("request") or {}, entry.get("response") or {}
            url = urlsplit(str(req.get("url") or ""))
            route = f"{str(req.get('method') or 'GET').upper()} {sanitize.route_pattern(url.path)}"
            sent: dict[str, str] = {}
            for header in _headers(req, "cookie"):
                for part in header.split(";"):
                    name, eq, value = part.strip().partition("=")
                    if eq and sanitize.COOKIE_NAME.match(name):
                        sent[name] = value
            for name, value in sent.items():
                c = item(name)
                c["sent"] += 1
                if route not in c["to"] and len(c["to"]) < 40:
                    c["to"].append(route)
                c["value"] = c["value"] or value_kind(value)
            for header in _headers(resp, "set-cookie"):
                for line in header.splitlines():
                    name, eq, rest = line.strip().partition("=")
                    if eq and sanitize.COOKIE_NAME.match(name):
                        c = item(name)
                        where = f"the answer to {route}"
                        if where not in c["set_by"]:
                            c["set_by"].append(where)
                        c["attributes"] += [a for a in _attrs(rest) if a not in c["attributes"]]
    for path, text in scripts:
        for m in SET_BY_SCRIPT.finditer(text or ""):
            c = item(m.group("name"))
            where = f"the script `{path}`"
            if where not in c["set_by"]:
                c["set_by"].append(where)
            c["attributes"] += [a for a in _attrs(m.group("rest")) if a not in c["attributes"]]
    # a body field holding `<name>=<value>` of a known cookie: the server keys records by it
    for doc in hars:
        for entry in (doc.get("log") or {}).get("entries") or []:
            req = entry.get("request") or {}
            body = str((req.get("postData") or {}).get("text") or "")
            if "=" not in body:
                continue
            route = (f"{str(req.get('method') or 'GET').upper()} "
                     f"{sanitize.route_pattern(urlsplit(str(req.get('url') or '')).path)}")  # fmt: skip
            for name, c in out.items():
                pat = r"\"([\w.-]{1,40})\"\s*:\s*\"" + re.escape(name) + "="
                for m in re.finditer(pat, body):
                    field = f"`{m.group(1)}` of `{route}`"
                    if field not in c["in_body"] and len(c["in_body"]) < 12:
                        c["in_body"].append(field)
    for c in out.values():  # calls first, then pages, then files
        c["to"].sort(
            key=lambda r: (r.startswith("GET "), bool(re.search(r"\.(?!html?$)\w{2,5}$", r)), r)
        )
    return sorted(out.values(), key=lambda c: (-c["sent"], c["name"]))


def facts(ctxs: list, found: dict | None = None) -> list[dict]:
    """One row per cookie name, for the brief, `session.json` and `ARCHITECTURE.md`: the
    responses that set and cleared it with their attributes (`signin._cookie_facts`, from
    `Set-Cookie` through `sanitize.cookie_attributes`), and from `observe()` the scripts that set
    it, the requests that sent it, the body fields that carry it and the shape of its value.
    Never a value. `found`: `signin.observe()`'s answer, whose rows are reused."""
    from labctl import har, pagerules, signin

    if found and found.get("cookie_facts") is not None:
        rows = [dict(r) for r in found["cookie_facts"]]
    else:  # nobody signed in: the cookies the responses set all the same
        tmp: dict = {"cookies": [], "cookie_facts": []}
        for c in ctxs:
            owner_of = {id(e): seq for seq, exs in c.part.owner.items() for e in exs}
            signin._cookie_facts(tmp, c, owner_of)
        rows = tmp["cookie_facts"]
    hars = []
    for c in ctxs:
        try:
            hars.append(har.load(c.part.path).har)
        except (OSError, ValueError):
            continue
    scripts, _ = pagerules.scripts_of(ctxs)
    seen = observe(hars, [(s.path, s.text) for s in scripts if s.own and not s.library])
    return merge(rows, seen)


def merge(rows: list[dict], seen: list[dict]) -> list[dict]:
    """`Set-Cookie` rows (signin) with what `observe()` saw, one row per name."""
    by = {r["name"]: r for r in rows}
    for o in seen:
        row = by.get(o["name"])
        if row is None:
            row = by[o["name"]] = {"name": o["name"], "set_by": [], "cleared_by": []}
            rows.append(row)
        for w in o["set_by"]:
            if w.startswith("the script "):
                item = {"script": w.removeprefix("the script ").strip("`")}
                if item not in row["set_by"]:
                    row["set_by"].append(item)
        if not row.get("attributes") and o["attributes"]:
            # a script writes the attributes in its statement: their names, no value
            row["script_attributes"] = o["attributes"]
        row["sent"] = o["sent"]
        row["sent_to"] = o["to"][:12]
        row["sent_back"] = bool(row.get("sent_back") or o["sent"])
        if o["in_body"]:
            row["in_body"] = o["in_body"]
        if o["value"]:
            row["shape"] = o["value"]
    return sorted(rows, key=lambda r: (-int(r.get("sent") or 0), r["name"]))


def where_set(row: dict) -> str:
    """The setters of a cookie row as the brief and the architecture say them."""
    parts = []
    for w in row.get("set_by") or []:
        if w.get("script"):
            parts.append(f"the script `{w['script']}`")
        else:
            parts.append(f"`{w.get('request')} {w.get('status')}`"
                         + (f" (step {w['step']}, on `{w.get('screen')}`)" if w.get("step") else ""))  # fmt: skip
    return "; ".join(parts) or "no recorded response or script (it was there already)"


def use_text(row: dict) -> str:
    """How a cookie is used, as one clause: requests that sent it, body fields, value shape."""
    bits = []
    if row.get("sent"):
        to = row.get("sent_to") or []
        bits.append(f"sent with {row['sent']} recorded request(s) ("
                    + ", ".join(f"`{t}`" for t in to[:6]) + (", ..." if len(to) > 6 else "") + ")")  # fmt: skip
    elif "sent" in row:
        bits.append("never sent in the recording (set on a path it did not take, such as "
                    "signing in)")  # fmt: skip
    if row.get("shape"):
        bits.append(f"its value is {row['shape']}")
    if row.get("in_body"):
        bits.append(f"the pages also send `{row['name']}=<value>` in the body field "
                    + ", ".join(row["in_body"]) + ": the server keys those records by it")  # fmt: skip
    return "; ".join(bits)
