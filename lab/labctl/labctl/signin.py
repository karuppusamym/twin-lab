"""Sign-in and session: what a recording shows, and what the rebuilt application must store.

Observed, per application (never a credential: a cookie gives its name, a header its scheme):

- the sign-in screen, its fields, the call it sends (a form post or a JSON call) and each
  recorded attempt: refused (status, message) or accepted (status, where the person landed);
- the session mechanism by kind: the cookie the accepted answer set, or the `Authorization`
  scheme later calls carried;
- sign-out: the element, the call, where it led;
- what a request without a session got: a redirect of a page to the sign-in screen, a 401 of
  an API call.

Required, because a demo application must be a real one: users and sessions live in the backend
database. So two tables are added to `db/schema.sql` (`app_users`, `app_sessions`; "assumed":
no answer showed them) and the seed gets one synthetic user per recorded sign-in: the user name
and password the acceptance replay types (`steps/*.values.yaml`), the password stored as a
PBKDF2 hash. A table the model inferred only from the session answers (`user`) is the same
record: it is left out and its fields become columns of `app_users`.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from labctl import sanitize, seedplan, stepfacts

USERS = "app_users"
SESSIONS = "app_sessions"
ITERATIONS = 100_000
SESSION_ROUTE = stepfacts.SESSION_ROUTE
SIGN_OUT = re.compile(r"log_?out|log_?off|sign_?out|sign_?off", re.I)
PASSWORD = re.compile(r"pass(word|wd)?|pwd", re.I)
HEADING = "## Sign-in and session"
DATA_RULE = (
    "**Everything is stored in the backend.** Every record a screen shows or changes is read "
    "from and written to the database of `db/schema.sql` by the server, on every request: "
    "nothing is kept only in the page or in the server's memory. A reload, a second browser "
    "and a restart of the application show the same data. Browser storage is used only where "
    "this brief's *Browser storage* section says the original uses it."
)


def password_hash(app: str, username: str, password: str) -> str:
    """`pbkdf2_sha256$<iterations>$<salt hex>$<hash hex>`; the salt is fixed per user so the
    generated seed is the same on every run."""
    salt = hashlib.sha256(f"twinlab:{app}:{username}".encode()).digest()[:16]
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS, dklen=32)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${dk.hex()}"


def verify(stored: str, password: str) -> bool:
    try:
        _, rounds, salt, want = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt),
                                 int(rounds), dklen=len(want) // 2)  # fmt: skip
    except ValueError:
        return False
    return dk.hex() == want


# --- what was observed ---------------------------------------------------------------------------


def _is_password(c, step: dict) -> bool:
    raw = c.part.step(step["n"])
    node = seedplan._node(c.part.html_at(step["n"]), raw.get("selector"))
    kind = (node.attrs.get("type") or "").lower() if node is not None else ""
    return kind == "password" or bool(PASSWORD.search(str(step.get("selector") or "")))


def _sent_password(e) -> bool:
    keys = list(e.body) if isinstance(e.body, dict) else list(e.form)
    return e.method == "POST" and any(PASSWORD.search(str(k)) for k in keys)


def observe(ctxs: list) -> dict | None:
    """The sign-in facts of the recordings, or None when nobody signed in."""
    found: dict = {"attempts": [], "users": [], "cookies": [], "schemes": [], "sign_out": [],
                   "unauthenticated": [], "whoami": []}  # fmt: skip
    for c in ctxs:
        steps = c.steps()
        effects = {r["n"]: r for r in c.part.facts.effects}
        typed = [s for s in steps if s["action"] == "type" and s.get("selector")]
        secret = [s for s in typed if _is_password(c, s)]
        if not secret:
            continue
        screen = secret[0].get("screen")
        found.setdefault("screen", screen)
        fields = []
        for s in typed:
            if s.get("screen") == screen and s["selector"] not in [f["selector"] for f in fields]:
                fields.append({"selector": s["selector"], "password": _is_password(c, s)})
        found.setdefault("fields", fields)
        owner_of = {id(e): seq for seq, exs in c.part.owner.items() for e in exs}
        accepted_at = None
        for e in c.part.exchanges:
            if not _sent_password(e):
                continue
            keys = list(e.body) if isinstance(e.body, dict) else list(e.form)
            found.setdefault("call", {
                "method": e.method, "path": e.route,
                "body": "JSON" if isinstance(e.body, dict) else "form fields",
                "fields": [str(k) for k in keys]})  # fmt: skip
            root = c.root_step(owner_of.get(id(e), -1)) or {}
            eff = effects.get(root.get("n"), {})
            html_error = e.status == 200 and "html" in e.resp_type
            ok = (300 <= e.status < 400) or (200 <= e.status < 300 and not html_error)
            row = {"recording": c.name, "step": root.get("n"), "status": e.status,
                   "accepted": ok, "by": f"{root.get('action')} {root.get('selector')}"}  # fmt: skip
            if not ok:
                row["messages"] = [f'{m["selector"]}: "{m["text"]}"'
                                   for m in eff.get("messages", [])][:3]  # fmt: skip
                empty = [str(k) for k in keys
                         if (e.body if isinstance(e.body, dict) else e.form).get(k) == ""]  # fmt: skip
                if empty:
                    row["sent_empty"] = empty
            else:
                row["lands_on"] = eff.get("then")
                if e.location:
                    row["redirect"] = sanitize.route_pattern(e.location)
                if accepted_at is None:
                    accepted_at = e.started
                for n in e.cookies_set:
                    if n not in found["cookies"]:
                        found["cookies"].append(n)
                if isinstance(e.data, dict):
                    row["answer_fields"] = [str(k) for k in e.data]
            found["attempts"].append(row)
        # the pair the replay types: the last value of each field before the accepted call
        ok_steps = [a["step"] for a in found["attempts"] if a["accepted"] and a["recording"] == c.name
                    and a["step"] is not None]  # fmt: skip
        if ok_steps:
            upto = ok_steps[0]
            name = next((c.values[s["n"]] for s in reversed(typed) if s["n"] <= upto
                         and s.get("screen") == screen and not _is_password(c, s)
                         and s["n"] in c.values), None)  # fmt: skip
            word = next((c.values[s["n"]] for s in reversed(typed) if s["n"] <= upto
                         and s.get("screen") == screen and _is_password(c, s)
                         and s["n"] in c.values), None)  # fmt: skip
            if name and word and name not in [u["username"] for u in found["users"]]:
                found["users"].append({"username": name, "password": word, "recording": c.name})
        for e in c.part.exchanges:
            before = accepted_at is None or e.started < accepted_at
            if e.auth_scheme and e.auth_scheme not in found["schemes"]:
                found["schemes"].append(e.auth_scheme)
            out = SIGN_OUT.search(e.route) or (
                e.method == "DELETE" and SESSION_ROUTE.search(e.route) and 200 <= e.status < 400
            )
            if out:
                root = c.root_step(owner_of.get(id(e), -1)) or {}
                eff = effects.get(root.get("n"), {})
                row = {"request": f"{e.method} {e.route}", "status": e.status,
                       "by": f"{root.get('action')} {root.get('selector')}",
                       "on": root.get("screen"), "step": root.get("n"),
                       "then": eff.get("then") or (sanitize.route_pattern(e.location)
                                                   if e.location else None)}  # fmt: skip
                if row not in found["sign_out"]:
                    found["sign_out"].append(row)
                accepted_at_out = e.started
                later = [x for x in c.part.exchanges if x.started > accepted_at_out
                         and x.status in (401, 403)]  # fmt: skip
                for x in later:
                    _unauth(found, x, "after signing out")
            elif _sent_password(e):
                continue
            elif before and (e.status in (401, 403) or (
                    300 <= e.status < 400 and e.method == "GET" and e.location
                    and sanitize.route_pattern(e.location) == found.get("screen"))):  # fmt: skip
                _unauth(found, e, "before signing in")
            elif (e.method == "GET" and SESSION_ROUTE.search(e.route) and e.status == 200
                  and isinstance(e.data, dict)):  # fmt: skip
                row = {"request": f"GET {e.route}", "fields": _shape_keys(e.data)}
                if row not in found["whoami"]:
                    found["whoami"].append(row)
        for e in c.part.exchanges:  # the cookie later calls carried
            for n in e.cookies_sent:
                if n in found["cookies"]:
                    found["cookie_sent_back"] = True
        _cookie_facts(found, c, owner_of)
    return found if found.get("screen") else None


def _cookie_facts(found: dict, c, owner_of: dict) -> None:
    """Per cookie NAME: which responses set it (request, status, step, screen), its attributes
    as the setting response gave them, which responses cleared it, and whether later requests
    sent it back. A cookie's value is never read: `sanitize.cookie_attributes` drops it."""
    rows: dict[str, dict] = {r["name"]: r for r in found.setdefault("cookie_facts", [])}
    for e in c.part.exchanges:
        for attrs in e.cookie_attrs or ():
            root = c.root_step(owner_of.get(id(e), -1)) or {}
            where = {"request": f"{e.method} {e.route}", "status": e.status,
                     "step": root.get("n"), "screen": root.get("screen")}  # fmt: skip
            row = rows.get(attrs["name"])
            if row is None:
                row = {"name": attrs["name"], "set_by": [], "cleared_by": []}
                rows[attrs["name"]] = row
                found["cookie_facts"].append(row)
            if attrs.get("clears"):
                if where not in row["cleared_by"]:
                    row["cleared_by"].append(where)
                continue
            if where not in row["set_by"]:
                row["set_by"].append(where)
            row.setdefault("attributes", {k: v for k, v in attrs.items()
                                          if k not in ("name", "clears")})  # fmt: skip
    for row in found["cookie_facts"]:
        row["sent_back"] = row.get("sent_back") or any(
            row["name"] in e.cookies_sent for e in c.part.exchanges)  # fmt: skip
        row["session_cookie"] = row["name"] in found["cookies"]


