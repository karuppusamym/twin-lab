"""Two small applications on two origins, and a widget on a third, written for the multi-app
tests. Synthetic data only; everything is served from 127.0.0.1 and kept in memory.

- `orders` (app A): sign-in; a search page (by order number or customer name, a results table
  with paging, filled by a JSON API); a new-order form over two pages (customer, then items with
  a select, a checkbox, a date and a textarea) with validation errors; a confirmation page with
  the saved order and its generated number; "Change this order" (the same two pages); and a link
  "Open in billing" that opens app B in a new tab. Markup convention: element ids.
- `billing` (app B): a different look and different conventions (BEM classes, `name` and
  `data-testid` attributes, labels as aria-label): an overview page embedding an exchange-rate
  widget from the third origin in an iframe, a lookup page where an order reference is typed, an
  invoice form, and a page that shows the saved invoice from a JSON call (also reached by looking
  the order up again).
- `widget`: the exchange-rate widget (an HTML page and a JSON call).

Run them by hand with `python apps.py` (prints the three addresses; Ctrl+C stops them). Tests use
`serve()`, which returns the three servers on free ports; `stop()` shuts them down. A later step
rebuilds these apps from their build packages and compares: keep their behaviour stable.
"""

from __future__ import annotations

import html
import json
import re
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PAGE_SIZE = 5
PRODUCTS = {"lamp": ("Desk lamp", 49.95), "chair": ("Office chair", 189.0),
            "shelf": ("Book shelf", 74.5)}  # fmt: skip
TERMS = {"NET15": "15 days", "NET30": "30 days", "NET60": "60 days"}


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


