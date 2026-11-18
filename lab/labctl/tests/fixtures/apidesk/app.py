"""A small help-desk application whose pages are filled by a JSON API, written for the tests of
the API catalogue, the navigation map and the per-screen action lists. Synthetic data only;
served from 127.0.0.1 and kept in memory.

- Sign-in: `/login` posts JSON to `POST /api/session`, which sets the cookie `desk_session`
  (401 with a message otherwise). The page first asks `GET /api/session` (401 when signed out).
  A page asked for without a session redirects to `/login`; an API call answers 401.
- `/tickets`: a list from `GET /api/tickets` (search `q`, filter `status`, `sort`, `page`), a
  pager, a modal dialog "New ticket" (`POST /api/tickets`: 201, or 422 with messages per field),
  and an embedded help panel in an iframe (`/help/panel`, a page within the page).
- `/tickets/<id>`: one ticket from `GET /api/tickets/<id>`, two in-page tabs (the History tab
  asks `GET /api/tickets/<id>/history`), a form saved by `PATCH /api/tickets/<id>` (200 or 422),
  a print view in a popup window (`/tickets/<id>/print`) and a delete behind a confirmation
  dialog (`DELETE /api/tickets/<id>`, 204).
- Sign-out: `DELETE /api/session`.

Run by hand with `python app.py [port]`. Tests use `serve()`.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PAGE_SIZE = 5
COOKIE = "desk_session"
STATUSES = ("OPEN", "PENDING", "CLOSED")
PRIORITIES = ("LOW", "NORMAL", "HIGH")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_TITLES = [
    "Office printer offline", "Intranet account locked", "Laptop screen flickers",
    "Shared printer prints blank pages", "New starter needs a badge", "VPN drops every hour",
    "Label printer misaligned", "Meeting room display dark", "Mailbox quota reached",
    "Colour printer out of toner", "Keyboard keys stick", "Phone headset crackles",
]  # fmt: skip
_PEOPLE = ["Mira Holt", "Jonas Weir", "Petra Lund", "Oskar Brandt"]
_REQ = ["ines.calvo", "tomas.reyl", "greta.moss", "henrik.daal", "olga.prenn", "rufus.tane"]


def _seed() -> list[dict]:
    out = []
    for i, title in enumerate(_TITLES):
        status = STATUSES[i % 3]
        out.append({
            "id": f"TCK-{1001 + i}", "title": title,
            "requester": f"{_REQ[i % len(_REQ)]}@fixture-mail.test",
            "priority": PRIORITIES[(i * 2) % 3], "status": status,
            "assignee": "" if status == "OPEN" else _PEOPLE[i % len(_PEOPLE)],
            "created": f"2026-10-{3 + 2 * i:02d}",
            "description": f"Reported at the front desk, case {i + 1}.",
        })  # fmt: skip
    return out


STYLE = """
body { font-family: Verdana, sans-serif; margin: 0; background: #fafafa; color: #222; }
#topbar { background: #0b5; color: #fff; padding: 8px 16px; display: flex; gap: 20px; }
#topbar a, #topbar button { color: #fff; background: none; border: 0; font: inherit; cursor: pointer; }
main { padding: 16px; max-width: 860px; }
label { display: block; margin-top: 8px; }
.error, .field-error { color: #b00020; }
table { border-collapse: collapse; margin-top: 12px; }
td, th { border: 1px solid #bbb; padding: 4px 8px; }
[role=tab][aria-selected=true] { font-weight: bold; border-bottom: 2px solid #0b5; }
dialog { border: 1px solid #888; padding: 16px; }
iframe { border: 1px solid #ccc; width: 420px; height: 130px; margin-top: 16px; }
"""

NAV = """<header id="topbar"><span class="brand">Help desk</span><nav id="mainNav">
<a id="navTickets" href="/tickets">Tickets</a><a id="navReports" href="/reports">Reports</a>
<button id="signOut" type="button">Sign out</button></nav><span id="whoAmI"></span></header>
<script>
fetch('/api/session').then(r => r.ok ? r.json() : null).then(d => {
  if (d) document.getElementById('whoAmI').textContent = d.user.display_name;
});
document.getElementById('signOut').addEventListener('click', async () => {
  await fetch('/api/session', {method: 'DELETE'});
  location.href = '/login';
});
</script>"""


def page(title: str, body: str, nav: bool = True) -> str:
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Help desk - {title}'
            f"</title><style>{STYLE}</style></head><body>{NAV if nav else ''}{body}</body></html>")  # fmt: skip


LOGIN = page("Sign in", """<header id="topbar"><span class="brand">Help desk</span></header>
<main><h1>Sign in</h1><form id="loginForm">
<label for="username">User name</label><input id="username" name="username" autocomplete="off">
<label for="password">Password</label><input id="password" name="password" type="password">
<button id="signIn" type="submit">Sign in</button></form>
<div id="loginError" class="error" role="alert" hidden></div></main>
<script>
fetch('/api/session').then(r => { if (r.ok) location.href = '/tickets'; });
document.getElementById('loginForm').addEventListener('submit', async ev => {
  ev.preventDefault();
  const r = await fetch('/api/session', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username: username.value, password: password.value})});
  if (r.ok) { location.href = '/tickets'; return; }
  const d = await r.json();
  loginError.textContent = d.error; loginError.hidden = false;
});
</script>""", nav=False)  # fmt: skip

TICKETS = page("Tickets", """<main><h1>Tickets</h1>
<p id="flash" class="flash" role="status" hidden></p>
<form id="searchForm">
<label for="q">Search in titles</label><input id="q" name="q">
<label for="statusFilter">Status</label><select id="statusFilter" name="status">
<option value="">Any status</option><option value="OPEN">Open</option>
<option value="PENDING">Pending</option><option value="CLOSED">Closed</option></select>
<label for="sortBy">Sort by</label><select id="sortBy" name="sort">
<option value="newest">Newest first</option><option value="oldest">Oldest first</option>
<option value="title">Title</option></select>
<button id="searchBtn" type="submit">Search</button>
<button id="newTicket" type="button">New ticket</button></form>
<table id="tickets"><thead><tr><th>Ticket</th><th>Title</th><th>Requester</th><th>Priority</th>
<th>Status</th><th>Created</th></tr></thead><tbody></tbody></table>
<p id="noTickets" hidden>No tickets found.</p>
<div id="pager"><button id="prevPage" type="button" disabled>Previous</button>
<span id="pageInfo"></span><button id="nextPage" type="button" disabled>Next</button></div>
<dialog id="ticketDialog" aria-labelledby="ticketDialogTitle"><h2 id="ticketDialogTitle">New ticket</h2>
<form id="ticketForm" novalidate>
<label for="title">Title</label><input id="title" name="title" maxlength="80">
<span id="titleError" class="field-error" hidden></span>
<label for="requester">Requester e-mail</label><input id="requester" name="requester">
<span id="requesterError" class="field-error" hidden></span>
<label for="priority">Priority</label><select id="priority" name="priority">
<option value="LOW">Low</option><option value="NORMAL" selected>Normal</option>
<option value="HIGH">High</option></select>
<label for="description">Description</label><textarea id="description" name="description"></textarea>
<div id="ticketFormError" class="error" hidden></div>
<button id="saveTicket" type="submit">Create ticket</button>
<button id="cancelTicket" type="button">Cancel</button></form></dialog>
<h2>Help</h2><iframe id="helpFrame" src="/help/panel" title="Help panel"></iframe></main>
<script>
let pageNo = 1, pages = 1;
const $ = id => document.getElementById(id);
async function load() {
  const p = new URLSearchParams({q: $('q').value, status: $('statusFilter').value,
                                 sort: $('sortBy').value, page: pageNo});
  const r = await fetch('/api/tickets?' + p);
  if (r.status === 401) { location.href = '/login'; return; }
  const d = await r.json();
  pages = d.pages;
  const body = document.querySelector('#tickets tbody');
  body.replaceChildren();
  for (const t of d.items) {
    const tr = document.createElement('tr'); tr.className = 'ticket-row';
    const first = document.createElement('td'); first.className = 'ticket-id';
    const a = document.createElement('a'); a.className = 'ticket-link';
    a.href = '/tickets/' + t.id; a.textContent = t.id; first.append(a); tr.append(first);
    for (const k of ['title', 'requester', 'priority', 'status', 'created']) {
      const td = document.createElement('td'); td.textContent = t[k]; tr.append(td);
    }
    body.append(tr);
  }
  $('noTickets').hidden = d.total > 0;
  $('pageInfo').textContent = d.total ? 'Page ' + d.page + ' of ' + d.pages : '';
  $('prevPage').disabled = d.page <= 1; $('nextPage').disabled = d.page >= d.pages;
}
$('searchForm').addEventListener('submit', ev => { ev.preventDefault(); pageNo = 1; load(); });
$('statusFilter').addEventListener('change', () => { pageNo = 1; load(); });
$('sortBy').addEventListener('change', () => { pageNo = 1; load(); });
$('prevPage').addEventListener('click', () => { pageNo -= 1; load(); });
$('nextPage').addEventListener('click', () => { pageNo += 1; load(); });
$('newTicket').addEventListener('click', () => $('ticketDialog').showModal());
$('cancelTicket').addEventListener('click', () => $('ticketDialog').close());
$('ticketForm').addEventListener('submit', async ev => {
  ev.preventDefault();
  const body = {title: $('title').value, requester: $('requester').value,
                priority: $('priority').value, description: $('description').value};
  const r = await fetch('/api/tickets', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const d = await r.json();
  for (const k of ['title', 'requester']) {
    const e = $(k + 'Error'); const m = (d.fields || {})[k];
    e.textContent = m || ''; e.hidden = !m;
  }
  $('ticketFormError').textContent = r.ok ? '' : d.error; $('ticketFormError').hidden = r.ok;
  if (!r.ok) return;
  $('ticketDialog').close(); $('ticketForm').reset();
  $('flash').textContent = 'Ticket ' + d.id + ' was created.'; $('flash').hidden = false;
  $('q').value = ''; $('statusFilter').value = ''; $('sortBy').value = 'newest'; pageNo = 1;
  load();
});
load();
</script>""")  # fmt: skip

DETAIL = page("Ticket", """<main><a id="backToList" href="/tickets">Back to the list</a>
<h1 id="ticketTitle"></h1>
<div role="tablist" id="ticketTabs"><button id="tabDetails" role="tab" type="button"
aria-selected="true" aria-controls="panelDetails">Details</button><button id="tabHistory"
role="tab" type="button" aria-selected="false" aria-controls="panelHistory">History</button></div>
<section id="panelDetails" role="tabpanel" aria-labelledby="tabDetails">
<dl id="ticketFacts"><dt>Ticket</dt><dd id="ticketId"></dd><dt>Requester</dt>
<dd id="ticketRequester"></dd><dt>Priority</dt><dd id="ticketPriority"></dd><dt>Created</dt>
<dd id="ticketCreated"></dd><dt>Description</dt><dd id="ticketDescription"></dd></dl>
<form id="editForm" novalidate><label for="status">Status</label><select id="status" name="status">
<option value="OPEN">Open</option><option value="PENDING">Pending</option>
<option value="CLOSED">Closed</option></select>
<label for="assignee">Assignee</label><input id="assignee" name="assignee">
<span id="assigneeError" class="field-error" hidden></span>
<button id="saveChanges" type="submit">Save changes</button></form>
<p id="saveMessage" class="flash" role="status" hidden>Changes saved.</p></section>
<section id="panelHistory" role="tabpanel" aria-labelledby="tabHistory" hidden>
<ul id="historyList"></ul><p id="noHistory" hidden>Nothing has changed yet.</p></section>
<p><button id="printTicket" type="button">Print view</button>
<button id="deleteTicket" type="button">Delete ticket</button></p>
<dialog id="confirmDelete" aria-labelledby="confirmDeleteTitle">
<h2 id="confirmDeleteTitle">Delete this ticket?</h2><p>This cannot be undone.</p>
<button id="confirmDeleteYes" type="button">Delete</button>
<button id="confirmDeleteNo" type="button">Keep it</button></dialog></main>
<script>
const $ = id => document.getElementById(id);
const id = location.pathname.split('/').pop();
function show(t) {
  $('ticketTitle').textContent = t.title; $('ticketId').textContent = t.id;
  $('ticketRequester').textContent = t.requester; $('ticketPriority').textContent = t.priority;
  $('ticketCreated').textContent = t.created; $('ticketDescription').textContent = t.description;
  $('status').value = t.status; $('assignee').value = t.assignee;
}
fetch('/api/tickets/' + id).then(r => {
  if (r.status === 401) { location.href = '/login'; return null; }
  return r.ok ? r.json() : null;
}).then(t => { if (t) show(t); else if (t === null) $('ticketTitle').textContent = 'Ticket not found'; });
function tab(name) {
  for (const n of ['Details', 'History']) {
    $('tab' + n).setAttribute('aria-selected', n === name ? 'true' : 'false');
    $('panel' + n).hidden = n !== name;
  }
}
$('tabDetails').addEventListener('click', () => tab('Details'));
$('tabHistory').addEventListener('click', async () => {
  tab('History');
  const d = await (await fetch('/api/tickets/' + id + '/history')).json();
  const list = $('historyList'); list.replaceChildren();
  for (const h of d.items) {
    const li = document.createElement('li'); li.className = 'history-item';
    li.textContent = h.at + ' ' + h.by + ': ' + h.change; list.append(li);
  }
  $('noHistory').hidden = d.items.length > 0;
});
$('editForm').addEventListener('submit', async ev => {
  ev.preventDefault();
  const r = await fetch('/api/tickets/' + id, {method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status: $('status').value, assignee: $('assignee').value})});
  const d = await r.json();
  const m = (d.fields || {}).assignee;
  $('assigneeError').textContent = m || ''; $('assigneeError').hidden = !m;
  $('saveMessage').hidden = !r.ok;
  if (r.ok) show(d);
});
$('printTicket').addEventListener('click', () =>
  window.open('/tickets/' + id + '/print', 'printView', 'width=640,height=520'));
$('deleteTicket').addEventListener('click', () => $('confirmDelete').showModal());
$('confirmDeleteNo').addEventListener('click', () => $('confirmDelete').close());
$('confirmDeleteYes').addEventListener('click', async () => {
  await fetch('/api/tickets/' + id, {method: 'DELETE'});
  location.href = '/tickets';
});
</script>""")  # fmt: skip

PRINT = page("Print view", """<main id="printPage"><h1 id="printTitle"></h1>
<dl><dt>Ticket</dt><dd id="printId"></dd><dt>Status</dt><dd id="printStatus"></dd>
<dt>Assignee</dt><dd id="printAssignee"></dd></dl>
<button id="closePrint" type="button">Close</button></main>
<script>
const id = location.pathname.split('/')[2];
fetch('/api/tickets/' + id).then(r => r.json()).then(t => {
  printTitle.textContent = t.title; printId.textContent = t.id;
  printStatus.textContent = t.status; printAssignee.textContent = t.assignee;
});
closePrint.addEventListener('click', () => window.close());
</script>""", nav=False)  # fmt: skip

HELP = page("Help panel", """<main id="helpPanel"><p>Need a hand with a ticket?</p>
<button id="helpToggle" type="button">Show a tip</button>
<p id="helpTip" hidden>Search by a word of the title, then open the ticket.</p>
<a id="helpGuide" href="/help/guide">Read the guide</a></main>
<script>
helpToggle.addEventListener('click', () => { helpTip.hidden = !helpTip.hidden; });
</script>""", nav=False)  # fmt: skip


class Desk:
    def __init__(self) -> None:
        self.tickets = _seed()
        self.history: dict[str, list[dict]] = {}
        self.sessions: dict[str, dict] = {}
        self.lock = threading.Lock()

    def next_id(self) -> str:
        return f"TCK-{max(int(t['id'][4:]) for t in self.tickets) + 1}"

    def find(self, ticket_id: str) -> dict | None:
        return next((t for t in self.tickets if t["id"] == ticket_id), None)


def _public(t: dict) -> dict:
    return {k: t[k] for k in ("id", "title", "requester", "priority", "status", "created")}


class Handler(BaseHTTPRequestHandler):
    server_version = "FixtureDesk/1.0"

    def log_message(self, *args):
        pass

    # -- plumbing --
    def _send(self, body: bytes, ctype: str, status: int = 200, headers=None) -> None:
        self.send_response(status)
        if body or status != 204:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def html(self, text: str, status: int = 200) -> None:
        self._send(text.encode("utf-8"), "text/html; charset=utf-8", status)

    def json(self, data, status: int = 200, headers=None) -> None:
        self._send(json.dumps(data).encode("utf-8"), "application/json", status, headers)

    def empty(self, headers=None) -> None:
        self._send(b"", "", 204, headers)

    def go(self, to: str) -> None:
        self._send(b"", "text/plain", 302, {"Location": to})

    def user(self) -> dict | None:
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                return self.server.desk.sessions.get(value)  # type: ignore[attr-defined]
        return None

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    # -- routes --
    def _route(self, method: str) -> None:
        desk: Desk = self.server.desk  # type: ignore[attr-defined]
        parts = urlsplit(self.path)
        path = parts.path
        query = {k: v[-1] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        with desk.lock:
            if path.startswith("/api/"):
                self._api(desk, method, path, query)
            elif method != "GET":
                self.html(page("Not found", "<main><h1>Not found</h1></main>", nav=False), 404)
            elif path == "/login":
                self.html(LOGIN)
            elif path == "/help/panel":
                self.html(HELP)
            elif self.user() is None:
                self.go("/login")
            elif path in ("/", "/tickets"):
                self.html(TICKETS)
            elif re.fullmatch(r"/tickets/TCK-\d+/print", path):
                self.html(PRINT)
            elif re.fullmatch(r"/tickets/TCK-\d+", path):
                self.html(DETAIL)
            else:
                self.html(page("Not found", "<main><h1>Not found</h1></main>"), 404)

    def _api(self, desk: Desk, method: str, path: str, query: dict) -> None:
        if path == "/api/session":
            if method == "POST":
                data = self.body()
                name, password = str(data.get("username") or ""), str(data.get("password") or "")
                if not name.strip() or not password:
                    self.json({"error": "Give your user name and your password."}, 401)
                    return
                sid = secrets.token_hex(12)
                user = {"username": name, "display_name": name.split(".")[0].title(),
                        "role": "agent"}  # fmt: skip
                desk.sessions[sid] = user
                self.json({"user": user}, 200,
                          {"Set-Cookie": f"{COOKIE}={sid}; Path=/; HttpOnly; SameSite=Lax"})  # fmt: skip
            elif method == "DELETE":
                for part in (self.headers.get("Cookie") or "").split(";"):
                    name, _, value = part.strip().partition("=")
                    if name == COOKIE:
                        desk.sessions.pop(value, None)
                self.empty({"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0"})
            elif self.user() is None:
                self.json({"error": "Not signed in."}, 401)
            else:
                self.json({"user": self.user()})
            return
        user = self.user()
        if user is None:
            self.json({"error": "Not signed in."}, 401)
            return
        m = re.fullmatch(r"/api/tickets(?:/(TCK-\d+))?(/history)?", path)
        if not m:
            self.json({"error": "Not found."}, 404)
            return
        ticket_id, history = m.group(1), bool(m.group(2))
        if ticket_id is None:
            if method == "GET":
                self._list(desk, query)
            elif method == "POST":
                self._create(desk, user)
            else:
                self.json({"error": "Method not allowed."}, 405)
            return
        ticket = desk.find(ticket_id)
        if ticket is None:
            self.json({"error": "No such ticket."}, 404)
        elif history:
            self.json({"items": desk.history.get(ticket_id, [])})
        elif method == "GET":
            self.json(ticket)
        elif method == "PATCH":
            data = self.body()
            status, assignee = str(data.get("status") or ""), str(data.get("assignee") or "")
            fields = {}
            if status not in STATUSES:
                fields["status"] = "Choose a status."
            if status == "CLOSED" and not assignee.strip():
                fields["assignee"] = "Closing a ticket needs an assignee."
            if fields:
                self.json({"error": "Check the marked fields.", "fields": fields}, 422)
                return
            if status != ticket["status"]:
                desk.history.setdefault(ticket_id, []).append({
                    "at": date.today().isoformat(), "by": user["display_name"],
                    "change": f"Status {ticket['status']} to {status}"})  # fmt: skip
            ticket["status"], ticket["assignee"] = status, assignee.strip()
            self.json(ticket)
        elif method == "DELETE":
            desk.tickets.remove(ticket)
            self.empty()
        else:
            self.json({"error": "Method not allowed."}, 405)

    def _list(self, desk: Desk, query: dict) -> None:
        rows = list(desk.tickets)
        q = (query.get("q") or "").strip().lower()
        if q:
            rows = [t for t in rows if q in t["title"].lower()]
        if query.get("status"):
            rows = [t for t in rows if t["status"] == query["status"]]
        sort = query.get("sort") or "newest"
        key = "title" if sort == "title" else "created"
        rows.sort(key=lambda t: (t[key].lower(), t["id"]), reverse=sort == "newest")
        total = len(rows)
        pages = max(1, -(-total // PAGE_SIZE))
        try:
            page_no = min(max(1, int(query.get("page") or 1)), pages)
        except ValueError:
            page_no = 1
        chunk = rows[(page_no - 1) * PAGE_SIZE : page_no * PAGE_SIZE]
        self.json({"items": [_public(t) for t in chunk], "page": page_no, "pages": pages,
                   "total": total})  # fmt: skip

    def _create(self, desk: Desk, user: dict) -> None:
        data = self.body()
        title, requester = str(data.get("title") or "").strip(), str(data.get("requester") or "")
        fields = {}
        if not title:
            fields["title"] = "Give the ticket a title."
        if not EMAIL.match(requester.strip()):
            fields["requester"] = "Give an e-mail address such as name@example.test."
        priority = str(data.get("priority") or "NORMAL")
        if priority not in PRIORITIES:
            fields["priority"] = "Choose a priority."
        if fields:
            self.json({"error": "Check the marked fields.", "fields": fields}, 422)
            return
        ticket = {"id": desk.next_id(), "title": title, "requester": requester.strip(),
                  "priority": priority, "status": "OPEN", "assignee": "",
                  "created": date.today().isoformat(),
                  "description": str(data.get("description") or "")}  # fmt: skip
        desk.tickets.append(ticket)
        self.json(ticket, 201)

    def do_GET(self):  # noqa: N802
        self._route("GET")

    def do_POST(self):  # noqa: N802
        self._route("POST")

    def do_PATCH(self):  # noqa: N802
        self._route("PATCH")

    def do_DELETE(self):  # noqa: N802
        self._route("DELETE")


class Running:
    def __init__(self, server: ThreadingHTTPServer) -> None:
        self.server = server
        self.url = f"http://127.0.0.1:{server.server_address[1]}"
        self.desk: Desk = server.desk  # type: ignore[attr-defined]

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def serve(port: int = 0) -> Running:
    """Start the help desk on 127.0.0.1 (a free port unless given) with fresh data."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.desk = Desk()  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return Running(server)


if __name__ == "__main__":
    import sys
    import time

    run = serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8741)
    print(f"help desk  {run.url}/login")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        run.stop()
