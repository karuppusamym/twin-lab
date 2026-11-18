"""The independent source fixture for capture and replay (fixtures/todo-app): a small to-do list
page written for these tests, served locally, and the scripted flow of the replication gate run
on it while Twin Lab records. Synthetic data only; no network beyond 127.0.0.1."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP = Path(__file__).parent / "fixtures" / "todo-app"
KEY = "tl-todos"

# What the source flow types, in order (blank values aside). None of these may appear in any
# file of the recording.
TYPED = ["Water plants", "Pay rent", "Book flights", "Pay rent now", "Pay the rent",
         "Call grandmother", "Temporary chore", "Buy stamps"]  # fmt: skip

# The gate's semantic actions, declared here from the flow below, not from what the recorder
# produced.
EXPECTED = [
    "goto",
    "type", "keypress:Enter",  # a blank task is rejected
    "type", "keypress:Enter", "type", "keypress:Enter", "type", "keypress:Enter",  # add three
    "check",  # complete one
    "click", "click", "click",  # the three filters
    "dblclick", "type", "keypress:Escape",  # edit, cancelled
    "dblclick", "type", "keypress:Enter",  # edit, saved
    "goto:reload",
    "check",  # toggle all
    "uncheck",  # one back to active
    "click",  # clear completed
    "type", "keypress:Enter", "click",  # add a task and delete it
    "type", "keypress:Enter", "dblclick", "type", "keypress:Enter",  # delete by an empty title
    "type", "keypress:Enter",  # a last task, kept
]  # fmt: skip


def serve(page: str):
    """Serve the source or the replica page at /index.html (both at the same path, so a
    recording of one replays on the other), with todo.js."""
    routes = {
        "/index.html": ((APP / page).read_bytes(), "text/html; charset=utf-8"),
        "/todo.js": ((APP / "todo.js").read_bytes(), "text/javascript"),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body, ctype = routes.get(self.path.split("?")[0].split("#")[0], (b"", "text/plain"))
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def base(server) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


def row(page, title: str):
    return page.locator(".todo-list li").filter(has=page.locator("label", has_text=title))


def pause(page, ms: int = 250) -> None:
    page.wait_for_timeout(ms)


def gate_flow(page) -> None:
    """The first completion gate, as a person would do it."""
    new = page.locator(".new-todo")
    new.fill("   ")
    new.press("Enter")  # rejected: blank
    pause(page)
    for title in TYPED[:3]:
        new.fill(title)
        new.press("Enter")
        pause(page)
    row(page, "Water plants").locator(".toggle").check()
    pause(page)
    for name in ("Active", "Completed", "All"):
        page.get_by_role("link", name=name).click()
        pause(page, 700)
    row(page, "Pay rent").locator("label").dblclick()
    pause(page)
    edit = page.locator("li.editing .edit")
    edit.fill(TYPED[3])
    edit.press("Escape")  # cancelled
    pause(page)
    row(page, "Pay rent").locator("label").dblclick()
    pause(page)
    edit = page.locator("li.editing .edit")
    edit.fill(TYPED[4])
    edit.press("Enter")  # saved
    pause(page, 700)
    page.reload()
    pause(page, 700)
    page.locator("#toggle-all").check()
    pause(page)
    row(page, "Book flights").locator(".toggle").uncheck()
    pause(page)
    page.get_by_role("button", name="Clear completed").click()
    pause(page, 700)
    new.fill(TYPED[5])
    new.press("Enter")
    pause(page)
    target = row(page, TYPED[5])
    target.hover()
    target.locator(".destroy").click()
    pause(page, 700)
    new.fill(TYPED[6])
    new.press("Enter")
    pause(page)
    row(page, TYPED[6]).locator("label").dblclick()
    pause(page)
    edit = page.locator("li.editing .edit")
    edit.fill("")
    edit.press("Enter")  # an empty title deletes the task
    pause(page)
    new.fill(TYPED[7])
    new.press("Enter")
    pause(page, 700)


def state(page) -> dict:
    """The page's business state, read independently of any DOM comparison."""
    return page.evaluate(
        """(key) => ({
          titles: [...document.querySelectorAll('.todo-list li label')].map(l => l.textContent),
          completed: [...document.querySelectorAll('.todo-list li .toggle')].map(b => b.checked),
          left: document.querySelector('.todo-count').textContent,
          clear_hidden: document.querySelector('.clear-completed').hidden,
          stored: localStorage.getItem(key),
          hash: location.hash,
        })""",
        KEY,
    )