# --- shared server plumbing ------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    """Routes by (method, path regex) on the server's `app` object."""

    server_version = "FixtureHTTP/1.0"

    def log_message(self, *args):  # quiet
        pass

    def _dispatch(self, method: str) -> None:
        app = self.server.app  # type: ignore[attr-defined]
        parts = urlsplit(self.path)
        self.query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
        self.form = {}
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8") if length else ""
            self.form = {k: v[-1] for k, v in parse_qs(body, keep_blank_values=True).items()}
        self.cookies = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name:
                self.cookies[name] = value
        for m, pattern, fn in app.routes:
            match = re.fullmatch(pattern, parts.path)
            if m == method and match:
                fn(self, *match.groups())
                return
        self.send_page("<h1>Not found</h1>", status=404)

    def do_GET(self):  # noqa: N802
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")

    def send_body(self, body: bytes, ctype: str, status: int = 200, headers=None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_page(self, text: str, status: int = 200, headers=None) -> None:
        self.send_body(text.encode("utf-8"), "text/html; charset=utf-8", status, headers)

    def send_json(self, data, status: int = 200) -> None:
        self.send_body(json.dumps(data).encode("utf-8"), "application/json", status)

    def redirect(self, to: str, headers=None) -> None:
        self.send_response(303)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()


class App:
    cookie = "sid"

    def __init__(self) -> None:
        self.routes: list[tuple[str, str, object]] = []
        self.sessions: dict[str, dict] = {}
        self.lock = threading.Lock()

    def route(self, method: str, pattern: str):
        def deco(fn):
            self.routes.append((method, pattern, fn))
            return fn

        return deco

    def session(self, req: Handler, create: bool = False) -> dict | None:
        sid = req.cookies.get(self.cookie)
        if sid in self.sessions:
            return self.sessions[sid]
        if not create:
            return None
        sid = secrets.token_hex(8)
        self.sessions[sid] = {"_id": sid}
        return self.sessions[sid]


# --- app A: orders ----------------------------------------------------------------------------------

ORDERS_STYLE = """
body { font-family: Arial, sans-serif; margin: 0; background: #f6f6f6; }
#topbar { background: #23395d; color: #fff; padding: 8px 16px; display: flex; gap: 24px; }
#topbar a { color: #fff; margin-right: 12px; }
#content { padding: 16px; max-width: 760px; }
label { display: block; margin-top: 8px; }
.error, .field-error { color: #b00020; }
table { border-collapse: collapse; margin-top: 12px; }
td, th { border: 1px solid #ccc; padding: 4px 8px; }
"""

SEARCH_JS = r"""
(function () {
  const form = document.getElementById("searchForm");
  const error = document.getElementById("searchError");
  const body = document.querySelector("#results tbody");
  const none = document.getElementById("noResults");
  const info = document.getElementById("pageInfo");
  const prev = document.getElementById("prevPage"), next = document.getElementById("nextPage");
  let page = 1, pages = 0;
  function cell(text, cls) { const td = document.createElement("td"); if (cls) td.className = cls; td.textContent = text; return td; }
  function load() {
    const by = document.getElementById("searchBy").value;
    const q = document.getElementById("q").value.trim();
    return fetch("/api/orders?by=" + encodeURIComponent(by) + "&q=" + encodeURIComponent(q) + "&page=" + page)
      .then((r) => r.json()).then((data) => {
        body.textContent = "";
        for (const o of data.items) {
          const tr = document.createElement("tr");
          tr.className = "order-row";
          tr.append(cell(o.number, "order-no"), cell(o.customer), cell(o.order_date), cell(o.status), cell(o.total.toFixed(2)));
          body.append(tr);
        }
        pages = data.pages;
        none.hidden = data.total > 0;
        info.textContent = data.total ? "Page " + data.page + " of " + data.pages : "";
        prev.disabled = page <= 1;
        next.disabled = page >= pages;
      });
  }
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!document.getElementById("q").value.trim()) {
      error.textContent = "Enter an order number or a customer name.";
      error.hidden = false;
      return;
    }
    error.hidden = true;
    page = 1;
    try { localStorage.setItem("orders.lastSearch", JSON.stringify({by: document.getElementById("searchBy").value, page: 1})); } catch (e) {}
    load();
  });
  prev.addEventListener("click", () => { if (page > 1) { page--; load(); } });
  next.addEventListener("click", () => { if (page < pages) { page++; load(); } });
})();
"""


@dataclass
class Order:
    number: str
    customer: str
    email: str
    phone: str
    product: str
    quantity: int
    delivery: str
    gift: bool
    notes: str
    order_date: str
    status: str = "NEW"

    @property
    def total(self) -> float:
        return round(PRODUCTS[self.product][1] * self.quantity, 2)


def seed_orders() -> dict[str, Order]:
    out = {}
    names = ["Hartley", "Lindqvist"]
    for n in range(1, 26):
        name = names[(n - 1) % 2] if n < 25 else "Okafor"
        number = f"ORD-{1000 + n}"
        product = list(PRODUCTS)[n % 3]
        out[number] = Order(number, f"{['Ann', 'Ben', 'Cara', 'Dev'][n % 4]} {name}",
                            f"c{n}@example.test", f"0100 000 {n:04d}", product, 1 + n % 4,
                            f"2026-12-{1 + n % 27:02d}", n % 2 == 0, "", f"2026-10-{1 + n % 28:02d}",
                            ["NEW", "PAID", "SHIPPED"][n % 3])  # fmt: skip
    return out


class Orders(App):
    cookie = "osid"

    def __init__(self, billing_url: str = "") -> None:
        super().__init__()
        self.billing_url = billing_url
        self.orders = seed_orders()
        r = self.route
        r("GET", "/")(lambda req: req.redirect("/login"))
        r("GET", "/login")(lambda req: self.login_page(req))
        r("POST", "/login")(self.login)
        r("GET", "/logout")(self.logout)
        r("GET", "/static/search.js")(
            lambda req: req.send_body(SEARCH_JS.encode(), "text/javascript")
        )
        r("GET", "/api/orders")(self.api_orders)
        r("GET", "/api/health")(lambda req: req.send_json({"ok": True}))
        r("GET", "/orders/search")(self.search_page)
        r("GET", "/orders/(new|edit)")(self.start_form)
        r("GET", "/orders/(new|edit)/customer")(self.customer_page)
        r("POST", "/orders/(new|edit)/customer")(self.customer_post)
        r("GET", "/orders/(new|edit)/items")(self.items_page)
        r("POST", "/orders/(new|edit)/items")(self.items_post)
        r("GET", "/orders/saved")(self.saved_page)
        r("GET", "/customers")(self.customers_page)
        r("GET", "/api/customers")(self.api_customers)

    # pages
    def page(self, title: str, body: str, signed_in: bool = True) -> str:
        nav = ('<nav id="mainNav"><a id="navSearch" href="/orders/search">Search</a>'
               '<a id="newOrder" href="/orders/new">New order</a>'
               '<a id="navCustomers" href="/customers">Customers</a>'
               '<a id="signOut" href="/logout">Sign out</a></nav>') if signed_in else ""  # fmt: skip
        return (f"<!doctype html><html><head><meta charset='utf-8'><title>Orders - {esc(title)}"
                f"</title><style>{ORDERS_STYLE}</style></head><body><header id='topbar'>"
                f"<span class='brand'>Orders</span>{nav}</header><main id='content'>{body}"
                "</main></body></html>")  # fmt: skip

    def guard(self, req) -> dict | None:
        s = self.session(req)
        if s is None or not s.get("user"):
            req.redirect("/login")
            return None
        return s

    def login_page(self, req, error: str = "", user: str = "") -> None:
        err = f'<div id="loginError" class="error">{esc(error)}</div>' if error else ""
        body = (f"<h1>Sign in</h1>{err}<form id='loginForm' method='post' action='/login'>"
                f"<label for='username'>User name</label><input id='username' name='username' value='{esc(user)}'>"
                "<label for='password'>Password</label><input id='password' name='password' type='password'>"
                "<button id='signIn' type='submit'>Sign in</button></form>")  # fmt: skip
        req.send_page(self.page("Sign in", body, signed_in=False))

    def login(self, req) -> None:
        user, password = req.form.get("username", "").strip(), req.form.get("password", "")
        if not user or not password:
            self.login_page(req, "Give your user name and your password.", user)
            return
        s = self.session(req, create=True)
        s["user"] = user
        req.redirect(
            "/orders/search", {"Set-Cookie": f"{self.cookie}={s['_id']}; HttpOnly; Path=/"}
        )

    def logout(self, req) -> None:
        s = self.session(req)
        if s:
            s.pop("user", None)
        req.redirect("/login")

    def search_page(self, req) -> None:
        if self.guard(req) is None:
            return
        body = ("<h1>Find orders</h1><form id='searchForm'>"
                "<label for='searchBy'>Search by</label><select id='searchBy' name='by'>"
                "<option value='number'>Order number</option><option value='customer'>Customer name</option></select>"
                "<label for='q'>Search for</label><input id='q' name='q'>"
                "<button id='searchBtn' type='submit'>Search</button></form>"
                "<div id='searchError' class='error' hidden></div>"
                "<table id='results'><thead><tr><th>Number</th><th>Customer</th><th>Order date</th>"
                "<th>Status</th><th>Total</th></tr></thead><tbody></tbody></table>"
                "<p id='noResults' hidden>No orders found.</p>"
                "<div id='pager'><button id='prevPage' type='button' disabled>Previous</button>"
                "<span id='pageInfo'></span><button id='nextPage' type='button' disabled>Next</button></div>"
                "<script src='/static/search.js'></script>")  # fmt: skip
        req.send_page(self.page("Search", body))

    def api_orders(self, req) -> None:
        if self.session(req) is None or not self.session(req).get("user"):
            req.send_json({"error": "sign in"}, 401)
            return
        by, q = req.query.get("by", "number"), req.query.get("q", "").strip().lower()
        page = max(1, int(req.query.get("page") or 1))
        hits = [o for o in self.orders.values()
                if (o.number.lower() == q if by == "number" else q in o.customer.lower())]  # fmt: skip
        pages = (len(hits) + PAGE_SIZE - 1) // PAGE_SIZE
        items = [{"number": o.number, "customer": o.customer, "order_date": o.order_date,
                  "status": o.status, "total": o.total}
                 for o in hits[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]]  # fmt: skip
        req.send_json({"items": items, "page": page, "pages": pages, "total": len(hits)})

    def customers_page(self, req) -> None:
        if self.guard(req) is None:
            return
        body = ("<h1>Customers</h1><table id='customerList'><thead><tr><th>Name</th><th>E-mail</th>"
                "<th>Orders</th></tr></thead><tbody></tbody></table>"
                "<script>fetch('/api/customers').then(r => r.json()).then(d => {"
                "const b = document.querySelector('#customerList tbody');"
                "for (const c of d.customers) { const tr = document.createElement('tr'); tr.className = 'customer-row';"
                "for (const v of [c.name, c.email, c.orders]) { const td = document.createElement('td'); td.textContent = v; tr.append(td); }"
                "b.append(tr); } });</script>")  # fmt: skip
        req.send_page(self.page("Customers", body))

    def api_customers(self, req) -> None:
        if self.session(req) is None or not self.session(req).get("user"):
            req.send_json({"error": "sign in"}, 401)
            return
        seen: dict[str, dict] = {}
        for o in self.orders.values():
            c = seen.setdefault(o.customer, {"name": o.customer, "email": o.email, "orders": 0})
            c["orders"] += 1
        req.send_json({"customers": sorted(seen.values(), key=lambda c: c["name"])[:20]})

    def start_form(self, req, mode: str) -> None:
        s = self.guard(req)
        if s is None:
            return
        if mode == "edit":
            saved = self.orders.get(s.get("saved") or "")
            if saved is None:
                req.redirect("/orders/new")
                return
            s["draft"] = dict(saved.__dict__)
        else:
            s["draft"] = {}
        self.customer_page(req, mode)

    def customer_page(self, req, mode: str, errors: dict | None = None) -> None:
        s = self.guard(req)
        if s is None:
            return
        d, errors = s.get("draft") or {}, errors or {}

        def err(key):
            return (
                f"<span class='field-error' id='{key}Error'>{esc(errors[key])}</span>"
                if key in errors
                else ""
            )

        title = "New order" if mode == "new" else "Change order"
        body = (f"<h1>{title}: customer</h1><p class='steps'>Step 1 of 2</p>"
                f"<form id='customerForm' method='post' action='/orders/{mode}/customer' novalidate>"
                "<fieldset><legend>Customer</legend>"
                f"<label for='customerName'>Customer name</label><input id='customerName' name='customer_name' value='{esc(d.get('customer'))}'>{err('customerName')}"
                f"<label for='customerEmail'>E-mail</label><input id='customerEmail' name='customer_email' type='email' value='{esc(d.get('email'))}'>{err('customerEmail')}"
                f"<label for='customerPhone'>Phone</label><input id='customerPhone' name='customer_phone' type='tel' value='{esc(d.get('phone'))}'>"
                "</fieldset><button id='toItems' type='submit'>Next: items</button></form>")  # fmt: skip
        req.send_page(self.page(title, body))

    def customer_post(self, req, mode: str) -> None:
        s = self.guard(req)
        if s is None:
            return
        f = req.form
        d = s.setdefault("draft", {})
        d.update(customer=f.get("customer_name", "").strip(), email=f.get("customer_email", "").strip(),
                 phone=f.get("customer_phone", "").strip())  # fmt: skip
        errors = {}
        if not d["customer"]:
            errors["customerName"] = "Give the customer's name."
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", d["email"]):
            errors["customerEmail"] = "Give an e-mail address such as name@example.test."
        if errors:
            self.customer_page(req, mode, errors)
            return
        req.redirect(f"/orders/{mode}/items")

    def items_page(self, req, mode: str, errors: dict | None = None) -> None:
        s = self.guard(req)
        if s is None:
            return
        d, errors = s.get("draft") or {}, errors or {}

        def err(key):
            return (
                f"<span class='field-error' id='{key}Error'>{esc(errors[key])}</span>"
                if key in errors
                else ""
            )

        options = "<option value=''>Choose a product</option>" + "".join(
            f"<option value='{k}'{' selected' if d.get('product') == k else ''}>{esc(v[0])}</option>"
            for k, v in PRODUCTS.items())  # fmt: skip
        checked = " checked" if d.get("gift") else ""
        title = "New order" if mode == "new" else "Change order"
        body = (f"<h1>{title}: items</h1><p class='steps'>Step 2 of 2</p>"
                f"<form id='itemsForm' method='post' action='/orders/{mode}/items' novalidate>"
                f"<label for='product'>Product</label><select id='product' name='product'>{options}</select>{err('product')}"
                f"<label for='quantity'>Quantity</label><input id='quantity' name='quantity' type='number' min='1' max='99' value='{esc(d.get('quantity'))}'>{err('quantity')}"
                f"<label for='deliveryDate'>Delivery date</label><input id='deliveryDate' name='delivery_date' type='date' value='{esc(d.get('delivery'))}'>{err('deliveryDate')}"
                f"<label class='check'><input id='giftWrap' name='gift_wrap' type='checkbox' value='yes'{checked}> Gift wrap</label>"
                f"<label for='notes'>Notes</label><textarea id='notes' name='notes'>{esc(d.get('notes'))}</textarea>"
                f"<p><a id='backToCustomer' href='/orders/{mode}/customer'>Back</a> "
                "<button id='saveOrder' type='submit'>Save order</button></p></form>")  # fmt: skip
        req.send_page(self.page(title, body))

    def items_post(self, req, mode: str) -> None:
        import datetime as dt

        s = self.guard(req)
        if s is None:
            return
        f = req.form
        d = s.setdefault("draft", {})
        d.update(product=f.get("product", ""), delivery=f.get("delivery_date", ""),
                 gift=f.get("gift_wrap") == "yes", notes=f.get("notes", ""))  # fmt: skip
        errors = {}
        if d["product"] not in PRODUCTS:
            errors["product"] = "Choose a product."
        try:
            qty = int(f.get("quantity", ""))
        except ValueError:
            qty = 0
        d["quantity"] = f.get("quantity", "")
        if not 1 <= qty <= 99:
            errors["quantity"] = "The quantity is a number from 1 to 99."
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["delivery"]):
            errors["deliveryDate"] = "Choose a delivery date."
        if errors:
            self.items_page(req, mode, errors)
            return
        with self.lock:
            if mode == "edit" and d.get("number") in self.orders:
                number, date, status = d["number"], d["order_date"], d["status"]
            else:
                number = f"ORD-{1000 + len(self.orders) + 1}"
                date, status = dt.date.today().isoformat(), "NEW"
            self.orders[number] = Order(number, d["customer"], d["email"], d["phone"], d["product"],
                                        qty, d["delivery"], d["gift"], d["notes"], date, status)  # fmt: skip
        s["saved"] = number
        s.pop("draft", None)
        req.redirect("/orders/saved")

    def saved_page(self, req) -> None:
        s = self.guard(req)
        if s is None:
            return
        o = self.orders.get(s.get("saved") or "")
        if o is None:
            req.redirect("/orders/search")
            return
        rows = [("Customer", "savedCustomer", o.customer), ("E-mail", "savedEmail", o.email),
                ("Phone", "savedPhone", o.phone), ("Product", "savedProduct", PRODUCTS[o.product][0]),
                ("Quantity", "savedQuantity", o.quantity), ("Delivery date", "savedDelivery", o.delivery),
                ("Gift wrap", "savedGift", "yes" if o.gift else "no"), ("Notes", "savedNotes", o.notes),
                ("Total", "savedTotal", f"{o.total:.2f}")]  # fmt: skip
        dl = "".join(f"<dt>{k}</dt><dd id='{i}'>{esc(v)}</dd>" for k, i, v in rows)
        body = (f"<h1>Order saved</h1><p id='savedMessage'>Order <b id='savedNumber'>{esc(o.number)}</b> "
                f"was saved.</p><dl id='savedOrder'>{dl}</dl>"
                f"<p><a id='openBilling' href='{esc(self.billing_url)}/billing/start' target='_blank'>Open in billing</a> "
                "<a id='editSaved' href='/orders/edit'>Change this order</a> "
                "<a id='searchAgain' href='/orders/search'>Back to search</a></p>")  # fmt: skip
        req.send_page(self.page("Order saved", body))


