"""The parcel depot (static/), served on 127.0.0.1 with a small in-memory JSON API, for the code
map tests. Synthetic data only. Written by hand: no framework, nothing downloaded.

- `/static/...`: the application's files (`static/js/main.js`, the lazy `static/js/admin.js`,
  `static/i18n/en.json`); every other page address answers `static/index.html` (client routes).
- `POST /api/session` signs in (any user name, the password `Synthetic-pass-7`), sets `depot_session`;
  `GET /api/session` answers who is signed in (401 when nobody); `DELETE` signs out.
- `GET /api/parcels`, `GET /api/parcels/<id>`, `GET /api/parcels/<id>/history`,
  `POST /api/parcels`, `DELETE /api/parcels/<id>`, `GET /api/depots`, `GET /api/admin/users`,
  `PUT /api/admin/settings`.

`serve()` starts it on a free port; `Running.requests` lists every request it was asked.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).parent / "static"
COOKIE = "depot_session"
PASSWORD = "Synthetic-pass-7"
TYPES = {".js": "text/javascript", ".json": "application/json", ".css": "text/css",
         ".html": "text/html; charset=utf-8"}  # fmt: skip
DEPOTS = ["Harbour North", "Quarry Lane", "Mill Cross"]
RECIPIENTS = ["Wren Okafor", "Basil Tamm", "Ivo Marsh", "Lena Brook"]
STATUSES = ["received", "sorted", "out", "delivered"]


def _parcels() -> list[dict]:
    return [{"id": f"P-{1001 + i}", "recipient": RECIPIENTS[i % 4], "depot": DEPOTS[i % 3],
             "status": STATUSES[i % 4]} for i in range(6)]  # fmt: skip


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: ANN002 - quiet
        pass

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data, extra: dict | None = None) -> None:
        self._send(status, json.dumps(data).encode(), "application/json", extra)

    def _user(self):
        cookie = self.headers.get("Cookie") or ""
        m = re.search(rf"{COOKIE}=([\w-]+)", cookie)
        return self.server.sessions.get(m.group(1)) if m else None  # type: ignore[attr-defined]

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def _route(self, method: str) -> None:
        self.server.requests.append((method, self.path, self.headers.get("Cookie") or ""))  # type: ignore[attr-defined]
        path = urlsplit(self.path).path
        if path.startswith("/static/"):
            file = (HERE / path[len("/static/") :]).resolve()
            if method != "GET" or HERE.resolve() not in file.parents or not file.is_file():
                return self._send(404, b"not found", "text/plain")
            return self._send(200, file.read_bytes(), TYPES.get(file.suffix, "text/plain"))
        if not path.startswith("/api/"):
            if method != "GET":
                return self._send(405, b"", "text/plain")
            return self._send(200, (HERE / "index.html").read_bytes(), TYPES[".html"])
        user = self._user()
        if path == "/api/session":
            if method == "POST":
                b = self._body()
                if b.get("username") and b.get("password") == PASSWORD:
                    token = secrets.token_hex(16)
                    self.server.sessions[token] = {"name": b["username"], "admin": False}  # type: ignore[attr-defined]
                    return self._json(
                        200, {"ok": True}, {"Set-Cookie": f"{COOKIE}={token}; Path=/"}
                    )
                return self._json(401, {"error": "Wrong user name or password."})
            if method == "DELETE":
                return self._json(204, {})
            return self._json(200, user) if user else self._json(401, {"error": "Not signed in."})
        if user is None:
            return self._json(401, {"error": "Not signed in."})
        parcels = self.server.parcels  # type: ignore[attr-defined]
        m = re.fullmatch(r"/api/parcels/([\w-]+)(/history)?", path)
        if path == "/api/parcels" and method == "GET":
            return self._json(200, {"items": parcels, "page": 1, "pages": 1})
        if path == "/api/parcels" and method == "POST":
            p = {"id": f"P-{1001 + len(parcels)}", "recipient": self._body().get("recipient", ""),
                 "depot": DEPOTS[0], "status": "received"}  # fmt: skip
            parcels.append(p)
            return self._json(201, p)
        if m:
            p = next((x for x in parcels if x["id"] == m.group(1)), None)
            if p is None:
                return self._json(404, {"error": "No such parcel."})
            if m.group(2):
                return self._json(200, {"items": [{"status": "received"}, {"status": p["status"]}]})
            if method == "DELETE":
                parcels.remove(p)
                return self._json(204, {})
            return self._json(200, p)
        if path == "/api/depots":
            return self._json(200, {"items": [{"name": d} for d in DEPOTS]})
        if path == "/api/admin/users":
            return self._json(200, {"items": [{"name": "Basil Tamm", "role": "clerk"}]})
        if path == "/api/admin/settings" and method == "PUT":
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "Not found."})

    def do_GET(self) -> None:  # noqa: N802
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._route("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._route("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._route("DELETE")


class Running:
    def __init__(self, server: ThreadingHTTPServer) -> None:
        self.server = server
        self.url = f"http://127.0.0.1:{server.server_address[1]}"

    @property
    def requests(self) -> list[tuple[str, str, str]]:
        return self.server.requests  # type: ignore[attr-defined]

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def serve(port: int = 0) -> Running:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.sessions = {}  # type: ignore[attr-defined]
    server.parcels = _parcels()  # type: ignore[attr-defined]
    server.requests = []  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return Running(server)


if __name__ == "__main__":
    import time

    run = serve(8752)
    print(f"parcel depot  {run.url}/parcels")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        run.stop()
