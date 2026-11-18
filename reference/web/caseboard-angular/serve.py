"""Serve the built Caseboard (`site/`) with nothing but Python's standard library.

    python serve.py --port 4210

A single-page application: an address that names no file (`/cases/CB-1007`) is answered with
`index.html`, and Angular's router shows the page. Binds 127.0.0.1 only. Nothing is cached, so
two instances never share anything through the browser.

`serve()` starts it in a thread (port 0: any free port) for `certify_local.py` and the tests.
"""

from __future__ import annotations

import argparse
import threading
from dataclasses import dataclass, field
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SITE = Path(__file__).resolve().parent / "site"
HOST = "127.0.0.1"

# Given here, not read from the machine: Windows can map .js to text/plain in its registry, and a
# browser refuses a module script that is not served as JavaScript.
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".map": "application/json",
}


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**TYPES, "": "application/octet-stream"}
    hits: list[tuple[str, str, int]]  # (method, path, status), set per server by serve()

    def translate_path(self, path: str) -> str:
        found = Path(super().translate_path(path))
        if found.is_file():
            return str(found)
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if "." in clean.rsplit("/", 1)[-1]:
            return str(found)  # a file that is not there: 404, never the application shell
        return str(Path(self.directory) / "index.html")

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_request(self, code="-", size="-") -> None:
        self.hits.append((self.command, self.path, int(code) if str(code).isdigit() else 0))

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - the base class's name
        pass


@dataclass
class Running:
    base: str
    port: int
    server: ThreadingHTTPServer
    hits: list[tuple[str, str, int]] = field(default_factory=list)

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def serve(root: Path = SITE, port: int = 0) -> Running:
    """Start serving `root` on 127.0.0.1 in a background thread; `stop()` ends it."""
    if not (Path(root) / "index.html").is_file():
        raise FileNotFoundError(f"{root} has no index.html: build the application first (README)")
    hits: list[tuple[str, str, int]] = []
    handler = type("CaseboardHandler", (Handler,), {"hits": hits})
    server = ThreadingHTTPServer((HOST, port), partial(handler, directory=str(root)))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    bound = server.server_address[1]
    return Running(base=f"http://{HOST}:{bound}", port=bound, server=server, hits=hits)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the built Caseboard on 127.0.0.1.")
    parser.add_argument("--port", type=int, default=4210)
    parser.add_argument("--root", type=Path, default=SITE, help="the built site (default: site/)")
    args = parser.parse_args()
    run = serve(args.root, args.port)
    print(f"Caseboard (Angular) at {run.base}/  -  Ctrl+C to stop", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        run.stop()


if __name__ == "__main__":
    main()