# --- app B: billing ---------------------------------------------------------------------------------

BILLING_STYLE = """
body.billing { font-family: Georgia, serif; margin: 0; color: #2b2b2b; background: #fffdf7; }
.shell { display: grid; grid-template-columns: 180px 1fr; min-height: 100vh; }
.shell__nav { background: #6b4226; padding: 20px 12px; }
.nav__item { display: block; color: #fff8e7; margin-bottom: 10px; text-decoration: none; }
.shell__main { padding: 24px 32px; }
.field { margin: 10px 0; display: flex; flex-direction: column; max-width: 360px; }
.field__label { font-size: 13px; text-transform: uppercase; letter-spacing: .05em; }
.btn { background: #6b4226; color: #fff; border: 0; padding: 6px 14px; border-radius: 14px; }
.notice--error { background: #fde8e8; padding: 6px 10px; }
.summary__row { display: flex; gap: 12px; border-bottom: 1px dotted #aaa; padding: 4px 0; }
.summary__key { width: 140px; font-weight: bold; }
"""


@dataclass
class Invoice:
    number: str
    order: str
    payer: str
    terms: str
    amount: float
    vat: bool
    memo: str

    @property
    def total(self) -> float:
        return round(self.amount * (1.2 if self.vat else 1.0), 2)


