"""A small order desk for the tests of the link check (labctl.linkcheck). Synthetic data only;
served from 127.0.0.1. Every request it receives is logged (method, address, cookie names), so
a test can prove what was and was not asked for.

- `/login`: a sign-in form (POST /login sets the cookie `shop_session`, then 303 to /orders).
- `/orders` (signed in, else 302 to /login): a list of orders and links of every kind the check
  meets: a recorded page, an unrecorded page (`/history`, a form and a table of data), a page
  that redirects to sign-in without a session (`/account`), an old address redirecting to a
  recorded screen (`/old-orders` to `/orders/1007`), a 404, a 500, a delete link, a sign-out link, another site, a PDF download,
  a mail address, a token in a query, a POST form, a GET form, a popup by `window.open`, an
  image-map area, a page answering 429 and one listed after it.
- `/orders/<n>`: one order.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

COOKIE = "shop_session"
GOOD_SESSION = "fixture-session-7Hq2Lw9Zk4Rt8Vb3"  # what a test's storage state carries
CUSTOMERS = ["Ingrid Solvang", "Bertil Rask", "Maren Okafor"]


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title></head>"
            f"<body>{body}</body></html>")  # fmt: skip


def login_page() -> str:
    return _page(
        "Sign in",
        """
<h1>Sign in</h1>
<form id="loginForm" method="post" action="/login">
  <label for="username">User name</label><input id="username" name="username">
  <label for="password">Password</label><input id="password" name="password" type="password">
  <button id="signIn" type="submit">Sign in</button>
</form>""",
    )


def orders_page() -> str:
    rows = "".join(
        f"<tr><td class='order-no'>ORD-{1007 + i}</td><td class='customer'>{name}</td>"
        f"<td><a class='order-link' href='/orders/{1007 + i}'>Open</a></td>"
        f"<td><a class='delete-link' href='/orders/{1007 + i}/delete'>Delete</a></td></tr>"
        for i, name in enumerate(CUSTOMERS)
    )
    return _page(
        "Orders",
        f"""
<nav id="menu">
  <a id="navHistory" href="/history">Order history</a>
  <a id="navAccount" href="/account">My account</a>
  <a id="navOld" href="/old-orders">Old list</a>
  <a id="navGone" href="/gone">Archive help</a>
  <a id="navBroken" href="/broken">Statistics</a>
  <a id="signOut" href="/logout">Sign out</a>
  <a id="partner" href="https://partner.example.test/offers">Partner offers</a>
  <a id="export" href="/export/orders.pdf">Export</a>
  <a id="mail" href="mailto:help@shop-fixture.test">Write to us</a>
  <a id="statement" href="/statements?session=Zx81Kq7Lm2Np9Rt4Vw6Yb3Hc">Statement</a>
</nav>
<h1>Orders</h1>
<form id="search" method="get" action="search"><label for="q">Find</label>
  <input id="q" name="q"><button type="submit">Find</button></form>
<form id="bulk" method="post" action="/orders/bulk"><button type="submit">Mark all</button></form>
<button id="help" onclick="window.open('/help/popup', 'help')">Help</button>
<img src="/static/map.png" usemap="#m" alt="map"><map name="m"><area href="/map/north" alt="North"></map>
<table id="orders"><thead><tr><th>Order</th><th>Customer</th><th></th><th></th></tr></thead>
<tbody>{rows}</tbody></table>
<a id="busy" href="/busy">Busy page</a>
<a id="reports" href="/reports">Reports</a>""",
    )


def order_page(n: str) -> str:
    return _page(
        "Order",
        f"""<h1>Order</h1><p><a id="backToList" href="/orders">Back</a></p>
<dl><dt>Number</dt><dd>ORD-{n}</dd><dt>Customer</dt><dd>{CUSTOMERS[0]}</dd></dl>""",
    )


def history_page() -> str:
    return _page(
        "Order history",
        """
<h1>Order history</h1>
<form id="historyFilter" method="get" action="/history">
  <label for="from">From date</label><input id="from" name="from" type="date">
  <label for="customer">Customer</label><input id="customer" name="customer">
  <button id="showHistory" type="submit">Show</button>
</form>
<table id="historyTable"><thead><tr><th>Order</th><th>Customer</th><th>Total</th></tr></thead>
<tbody>
<tr><td>ORD-5521</td><td>Ingrid Solvang</td><td>1,234.50</td></tr>
<tr><td>ORD-5522</td><td>Torvald Quist</td><td>88.20</td></tr>
</tbody></table>""",
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # quiet
        pass

    def _log(self) -> None:
        cookie = self.headers.get("Cookie") or ""
        self.server.log.append({  # type: ignore[attr-defined]
            "method": self.command, "path": self.path, "cookie": cookie,
            "body": int(self.headers.get("Content-Length") or 0),
            "ua": self.headers.get("User-Agent") or ""})  # fmt: skip

    def _session(self) -> bool:
        cookie = self.headers.get("Cookie") or ""
        return f"{COOKIE}={GOOD_SESSION}" in cookie or f"{COOKIE}=rec-" in cookie

    def _send(self, status: int, html: str | None = None, headers: dict | None = None,
              ctype: str = "text/html; charset=utf-8") -> None:  # fmt: skip
        body = (html or "").encode("utf-8")
        self.send_response(status)
        if html is not None:
            self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _route(self) -> None:
        self._log()
        path = urlsplit(self.path).path
        signed = self._session()
        if path == "/login":
            if self.command == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                self._send(303, "", {"Location": "/orders",
                                     "Set-Cookie": f"{COOKIE}=rec-1; Path=/"})  # fmt: skip
                return
            self._send(200, login_page())
            return
        if self.command not in ("GET", "HEAD"):
            self._send(405, "")
            return
        if path == "/orders":
            if not signed:
                self._send(302, "", {"Location": "/login?next=/orders"})
                return
            self._send(200, orders_page())
        elif path.startswith("/orders/") and path.count("/") == 2:
            self._send(200, order_page(path.rsplit("/", 1)[1]))
        elif path == "/history":
            self._send(200, history_page())
        elif path == "/account":
            if signed:
                self._send(200, _page("My account", "<h1>My account</h1><p>Settings</p>"))
            else:
                self._send(302, "", {"Location": "/login?next=/account"})
        elif path == "/old-orders":
            self._send(301, "", {"Location": "/orders/1007"})
        elif path == "/broken":
            self._send(500, _page("Error", "<h1>Something went wrong</h1>"))
        elif path == "/search":
            self._send(200, _page("Search", "<h1>Search</h1><form><input name='q'></form>"))
        elif path == "/help/popup":
            if self.command == "HEAD":
                self._send(405, "")  # HEAD refused: the check falls back to GET
                return
            self._send(200, _page("Help", "<h1>Help</h1><p>How to use the order desk.</p>"))
        elif path == "/busy":
            self._send(429, "", {"Retry-After": "60"})
        elif path == "/reports":
            self._send(200, _page("Reports", "<h1>Reports</h1>"))
        else:
            self._send(404, _page("Not found", "<h1>Not found</h1>"))

    def do_GET(self):  # noqa: N802
        self._route()

    def do_HEAD(self):  # noqa: N802
        self._route()

    def do_POST(self):  # noqa: N802
        self._route()

    def do_DELETE(self):  # noqa: N802
        self._route()


class Running:
    def __init__(self, server: ThreadingHTTPServer) -> None:
        self.server = server
        self.url = f"http://127.0.0.1:{server.server_address[1]}"
        self.log: list[dict] = server.log  # type: ignore[attr-defined]

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def serve(port: int = 0) -> Running:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.log = []  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return Running(server)