def cookie_lines(found: dict) -> list[str]:
    """The brief's lines on each cookie: name, who sets it, attributes, when it is cleared."""
    out = []
    for row in found.get("cookie_facts") or []:
        a = row.get("attributes") or {}
        sets = "; ".join(f"`{w['request']} {w['status']}`"
                         + (f" (step {w['step']}, on `{w['screen']}`)" if w.get("step") else "")
                         for w in row["set_by"]) or "no recorded response"  # fmt: skip
        flags = [f"Path `{a['path']}`" if a.get("path") else "",
                 f"Domain: {a['domain']}" if a.get("domain") else "",
                 "HttpOnly" if a.get("http_only") else "not HttpOnly",
                 "Secure" if a.get("secure") else "not Secure",
                 f"SameSite {a['same_site']}" if a.get("same_site") else "",
                 "Partitioned" if a.get("partitioned") else "",
                 str(a.get("lifetime") or "")] if a else ["attributes not recorded"]  # fmt: skip
        cleared = "; ".join(f"`{w['request']} {w['status']}`"
                            + (f" (step {w['step']})" if w.get("step") else "")
                            for w in row["cleared_by"])  # fmt: skip
        out.append(f"- Cookie `{row['name']}`"
                   + (" (the session)" if row.get("session_cookie") else "")
                   + f": set by {sets}; " + ", ".join(f for f in flags if f)
                   + (f"; cleared by {cleared}" if cleared else "; never cleared in the recording")
                   + (". Later requests sent it back." if row.get("sent_back") else ".")
                   + " Its value is never kept and is not needed.")  # fmt: skip
    return out