class Billing(App):
    cookie = "bsid"

    def __init__(self, widget_url: str = "") -> None:
        super().__init__()
        self.widget_url = widget_url
        self.invoices: dict[str, Invoice] = {}  # by order reference
        r = self.route
        r("GET", "/")(lambda req: req.redirect("/billing/start"))
        r("GET", "/billing/start")(self.start)
        r("GET", "/billing/lookup")(lambda req: self.lookup_page(req))
        r("POST", "/billing/lookup")(self.lookup)
        r("GET", "/billing/account")(lambda req: self.account_page(req))
        r("POST", "/billing/account")(self.account_post)
        r("GET", "/billing/invoice")(self.invoice_page)
        r("GET", "/billing/api/invoices")(self.api_invoices)
        r("GET", "/billing/health")(lambda req: req.send_json({"status": "up"}))

    def page(self, req, title: str, body: str) -> None:
        s = self.session(req)
        headers = {}
        if s is None:
            s = self.session(req, create=True)
            headers["Set-Cookie"] = f"{self.cookie}={s['_id']}; Path=/"
        text = (f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(title)} · Billing</title>"
                f"<style>{BILLING_STYLE}</style></head><body class='billing'><div class='shell'>"
                "<aside class='shell__nav'><a class='nav__item' data-testid='nav-start' href='/billing/start'>Overview</a>"
                "<a class='nav__item' data-testid='nav-lookup' href='/billing/lookup'>Find an order</a></aside>"
                f"<section class='shell__main'>{body}</section></div>"
                "<script>try{sessionStorage.setItem('billing.lastView', JSON.stringify({page: location.pathname, at: Date.now()}));}catch(e){}</script>"
                "</body></html>")  # fmt: skip
        req.send_page(text, headers=headers)

    def start(self, req) -> None:
        body = ("<h2 class='page__title'>Billing overview</h2>"
                "<p class='lede'>Look an order up to write its invoice, or to see the invoice written for it.</p>"
                f"<div class='rates'><iframe id='fxWidget' title='Exchange rates' src='{esc(self.widget_url)}/widget/rates' "
                "width='320' height='140'></iframe></div>")  # fmt: skip
        self.page(req, "Overview", body)

    def lookup_page(self, req, error: str = "", value: str = "") -> None:
        note = f"<p class='notice notice--error' role='alert'>{esc(error)}</p>" if error else ""
        body = (f"<h2 class='page__title'>Find an order</h2>{note}"
                "<form class='lookup' name='lookup' method='post' action='/billing/lookup'>"
                "<div class='field'><span class='field__label'>Order reference</span>"
                f"<input class='field__input' name='orderRef' data-testid='order-ref' aria-label='Order reference' value='{esc(value)}'></div>"
                "<button class='btn btn--primary' data-testid='find' type='submit'>Find</button></form>")  # fmt: skip
        self.page(req, "Find an order", body)

    def lookup(self, req) -> None:
        ref = req.form.get("orderRef", "").strip().upper()
        if not re.fullmatch(r"ORD-\d{4}", ref):
            self.lookup_page(req, "An order reference looks like ORD-0000.", ref)
            return
        s = self.session(req, create=True)
        s["ref"] = ref
        req.redirect("/billing/invoice" if ref in self.invoices else "/billing/account")

    def account_page(self, req, errors: dict | None = None, form: dict | None = None) -> None:
        s = self.session(req)
        if not s or not s.get("ref"):
            req.redirect("/billing/lookup")
            return
        errors, form = errors or {}, form or {}
        notes = "".join(
            f"<p class='notice notice--error' role='alert'>{esc(e)}</p>" for e in errors.values()
        )
        terms = "".join(f"<option value='{k}'{' selected' if form.get('terms') == k else ''}>{v}</option>"
                        for k, v in TERMS.items())  # fmt: skip
        vat = " checked" if form.get("vat") else ""
        body = (f"<h2 class='page__title'>New invoice</h2><p class='muted'>For order <span class='ref'>{esc(s['ref'])}</span></p>{notes}"
                "<form class='invoice' name='invoice' method='post' action='/billing/account'>"
                f"<div class='field'><span class='field__label'>Payer</span><input class='field__input' name='payer' aria-label='Payer' value='{esc(form.get('payer'))}'></div>"
                f"<div class='field'><span class='field__label'>Payment terms</span><select class='field__input' name='terms' aria-label='Payment terms'>{terms}</select></div>"
                f"<div class='field'><span class='field__label'>Amount</span><input class='field__input' name='amount' inputmode='decimal' aria-label='Amount' value='{esc(form.get('amount'))}'></div>"
                f"<label class='check'><input type='checkbox' name='vat' value='1'{vat}> Add VAT</label>"
                f"<div class='field'><span class='field__label'>Memo</span><textarea class='field__input' name='memo' aria-label='Memo'>{esc(form.get('memo'))}</textarea></div>"
                "<button class='btn btn--primary' data-testid='save-invoice' type='submit'>Save invoice</button></form>")  # fmt: skip
        self.page(req, "New invoice", body)

    def account_post(self, req) -> None:
        s = self.session(req)
        if not s or not s.get("ref"):
            req.redirect("/billing/lookup")
            return
        f = req.form
        errors = {}
        if not f.get("payer", "").strip():
            errors["payer"] = "Name the payer."
        try:
            amount = float(f.get("amount", "").replace(",", "."))
        except ValueError:
            amount = -1
        if amount <= 0:
            errors["amount"] = "The amount is a number greater than 0, such as 120.50."
        if f.get("terms") not in TERMS:
            errors["terms"] = "Choose the payment terms."
        if errors:
            self.account_page(req, errors, f)
            return
        with self.lock:
            number = f"INV-{5000 + len(self.invoices) + 1}"
            self.invoices[s["ref"]] = Invoice(number, s["ref"], f["payer"].strip(), f["terms"], amount,
                                              f.get("vat") == "1", f.get("memo", ""))  # fmt: skip
        req.redirect("/billing/invoice")

    def invoice_page(self, req) -> None:
        """The saved invoice. The page is a shell; its values come from the invoices API."""
        s = self.session(req)
        inv = self.invoices.get((s or {}).get("ref") or "")
        if inv is None:
            req.redirect("/billing/lookup")
            return
        body = (f"<h2 class='page__title'>Invoice <span class='inv-no'>{esc(inv.number)}</span></h2>"
                f"<div class='summary' data-order='{esc(inv.order)}'></div>"
                "<p><a class='btn' data-testid='find-another' href='/billing/lookup'>Find another order</a></p>"
                "<script>(function(){const box=document.querySelector('.summary');"
                "const keys=[['order','Order'],['payer','Payer'],['terms','Payment terms'],['amount','Amount'],"
                "['vat','VAT'],['total','Total'],['memo','Memo']];"
                "fetch('/billing/api/invoices?order='+encodeURIComponent(box.dataset.order)).then(r=>r.json()).then(d=>{"
                "const inv=d.items[0];for(const [k,label] of keys){const row=document.createElement('div');row.className='summary__row';"
                "const a=document.createElement('span');a.className='summary__key';a.textContent=label;"
                "const b=document.createElement('span');b.className='summary__value';b.dataset.field=k;"
                "b.textContent=k==='vat'?(inv.vat?'added':'none'):(typeof inv[k]==='number'?inv[k].toFixed(2):inv[k]);"
                "row.append(a,b);box.append(row);}});})();</script>")  # fmt: skip
        self.page(req, "Invoice", body)

    def api_invoices(self, req) -> None:
        inv = self.invoices.get(req.query.get("order", "").strip().upper())
        items = [] if inv is None else [{"number": inv.number, "order": inv.order, "payer": inv.payer,
                                         "terms": inv.terms, "amount": inv.amount, "vat": inv.vat,
                                         "memo": inv.memo, "total": inv.total}]  # fmt: skip
        req.send_json({"items": items, "total": len(items)})


