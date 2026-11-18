"""A small application with two roles and a cookie session, written for the role and session
tests (test_roles_and_sessions.py). Synthetic users only; served from 127.0.0.1, kept in memory.

- `/signin`: the sign-in form. A wrong user or password shows `#signinError`. After a session
  ended, `/signin?expired=1` shows `#expiredNotice` above the same form.
- `/home`, `/orders`: for anyone signed in. The menu has "Approvals" (`#navApprovals`) for a
  supervisor only.
- `/approvals`: the supervisor's page. A clerk gets HTTP 403 and a refusal page (`#refused`).
- "Sign out" (a POST) ends the session at the server and clears the cookie.
- A protected page without a session answers 302 to `/signin` (to `/signin?expired=1` when the
  browser still holds the cookie of a session that ended).

Users: `clerk.avery` (role clerk) and `supervisor.blake` (role supervisor), see `USERS`.

Session lifetime, for tests: `serve(session_views=N)` ends a session after N protected pages
(a deterministic stand-in for an idle timeout: the page after the Nth is refused), and
`Running.expire_sessions()` ends every session at once from outside the browser (what a timeout
between two steps looks like).

`serve(faults={...})` gives a replica that is wrong in one named way (`FAULTS`). Run by hand with
`python roles_app.py`.
"""

from __future__ import annotations

import html
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

COOKIE = "roles_sid"
USERS = {
    "clerk.avery": {"password": "Reed-Marsh-41", "role": "clerk", "name": "Avery Clerk"},
    "supervisor.blake": {"password": "Stone-Quay-58", "role": "supervisor", "name": "Blake Super"},
}
FAULTS = {
    "menu_for_all": "the Approvals menu entry is shown to every role",
    "no_role_check": "a clerk may open the supervisor's page",
    "no_expiry": "a session never ends by itself",
    "signout_keeps_session": "sign-out leaves the session alive at the server and in the browser",
}
ORDERS = [("Desk lamp", "2"), ("Office chair", "1"), ("Book shelf", "4")]
PENDING = [("Refund over the limit", "Avery Clerk"), ("Price override", "Avery Clerk")]
PROTECTED = ("/home", "/orders", "/approvals")


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def page(title: str, body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(title)}</title></head>"
            f"<body>{body}</body></html>")  # fmt: skip


def signin_page(expired: bool = False, error: bool = False) -> str:
    notice = "<p id='expiredNotice'>Your session has ended. Sign in again.</p>" if expired else ""
    err = "<p id='signinError'>The user name or password is not right.</p>" if error else ""
    return page("Sign in", f"<h1 id='signinTitle'>Sign in</h1>{notice}{err}"
                "<form id='signinForm' method='post' action='/signin'>"
                "<label for='username'>User name</label><input id='username' name='username'>"
                "<label for='password'>Password</label>"
                "<input id='password' name='password' type='password'>"
                "<button id='signIn' type='submit'>Sign in</button></form>")  # fmt: skip


def frame(user: dict, title: str, body: str, approvals_link: bool) -> str:
    link = "<a id='navApprovals' href='/approvals'>Approvals</a>" if approvals_link else ""
    return page(title, "<nav id='menu'><a id='navHome' href='/home'>Home</a>"
                f"<a id='navOrders' href='/orders'>Orders</a>{link}"
                "<form id='signoutForm' method='post' action='/signout'>"
                "<button id='signOut' type='submit'>Sign out</button></form></nav>"
                f"<p id='who'>Signed in as {esc(user['name'])} ({esc(user['role'])})</p>"
                f"{body}")  # fmt: skip


def table(table_id: str, heads: tuple[str, str], rows) -> str:
    cells = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in rows)
    return (f"<table id='{table_id}'><thead><tr><th>{heads[0]}</th><th>{heads[1]}</th></tr>"
            f"</thead><tbody>{cells}</tbody></table>")  # fmt: skip


