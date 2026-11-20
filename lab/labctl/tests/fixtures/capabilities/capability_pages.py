"""Small pages, one per interaction surface Twin Lab says it captures and replays (R12), served on
two local origins (`main` and `other`, for a cross-origin frame). Synthetic content only; nothing
leaves 127.0.0.1. `serve()` returns both addresses; `stop()` shuts them down.

Pages on `main`: /iframe-same, /iframe-cross, /shadow-open, /shadow-closed, /upload, /download,
/popup (and /child), /dialogs, /async (its /api/slow answers after SLOW_MS), /select, /drag,
/canvas, /sw (registers /sw.js), /slow-form (its POST answers after SLOW_MS, then redirects to
/slow-done: POST-redirect-GET slower than the recorder's settle time).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

SLOW_MS = 1500
HEAD = "<!doctype html><html><head><meta charset='utf-8'><title>{t}</title></head><body>"
TAIL = "</body></html>"


def page(title: str, body: str) -> str:
    return HEAD.format(t=title) + body + TAIL


FRAME = page("Frame", "<button id='ping' type='button'>Ping</button><p id='out'></p>"
                      "<script>document.getElementById('ping').onclick = () => "
                      "{ document.getElementById('out').textContent = 'pinged'; };</script>")  # fmt: skip

SHADOW = """<x-card id='card'></x-card><script>
customElements.define('x-card', class extends HTMLElement {
  connectedCallback() {
    const root = this.attachShadow({mode: '%s'});
    root.innerHTML = "<input class='title' placeholder='Title'><button class='add' type='button'>Add</button>"
      + "<ul class='items'></ul>";
    root.querySelector('.add').addEventListener('click', () => {
      const li = document.createElement('li');
      li.textContent = root.querySelector('.title').value ? 'added' : 'empty';
      root.querySelector('.items').append(li);
    });
  }
});</script>"""

PAGES = {
    "/iframe-same": page(
        "Same-origin frame", "<h1>Frame</h1><iframe id='inner' src='/frame'></iframe>"
    ),
    "/frame": FRAME,
    "/shadow-open": page("Open shadow", SHADOW % "open"),
    "/shadow-closed": page("Closed shadow", SHADOW % "closed"),
    "/upload": page(
        "Upload",
        "<label for='doc'>Document</label><input id='doc' type='file'>"
        "<p id='chosen'></p><script>document.getElementById('doc').onchange = (e) => {"
        "const f = e.target.files[0]; document.getElementById('chosen').textContent ="
        " f ? f.name + ' (' + f.size + ' bytes)' : 'none'; };</script>",
    ),
    "/download": page("Download", "<a id='get' href='/report.csv'>Download the report</a>"),
    "/popup": page(
        "Popup",
        "<button id='open' type='button'>Open</button><p id='state'></p>"
        "<script>document.getElementById('open').onclick = () => {"
        "window.open('/child', '_blank'); document.getElementById('state').textContent"
        " = 'opened'; };</script>",
    ),
    "/child": page(
        "Child",
        "<button id='done' type='button'>Done</button><p id='msg'></p>"
        "<script>document.getElementById('done').onclick = () => {"
        "document.getElementById('msg').textContent = 'done'; };</script>",
    ),
    "/lookup": page(
        "Lookup",
        "<button id='pick' type='button'>Pick</button><p id='picked'></p>"
        "<button id='after' type='button'>After</button>"
        "<script>document.getElementById('pick').onclick = () => {"
        "window.open('/picker', 'picker', 'width=420,height=300,resizable=no'); };</script>",
    ),
    "/picker": page(
        "Picker",
        "<button id='choose' type='button'>Choose</button>"
        "<script>document.getElementById('choose').onclick = () => {"
        "window.opener.document.getElementById('picked').textContent = 'A-1';"
        " window.close(); };</script>",
    ),
    "/dialogs": page(
        "Dialogs",
        "<button id='ask' type='button'>Delete</button>"
        "<button id='tell' type='button'>Save</button>"
        "<button id='name' type='button'>Name</button><p id='out'></p><script>"
        "const out = document.getElementById('out');"
        "document.getElementById('ask').onclick = () => { out.textContent ="
        " confirm('Delete it?') ? 'deleted' : 'kept'; };"
        "document.getElementById('tell').onclick = () => { alert('Saved');"
        " out.textContent = 'alerted'; };"
        "document.getElementById('name').onclick = () => { const n = prompt('Your name?');"
        " out.textContent = n ? 'Hello ' + n : 'no name'; };</script>",
    ),
    "/async": page(
        "Async",
        "<button id='load' type='button'>Load</button><div id='list'></div>"
        "<p id='picked'></p><script>document.getElementById('load').onclick = () =>"
        " fetch('/api/slow').then(r => r.json()).then(d => {"
        " const box = document.getElementById('list');"
        " for (const n of d.items) { const b = document.createElement('button');"
        " b.type = 'button'; b.id = 'pick-' + n; b.textContent = 'Pick ' + n;"
        " b.onclick = () => { document.getElementById('picked').textContent = n; };"
        " box.append(b); } });</script>",
    ),
    "/select": page(
        "Select",
        "<label for='size'>Size</label><select id='size'>"
        "<option value='s'>Small</option><option value='m'>Medium</option>"
        "<option value='l'>Large</option></select><p id='sz'></p><script>"
        "document.getElementById('size').onchange = (e) => {"
        "document.getElementById('sz').textContent = e.target.value; };</script>",
    ),
    "/drag": page(
        "Drag",
        "<div id='item' draggable='true' style='width:80px;height:30px;"
        "background:#ccc'>Item</div><div id='zone' style='width:200px;height:80px;"
        "border:1px solid'>Drop here</div><p id='dropped'></p><script>"
        "const z = document.getElementById('zone');"
        "document.getElementById('item').ondragstart = (e) =>"
        " e.dataTransfer.setData('text/plain', 'item');"
        "z.ondragover = (e) => e.preventDefault();"
        "z.ondrop = (e) => { e.preventDefault();"
        " document.getElementById('dropped').textContent = 'dropped'; };</script>",
    ),
    "/canvas": page(
        "Canvas",
        "<canvas id='pad' width='200' height='100' style='border:1px solid'>"
        "</canvas><script>const c = document.getElementById('pad');"
        "c.onclick = (e) => { const g = c.getContext('2d');"
        " g.fillRect(e.offsetX, e.offsetY, 6, 6); };</script>",
    ),
    "/sw": page(
        "Service worker",
        "<p id='sw'>registering</p><button id='hello' type='button'>"
        "Hello</button><script>navigator.serviceWorker.register('/sw.js').then(() =>"
        " { document.getElementById('sw').textContent = 'registered'; });"
        "document.getElementById('hello').onclick = () => {"
        " document.getElementById('sw').textContent = 'hello'; };</script>",
    ),
    "/slow-form": page(
        "Slow form",
        "<form id='slow' method='post' action='/slow-form'>"
        "<label for='who'>Name</label><input id='who' name='who'>"
        "<button id='send' type='submit'>Send</button></form>",
    ),
    "/slow-done": page(
        "Done", "<h1 id='done'>Received</h1><a id='again' href='/slow-form'>Again</a>"
    ),
}
SW_JS = "self.addEventListener('fetch', () => {});"


def cross_frame_page(other: str) -> str:
    return page("Cross-origin frame",
                f"<h1>Frame</h1><iframe id='inner' src='{other}/frame'></iframe>")  # fmt: skip


@dataclass
class Running:
    main: str
    other: str
    servers: list = field(default_factory=list)
    hits: list = field(default_factory=list)  # (method, path) the main server answered

    def stop(self) -> None:
        for s in self.servers:
            s.shutdown()
            s.server_close()


def _handler(running: Running, is_main: bool):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, body: str | bytes, ctype="text/html; charset=utf-8", status=200,
                  headers=None):  # fmt: skip
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            path = urlsplit(self.path).path
            if is_main:
                running.hits.append(("GET", path))
            if path == "/iframe-cross" and is_main:
                return self._send(cross_frame_page(running.other))
            if path == "/api/slow":
                time.sleep(SLOW_MS / 1000)
                return self._send('{"items": ["alpha", "beta"]}', "application/json")
            if path == "/report.csv":
                return self._send("a,b\n1,2\n", "text/csv",
                                  headers={"Content-Disposition": "attachment; filename=report-2026.csv"})  # fmt: skip
            if path == "/sw.js":
                return self._send(SW_JS, "text/javascript")
            if path in PAGES:
                return self._send(PAGES[path])
            return self._send(page("Not found", "<h1>Not found</h1>"), status=404)

        def do_POST(self):  # noqa: N802
            path = urlsplit(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            parse_qs(self.rfile.read(length).decode("utf-8") if length else "")
            running.hits.append(("POST", path))
            if path == "/slow-form":
                time.sleep(SLOW_MS / 1000)
                self.send_response(303)
                self.send_header("Location", "/slow-done")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            return self._send("", status=404)

    return Handler


def serve() -> Running:
    running = Running("", "")
    for is_main in (True, False):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(running, is_main))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        running.servers.append(server)
        if is_main:
            running.main = url
        else:
            running.other = url
    return running
