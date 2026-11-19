"""Build Caseboard if needed and start it on 127.0.0.1. Standard library only.

    python reference/web/caseboard-primefaces/run.py --port 8139      # Ctrl+C stops it

Tests use `serve()`: a fresh instance (the twelve seeded cases, no session) on a free port,
stopped with `.stop()`.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _sibling(name: str):
    """A module of this folder, loaded by its path under a name of its own: `flow`, `run` and
    `build` are names other folders of the repository use too."""
    import importlib.util

    full = f"caseboard_pf_{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


build_mod = _sibling("build")
DEFAULT_PORT = 8139


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Running:
    """One Caseboard process. `base` is its address, without a trailing slash."""

    def __init__(self, process: subprocess.Popen, port: int, log: Path):
        self.process, self.port, self.log = process, port, log
        self.base = f"http://127.0.0.1:{port}"

    def listening(self) -> bool:
        with socket.socket() as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", self.port)) == 0

    def stop(self) -> None:
        """End the server and make sure nothing listens on its port any more."""
        if self.process.poll() is None:
            if sys.platform == "win32":
                # `java` on PATH can be a launcher that starts the real JVM as its child:
                # ending the launcher alone would leave the server running.
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                               capture_output=True, timeout=30)  # fmt: skip
            else:
                self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=15)
        deadline = time.monotonic() + 15
        while self.listening():
            if time.monotonic() > deadline:
                raise RuntimeError(f"Caseboard still listens on port {self.port} after stop()")
            time.sleep(0.2)

    def __enter__(self) -> Running:
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def serve(port: int | None = None, timeout: float = 60.0) -> Running:
    """Start a fresh instance and wait until its sign-in page answers."""
    build_mod.build()
    port = port or free_port()
    log = Path(tempfile.gettempdir()) / f"caseboard-primefaces-{port}.log"
    with log.open("wb") as out:
        process = subprocess.Popen(
            [build_mod.tool("java"), "-cp", build_mod.classpath(), "caseboardpf.Main",
             "--port", str(port), "--webapp", str(build_mod.WEBAPP)],
            stdout=out, stderr=subprocess.STDOUT, cwd=str(HERE),
        )  # fmt: skip
    running = Running(process, port, log)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            with urllib.request.urlopen(f"{running.base}/login.xhtml", timeout=5) as response:  # noqa: S310
                if response.status == 200:
                    return running
        except OSError:
            time.sleep(0.3)
    running.stop()
    tail = log.read_text("utf-8", "replace")[-3000:]
    raise RuntimeError(f"Caseboard did not start on port {port}:\n{tail}")


def main(argv: list[str]) -> int:
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else DEFAULT_PORT
    running = serve(port)
    print(f"Caseboard PF (PrimeFaces) is at {running.base}/  (log: {running.log}); Ctrl+C stops it")
    try:
        running.process.wait()
    except KeyboardInterrupt:
        pass
    finally:
        running.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