# --- the widget (third origin) -----------------------------------------------------------------------

WIDGET_PAGE = """<!doctype html><html><head><meta charset='utf-8'><title>Rates</title>
<style>body{font:12px sans-serif;margin:6px} .fx td,.fx th{padding:2px 6px}</style></head>
<body><table class="fx"><thead><tr><th>Currency</th><th>Rate</th></tr></thead><tbody id="fxRows"></tbody></table>
<script>fetch('/widget/rates.json').then(r => r.json()).then(d => {
  const body = document.getElementById('fxRows');
  for (const [k, v] of Object.entries(d.rates)) {
    const tr = document.createElement('tr'); tr.innerHTML = '<th>' + k + '</th><td>' + v.toFixed(4) + '</td>'; body.append(tr);
  }
});</script></body></html>"""


class Widget(App):
    def __init__(self) -> None:
        super().__init__()
        self.route("GET", "/widget/rates")(lambda req: req.send_page(WIDGET_PAGE))
        self.route("GET", "/widget/rates.json")(
            lambda req: req.send_json({"base": "GBP", "rates": {"EUR": 1.1612, "USD": 1.2675}})
        )


# --- launching ---------------------------------------------------------------------------------------


@dataclass
class Running:
    orders: str  # base addresses
    billing: str
    widget: str
    servers: list = field(default_factory=list)
    apps: dict = field(default_factory=dict)

    def stop(self) -> None:
        for s in self.servers:
            s.shutdown()
            s.server_close()


def _start(app: App, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.app = app  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def serve(ports: tuple[int, int, int] = (0, 0, 0)) -> Running:
    """Start the widget, billing and orders on 127.0.0.1 (free ports unless given), each with
    fresh data, and return their addresses."""
    widget = Widget()
    ws, wurl = _start(widget, ports[2])
    billing = Billing(wurl)
    bs, burl = _start(billing, ports[1])
    orders = Orders(burl)
    os_, ourl = _start(orders, ports[0])
    return Running(ourl, burl, wurl, [os_, bs, ws], {"orders": orders, "billing": billing,
                                                     "widget": widget})  # fmt: skip


if __name__ == "__main__":
    import sys
    import time

    ports = tuple(int(p) for p in sys.argv[1:4]) if len(sys.argv) >= 4 else (8731, 8732, 8733)
    run = serve(ports)  # type: ignore[arg-type]
    print(
        f"orders  {run.orders}/login\nbilling {run.billing}/billing/start\nwidget  {run.widget}/widget/rates"
    )
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        run.stop()