def _shape_keys(data: dict) -> list[str]:
    out = []
    for k, v in data.items():
        if isinstance(v, dict):
            out += [f"{k}.{x}" for x in v]
        else:
            out.append(str(k))
    return out


def _unauth(found: dict, e, when: str) -> None:
    row = {"request": f"{e.method} {e.route}", "status": e.status, "when": when}
    if e.location:
        row["to"] = sanitize.route_pattern(e.location)
    elif isinstance(e.data, dict):
        row["answer_fields"] = [str(k) for k in e.data]
    else:
        row["answer"] = "body not recorded"
    if row not in found["unauthenticated"]:
        found["unauthenticated"].append(row)


# --- tables and seed -----------------------------------------------------------------------------


def session_tables(brief) -> list[str]:
    """Tables of the brief that were inferred only from session answers (`user` from
    `GET /api/session`): the same record as `app_users`."""
    out = []
    for name, reasons in brief.tables.items():
        if reasons and all(re.match(r"used by \w+ \S+$", r) and SESSION_ROUTE.search(r)
                           for r in reasons):  # fmt: skip
            out.append(name)
    return out


def adopt(brief) -> dict[str, list[str]]:
    """Take the session-only tables out of the brief's scope; returns {table: its columns}."""
    taken: dict[str, list[str]] = {}
    for name in session_tables(brief):
        taken[name] = [c.name for c in brief.table(name).columns]
        del brief.tables[name]
    return taken


