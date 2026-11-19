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

`signin.py` may add attribute facts of its own from `Set-Cookie`; this module only reads, and
its one entry point is `observe()`.
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


def brief_lines(found: list[dict]) -> list[str]:
    """The cookies as the brief's sign-in section lists them."""
    if not found:
        return []
    out = ["**Cookies** (names as the original uses them; values are never kept):\n"]
    for c in found:
        how = ", ".join(c["set_by"]) or "not set in the recording (it was there already)"
        line = f"- `{c['name']}`: set by {how}"
        if c["attributes"]:
            line += " with " + ", ".join(f"`{a}`" for a in c["attributes"])
        if c["sent"]:
            line += (f"; sent with {c['sent']} recorded request(s) ("
                     + ", ".join(f"`{t}`" for t in c["to"][:6])
                     + (", ..." if len(c["to"]) > 6 else "") + ")")  # fmt: skip
        else:
            line += (
                "; never sent in the recording (set on a path it did not take, such as signing in)"
            )
        if c["value"]:
            line += f"; its value is {c['value']}"
        if c["in_body"]:
            line += ("; the pages also send `" + c["name"] + "=<value>` in the body field "
                     + ", ".join(c["in_body"]) + ": the server keys those records by it")  # fmt: skip
        out.append(line + ".")
    out.append("Set and read these cookies under the same names, at the same moments; give each "
               "a synthetic value of the same shape.\n")  # fmt: skip
    return out
