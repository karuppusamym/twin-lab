"""Compile Caseboard with `javac` into classes/ (Git-ignored). Standard library only.

    python reference/web/caseboard-jsf/build.py

Needs a JDK 11 or newer on PATH (or under JAVA_HOME) and the jars in lib/ (`fetch_libs.py`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _sibling(name: str):
    """A module of this folder, loaded by its path under a name of its own: `flow`, `run` and
    `build` are names other folders of the repository use too."""
    import importlib.util

    full = f"caseboard_jsf_{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


fetch_libs = _sibling("fetch_libs")
SRC = HERE / "src"
CLASSES = HERE / "classes"
WEBAPP = HERE / "webapp"


def tool(name: str) -> str | None:
    """`java` or `javac`: from JAVA_HOME when it is set and has it, else from PATH."""
    home = os.environ.get("JAVA_HOME")
    if home:
        for candidate in (Path(home) / "bin" / name, Path(home) / "bin" / f"{name}.exe"):
            if candidate.is_file():
                return str(candidate)
    return shutil.which(name)


def unavailable() -> str | None:
    """Why Caseboard cannot be built and started here (None: it can)."""
    if not tool("java") or not tool("javac"):
        return "no JDK: `java` and `javac` are not on PATH (and not under JAVA_HOME)"
    lacking = fetch_libs.missing()
    if lacking:
        return (
            f"{len(lacking)} jar(s) missing from {fetch_libs.LIB} (run fetch_libs.py): "
            + ", ".join(lacking[:3])
        )
    return None


def classpath() -> str:
    return os.pathsep.join([str(CLASSES), *(str(p) for p in fetch_libs.jar_paths())])


def build(force: bool = False) -> Path:
    """Compile the sources unless classes/ is newer than every one of them."""
    reason = unavailable()
    if reason:
        raise RuntimeError(reason)
    sources = sorted(SRC.rglob("*.java"))
    stamp = CLASSES / "caseboard" / "Main.class"
    if (
        not force
        and stamp.is_file()
        and stamp.stat().st_mtime >= max(s.stat().st_mtime for s in sources)
    ):
        return CLASSES
    CLASSES.mkdir(exist_ok=True)
    jars = os.pathsep.join(str(p) for p in fetch_libs.jar_paths())
    done = subprocess.run(
        [tool("javac"), "-nowarn", "-encoding", "UTF-8", "--release", "11", "-cp", jars,
         "-d", str(CLASSES), *(str(s) for s in sources)],
        capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    if done.returncode != 0:
        raise RuntimeError("javac failed:\n" + done.stdout + done.stderr)
    return CLASSES


if __name__ == "__main__":
    print(build(force=True))