def profile_columns(found: dict, taken: dict[str, list[str]]) -> list[str]:
    """Columns of `app_users` beside the user name and the hash: what the answers show of the
    signed-in user (`display_name`, `role`), never a credential."""
    cols: list[str] = []
    for names in taken.values():
        cols += names
    for row in found.get("whoami") or []:
        cols += [f.split(".")[-1] for f in row["fields"]]
    out = []
    for c in cols:
        name = re.sub(r"[^a-z0-9_]", "_", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", c).lower())
        if (name and name not in out and name not in ("username", "password_hash")
                and not PASSWORD.search(name) and not re.search(r"token|secret", name)):  # fmt: skip
            out.append(name)
    return out[:12]


def schema_sql(columns: list[str]) -> str:
    extra = "".join(f",\n  {c} VARCHAR(200)" for c in columns)
    return (
        "\n-- app_users, app_sessions: assumed (no recorded answer showed them). Required by the "
        'brief\'s\n-- "Sign-in and session": users and sessions are stored in the database.\n'
        f"CREATE TABLE {USERS} (\n  username VARCHAR(200) NOT NULL,\n  password_hash VARCHAR(200) "
        f"NOT NULL{extra},\n  PRIMARY KEY (username)\n);\n\n"
        f"CREATE TABLE {SESSIONS} (\n  token VARCHAR(200) NOT NULL,\n  username VARCHAR(200) NOT "
        "NULL,\n  created_at VARCHAR(40) NOT NULL,\n  PRIMARY KEY (token)\n);\n"
    )


def user_rows(app: str, found: dict, columns: list[str], profile: dict) -> list[dict]:
    rows = []
    for i, u in enumerate(found.get("users") or []):
        row = {"username": u["username"],
               "password_hash": password_hash(app, u["username"], u["password"])}  # fmt: skip
        for c in columns:
            value = profile.get(c)
            row[c] = value if isinstance(value, str) and i == 0 else (
                value if isinstance(value, str) else None)  # fmt: skip
        rows.append(row)
    return rows


def profile_from_catalogue(endpoints: dict) -> dict:
    """The synthetic user fields the API examples show (`display_name`, `role`), so the seeded
    user and the examples are the same person."""
    out: dict = {}
    for doc in (endpoints or {}).values():
        if not SESSION_ROUTE.search(doc.get("path", "")):
            continue
        for call in doc.get("calls") or []:
            body = call.get("response") if call.get("status", 0) < 400 else None
            for holder in [body] if isinstance(body, dict) else []:
                for k, v in holder.items():
                    items = v.items() if isinstance(v, dict) else [(k, v)]
                    for kk, vv in items:
                        if isinstance(vv, str):
                            key = re.sub(r"[^a-z0-9_]", "_",
                                         re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", kk).lower())  # fmt: skip
                            out.setdefault(key, vv)
    return out


def write(root: Path, app: str, found: dict | None, taken: dict, endpoints: dict) -> dict | None:
    """Append the two tables to `db/schema.sql` and the seeded users to `db/seed.sql` and
    `db/seed.json`. Returns what was written, or None when nobody signed in."""
    if not found:
        return None
    db = Path(root) / "apps" / app / "db"
    schema = db / "schema.sql"
    if not schema.is_file():
        return None
    columns = profile_columns(found, taken)
    text = schema.read_text(encoding="utf-8")
    if f"CREATE TABLE {USERS}" not in text:
        schema.write_bytes((text.rstrip("\n") + "\n" + schema_sql(columns)).encode("utf-8"))
    rows = user_rows(app, found, columns, profile_from_catalogue(endpoints))
    if rows:
        names = list(rows[0])
        lines = [f"\n-- {USERS}: {len(rows)} row(s): the user(s) the acceptance replay signs in as "
                 "(steps/*.values.yaml); the password is stored as a hash"]  # fmt: skip
        for r in rows:
            vals = ", ".join("NULL" if r[n] is None else seedplan._q(r[n]) for n in names)
            lines.append(f"INSERT INTO {USERS} ({', '.join(names)}) VALUES ({vals});")
        seed = db / seedplan.SEED_SQL
        head = ("-- Seed data for the " + app + " demo app. Synthetic values only.\n"
                if not seed.is_file() else seed.read_text(encoding="utf-8").rstrip("\n") + "\n")  # fmt: skip
        seed.write_bytes((head + "\n".join(lines) + "\n").encode("utf-8"))
        js = db / seedplan.SEED_JSON
        try:
            doc = json.loads(js.read_text(encoding="utf-8")) if js.is_file() else {}
        except ValueError:
            doc = {}
        doc[USERS] = rows
        doc.setdefault(SESSIONS, [])
        js.write_bytes((json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    return {"columns": columns, "rows": rows, "taken": taken}


# --- the brief -----------------------------------------------------------------------------------


def brief_section(app: str, found: dict | None, done: dict | None) -> str:
    out = [HEADING + "\n"]
    if not found:
        out += ["No sign-in was recorded: the recording never typed a password. Build no sign-in "
                "screen and protect nothing, unless a screen of this brief is one.\n", DATA_RULE, ""]  # fmt: skip
        return "\n".join(out) + "\n"
    call = found.get("call") or {}
    fields = ", ".join(f"`{f['selector']}`" + (" (the password)" if f["password"] else "")
                       for f in found.get("fields") or [])  # fmt: skip
    out.append("**Observed.**\n")
    out.append(f"- Sign-in screen: `{found['screen']}`, fields {fields or 'not typed into'}.")
    if call:
        out.append(f"- The call: `{call['method']} {call['path']}` with {call['body']} "
                   + ", ".join(f"`{f}`" for f in call["fields"]) + ".")  # fmt: skip
    for a in found["attempts"]:
        step = f"step {a['step']}" if a.get("step") is not None else "a step"
        if a["accepted"]:
            took = f"- Accepted at {step} (`{a['by']}`): status `{a['status']}`"
            if a.get("redirect"):
                took += f", redirect to `{a['redirect']}`"
            if a.get("answer_fields"):
                took += ", answer fields " + ", ".join(f"`{f}`" for f in a["answer_fields"])
            elif call.get("body") == "JSON" and not a.get("redirect"):
                took += ", answer body not recorded (the page did not read it)"
            if a.get("lands_on"):
                took += f"; the person landed on `{a['lands_on']}`"
            out.append(took + ".")
        else:
            why = (f" with {', '.join(f'`{k}`' for k in a['sent_empty'])} empty"
                   if a.get("sent_empty") else "")  # fmt: skip
            msg = "; ".join(a.get("messages") or []) or "no message recorded"
            out.append(f"- Refused at {step} (`{a['by']}`){why}: status `{a['status']}`, the page "
                       f"stayed on `{found['screen']}` and showed {msg}.")  # fmt: skip
    if found["cookies"]:
        back = " Later calls sent it back." if found.get("cookie_sent_back") else ""
        out.append("- Session: the accepted answer set the cookie "
                   + ", ".join(f"`{n}`" for n in found["cookies"])
                   + f" (its value is never kept).{back} Use the same cookie name.")  # fmt: skip
    elif found["schemes"]:
        out.append("- Session: later calls carried the header `Authorization: "
                   + "/".join(found["schemes"]) + " <token>` (the token is never kept). Issue a "
                   "token at sign-in and require the same header.")  # fmt: skip
    else:
        out.append("- Session: no cookie set by the accepted answer and no `Authorization` header "
                   "were recorded (the cookie may have been set earlier). Use an HttpOnly cookie "
                   "and say so in BUILD-NOTES.md.")  # fmt: skip
    out += cookie_lines(found)
    for w in found["whoami"]:
        out.append(f"- Who is signed in: `{w['request']}` answered "
                   + ", ".join(f"`{f}`" for f in w["fields"]) + ".")  # fmt: skip
    for s in found["sign_out"]:
        out.append(f"- Sign-out: `{s['by']}` on `{s['on']}` (step {s['step']}) sent "
                   f"`{s['request']} {s['status']}`"
                   + (f", then `{s['then']}`" if s.get("then") else "") + ".")  # fmt: skip
    if not found["sign_out"]:
        out.append("- Sign-out: not recorded.")
    for u in found["unauthenticated"]:
        what = (f"redirect to `{u['to']}`" if u.get("to") else
                "answer fields " + ", ".join(f"`{f}`" for f in u["answer_fields"])
                if u.get("answer_fields") else u.get("answer", ""))  # fmt: skip
        out.append(f"- Without a session ({u['when']}): `{u['request']}` answered `{u['status']}`"
                   + (f", {what}" if what else "") + ".")  # fmt: skip
    if not found["unauthenticated"]:
        out.append("- Without a session: nothing was recorded. Hypothesis: a page redirects to "
                   f"`{found['screen']}`, an API call answers 401.")  # fmt: skip
    out += ["", "**Required.**\n"]
    if done:
        cols = "".join(f", `{c}`" for c in done["columns"])
        out.append(f"- Users are rows of `{USERS}` (`username`, `password_hash`{cols}) and "
                   f"sessions rows of `{SESSIONS}` (`token`, `username`, `created_at`), both in "
                   "`db/schema.sql`. They are assumed tables: no recorded answer showed them.")  # fmt: skip
        for name, columns in (done.get("taken") or {}).items():
            out.append(f"- The answers' `{name}` object ("
                       + ", ".join(f"`{c}`" for c in columns)
                       + f") is read from `{USERS}`: there is no separate `{name}` table.")  # fmt: skip
        if done["rows"]:
            names = ", ".join(f"`{r['username']}`" for r in done["rows"])
            out.append(f"- `db/seed.sql` holds the user(s) {names}: the user name and password "
                       f"the acceptance replay types (`apps/{app}/steps/*.values.yaml`, the steps "
                       "on the fields above). The password is stored as "
                       f"`pbkdf2_sha256$<iterations>$<salt hex>$<hash hex>`: PBKDF2-HMAC-SHA256 "
                       "over the UTF-8 password, the salt being the bytes of `<salt hex>`, 32 "
                       "bytes out. Check a password against it; never store or log a password.")  # fmt: skip
        else:
            out.append("- No replay value was generated for the sign-in fields, so no user is "
                       "seeded: create one synthetic user at start-up and name it in "
                       "BUILD-NOTES.md.")  # fmt: skip
    out += [
        f"- Accept a user name and password that match a row of `{USERS}`; refuse every other "
        "pair, and an empty field, exactly as the refused attempt above (status, body, message). "
        "The recording shows one accepted pair and not the rule behind it: this is a hypothesis; "
        "say so in BUILD-NOTES.md.",
        f"- On sign-in, insert a row into `{SESSIONS}` with a new random token and send it as the "
        "session (the cookie or header above). On every protected page and call, look the token "
        "up in the table. On sign-out, delete the row. A session survives a restart of the "
        "application; it is never kept only in memory.",
        "- Every screen but the sign-in screen, and every endpoint but the sign-in call, needs a "
        "session, answering as *Without a session* above shows. Give every user the same screens "
        "(no roles were recorded).",
        *([COOKIE_RULE] if found.get("cookie_facts") else []),
        "", DATA_RULE, ""]  # fmt: skip
    return "\n".join(out) + "\n"


FILE = "session.json"  # apps/<app>/session.json: the sign-in and session facts, no credential
FORMAT = "twinlab-session/1"
COOKIE_RULE = (
    "- Cookies: set each cookie listed above under the same name, from the same response, with "
    "the same attributes (Path, Domain, HttpOnly, Secure, SameSite, lifetime), and clear it "
    "where the recording cleared it. Its value is your own (a new random token): a recorded "
    "cookie value is never carried and is not needed to rebuild the application."
)
COOKIE_ABOUT = (
    "Cookie values are never captured into the package and are not needed: a rebuilt "
    "application issues its own. What a builder needs is each cookie's name, the response that "
    "sets it, its attributes (Path; Domain relative to the application's host; HttpOnly; "
    "Secure; SameSite; lifetime as a duration, a session cookie or a persistent one) and the "
    "response that clears it (sign-out)."
)


def facts_doc(app: str, found: dict | None) -> dict | None:
    """`session.json`: the sign-in and session facts of `observe()`, for the builder and the
    package's generated documents. Never a user name, password, token or cookie value: the
    seeded user is in `db/seed.sql`, the replay values in `steps/*.values.yaml`."""
    if not found:
        return None
    attempts = [{k: v for k, v in a.items() if k != "recording"} for a in found["attempts"]]
    mechanism = ("cookie" if found["cookies"] else
                 "Authorization header" if found["schemes"] else "not recorded")  # fmt: skip
    return {
        "format": FORMAT, "app": app,
        "about": ("Sign-in and session as the recording showed them: the sign-in screen and "
                  "call, each attempt, the session mechanism, every cookie by name with its "
                  "attributes, sign-out and what a request without a session got. "
                  + COOKIE_ABOUT),  # fmt: skip
        "sign_in": {"screen": found.get("screen"), "fields": found.get("fields") or [],
                    "call": found.get("call"), "attempts": attempts},  # fmt: skip
        "session": {"mechanism": mechanism, "cookies": list(found["cookies"]),
                    "schemes": list(found["schemes"]),
                    "sent_back": bool(found.get("cookie_sent_back"))},  # fmt: skip
        "cookies": [{k: v for k, v in r.items()} for r in found.get("cookie_facts") or []],
        "who_is_signed_in": found.get("whoami") or [],
        "sign_out": found.get("sign_out") or [],
        "without_session": found.get("unauthenticated") or [],
        "tables": {"users": USERS, "sessions": SESSIONS},
    }  # fmt: skip


def write_facts(root: Path, app: str, found: dict | None) -> Path | None:
    """Write `apps/<app>/session.json` (removed when nobody signed in)."""
    path = Path(root) / "apps" / app / FILE
    doc = facts_doc(app, found)
    if doc is None:
        path.unlink(missing_ok=True)
        return None
    path.write_bytes((json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    return path