@dataclass
class Running:
    base: str = ""
    faults: frozenset = frozenset()
    session_views: int | None = None
    server: ThreadingHTTPServer | None = None
    sessions: dict = field(default_factory=dict)
    hits: list = field(default_factory=list)  # (method, path, status) in the order answered

    def expire_sessions(self) -> None:
        """Every session ends now (the cookies stay in the browsers that hold them)."""
        self.sessions.clear()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _handler(run: Running):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, body: str, status=200, headers=None):
            data = body.encode("utf-8")
            run.hits.append((self.command, urlsplit(self.path).path, status))
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, to: str, status=302, headers=None):
            self._send("", status, {"Location": to, **(headers or {})})

        def _sid(self) -> str | None:
            for part in (self.headers.get("Cookie") or "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == COOKIE and value:
                    return value
            return None

        def do_GET(self):  # noqa: N802
            url = urlsplit(self.path)
            path = url.path
            if path in ("/", "/signin"):
                return self._send(signin_page(expired="expired=1" in url.query))
            if path not in PROTECTED:
                return self._send(page("Not found", "<h1 id='notFound'>Not found</h1>"), 404)
            sid = self._sid()
            session = run.sessions.get(sid)
            if session is not None and run.session_views is not None:
                session["views"] += 1
                if session["views"] > run.session_views and "no_expiry" not in run.faults:
                    del run.sessions[sid]
                    session = None
            if session is None:
                return self._redirect("/signin?expired=1" if sid else "/signin")
            user = USERS[session["user"]]
            supervisor = user["role"] == "supervisor"
            menu = supervisor or "menu_for_all" in run.faults
            if path == "/home":
                return self._send(frame(user, "Home", "<h1 id='homeTitle'>Home</h1>", menu))
            if path == "/orders":
                body = "<h1 id='ordersTitle'>Orders</h1>" + table(
                    "orders", ("Product", "Quantity"), ORDERS)  # fmt: skip
                return self._send(frame(user, "Orders", body, menu))
            if not supervisor and "no_role_check" not in run.faults:
                body = ("<h1 id='refused'>Not allowed</h1><p id='refusedWhy'>This page is for "
                        "supervisors.</p><a id='backHome' href='/home'>Back to home</a>")  # fmt: skip
                return self._send(frame(user, "Not allowed", body, menu), 403)
            body = ("<h1 id='approvalsTitle'>Approvals</h1>"
                    + table("pending", ("Request", "Asked by"), PENDING)
                    + "<button id='approveAll' type='button'>Approve all</button>")  # fmt: skip
            return self._send(frame(user, "Approvals", body, menu))

        def do_POST(self):  # noqa: N802
            path = urlsplit(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(length).decode("utf-8") if length else "")
            if path == "/signin":
                name = (form.get("username") or [""])[0]
                user = USERS.get(name)
                if user is None or (form.get("password") or [""])[0] != user["password"]:
                    return self._send(signin_page(error=True), 401)
                sid = secrets.token_hex(16)
                run.sessions[sid] = {"user": name, "views": 0}
                return self._redirect("/home", 303,
                                      {"Set-Cookie": f"{COOKIE}={sid}; HttpOnly; Path=/"})  # fmt: skip
            if path == "/signout":
                if "signout_keeps_session" in run.faults:
                    return self._redirect("/signin", 303)
                run.sessions.pop(self._sid(), None)
                return self._redirect("/signin", 303,
                                      {"Set-Cookie": f"{COOKIE}=; Max-Age=0; Path=/"})  # fmt: skip
            return self._send(page("Not found", "<h1 id='notFound'>Not found</h1>"), 404)

    return Handler


def serve(faults=(), session_views: int | None = None, port: int = 0) -> Running:
    unknown = set(faults) - set(FAULTS)
    if unknown:
        raise ValueError(f"unknown fault(s): {sorted(unknown)}")
    run = Running(faults=frozenset(faults), session_views=session_views)
    run.server = ThreadingHTTPServer(("127.0.0.1", port), _handler(run))
    threading.Thread(target=run.server.serve_forever, daemon=True).start()
    run.base = f"http://127.0.0.1:{run.server.server_address[1]}"
    return run


if __name__ == "__main__":
    running = serve(port=8731)
    print(running.base, "(Ctrl+C stops it)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        running.stop()
