"""Test helpers for acceptance, baseline and visual replays: the to-do fixture's pages copied
into a folder (so a test can make a deliberately wrong replica), served from 127.0.0.1 with every
request the server saw kept (path and Cookie header), and a short source flow."""

from __future__ import annotations

import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import todo_flow as tf

# What the short source flow types (the source's values; never in any artifact).
SOURCE_TYPED = ["Water plants", "Pay rent"]
# What the replica is given instead.
SYNTHETIC = ["Syn first", "Syn second"]


def make_app(dest: Path, page: str = "replica.html", js: list[tuple[str, str]] = (),
             html: list[tuple[str, str]] = ()) -> Path:  # fmt: skip
    """The fixture's `page` and todo.js in `dest`, each (old, new) patch applied once."""
    dest.mkdir(parents=True, exist_ok=True)
    text = (tf.APP / page).read_text(encoding="utf-8")
    for old, new in html:
        assert old in text, old
        text = text.replace(old, new, 1)
    (dest / "index.html").write_text(text, encoding="utf-8")
    code = (tf.APP / "todo.js").read_text(encoding="utf-8")
    for old, new in js:
        assert old in code, old
        code = code.replace(old, new, 1)
    (dest / "todo.js").write_text(code, encoding="utf-8")
    return dest


def serve_dir(folder: Path):
    """Serve index.html and todo.js from `folder`. `server.seen` lists (path, Cookie header)."""
    folder = Path(folder)
    seen: list[tuple[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append((self.path, self.headers.get("Cookie")))
            name = self.path.split("?")[0].split("#")[0].lstrip("/")
            types = {"index.html": "text/html; charset=utf-8", "todo.js": "text/javascript"}
            path = folder / name
            body = path.read_bytes() if name in types and path.exists() else b""
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", types.get(name, "text/plain"))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.seen = seen
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def short_flow(page) -> None:
    """Add two tasks with Enter, complete the first, reload."""
    new = page.locator(".new-todo")
    for title in SOURCE_TYPED:
        new.fill(title)
        new.press("Enter")
        tf.pause(page)
    tf.row(page, SOURCE_TYPED[0]).locator(".toggle").check()
    tf.pause(page)
    page.reload()
    tf.pause(page, 600)


def add_one_flow(page) -> None:
    new = page.locator(".new-todo")
    new.fill(SOURCE_TYPED[0])
    new.press("Enter")
    tf.pause(page, 400)


def typed_steps(rec_dir: Path) -> list[int]:
    from labctl.botlog.recording import load

    return [s.seq for s in load(rec_dir).steps if s.action == "type" and not s.blank]


def write_bindings(rec_dir: Path, values: list[str]) -> None:
    import yaml

    steps = {seq: {"value": v} for seq, v in zip(typed_steps(rec_dir), values, strict=True)}
    (rec_dir / "bindings.yaml").write_text(yaml.safe_dump({"version": 1, "steps": steps}),
                                           encoding="utf-8")  # fmt: skip


def copy_recording(rec_dir: Path, dest: Path) -> Path:
    shutil.copytree(rec_dir, dest)
    return dest
