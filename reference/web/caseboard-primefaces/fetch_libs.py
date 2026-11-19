"""Download the jars Caseboard PF needs from Maven Central, each checked against its SHA-1.

    python reference/web/caseboard-primefaces/fetch_libs.py            # download what is missing
    python reference/web/caseboard-primefaces/fetch_libs.py --check    # only say what is there

The JSF, servlet and Jetty jars are those of the plain JSF reference application
(`reference/web/caseboard-jsf/fetch_libs.py`, same pins, same `lib/` folder there): this script
asks it to fetch them. The one jar of its own is PrimeFaces, into this folder's `lib/`. PrimeFaces
10 needs nothing else at run time: its other dependencies are optional (file upload, PDF and Excel
export, bean validation) and not used here.

Only the Python standard library. The jars are not committed (lib/ is Git-ignored). The
checksums below are the ones Maven Central publishes beside each jar (`<jar>.sha1`); a file that
does not match is deleted and the script fails.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE / "lib"
CENTRAL = "https://repo1.maven.org/maven2/"
JSF_APP = HERE.parent / "caseboard-jsf"

# (group path, artifact, version, sha1, bytes)
JARS = [
    ("org/primefaces", "primefaces", "10.0.0", "e32fd22721d5cd0f89fb0cb025b073248196d721", 5233705),
]  # fmt: skip


def _jsf():
    """The plain JSF application's fetch_libs, loaded by its path under a name of its own."""
    full = "caseboard_jsf_fetch_libs"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, JSF_APP / "fetch_libs.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


def jar_name(artifact: str, version: str) -> str:
    return f"{artifact}-{version}.jar"


def own_paths() -> list[Path]:
    return [LIB / jar_name(a, v) for _, a, v, _, _ in JARS]


def jar_paths() -> list[Path]:
    """Every jar on the class path: the JSF application's and PrimeFaces."""
    return [*_jsf().jar_paths(), *own_paths()]


def sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()  # noqa: S324 - Maven Central's checksum


def missing() -> list[str]:
    """The jars that are not there or do not match their checksum (empty: ready to build)."""
    own = [
        jar_name(a, v)
        for _, a, v, digest, _ in JARS
        if not (LIB / jar_name(a, v)).is_file() or sha1(LIB / jar_name(a, v)) != digest
    ]
    return [*_jsf().missing(), *own]


def fetch() -> None:
    _jsf().fetch()
    LIB.mkdir(exist_ok=True)
    for group, artifact, version, digest, size in JARS:
        name = jar_name(artifact, version)
        path = LIB / name
        if path.is_file() and sha1(path) == digest:
            print(f"ok       {name}")
            continue
        url = f"{CENTRAL}{group}/{artifact}/{version}/{name}"
        with urllib.request.urlopen(url, timeout=300) as response:  # noqa: S310 - fixed https URL
            data = response.read()
        got = hashlib.sha1(data).hexdigest()  # noqa: S324
        if got != digest or len(data) != size:
            raise SystemExit(
                f"{name}: checksum {got} ({len(data)} bytes) is not the pinned {digest}"
            )
        path.write_bytes(data)
        print(f"fetched  {name} ({size} bytes, sha1 {digest})")


def main(argv: list[str]) -> int:
    if "--check" in argv:
        lacking = missing()
        for name in lacking:
            print(f"missing  {name}")
        total = sum(p.stat().st_size for p in jar_paths() if p.is_file())
        count = len(jar_paths())
        print(f"{count - len(lacking)} of {count} jars present and verified, {total} bytes")
        return 1 if lacking else 0
    fetch()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
