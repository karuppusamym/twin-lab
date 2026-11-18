"""Download the jars Caseboard needs from Maven Central into lib/, each checked against its SHA-1.

    python reference/web/caseboard-jsf/fetch_libs.py            # download what is missing
    python reference/web/caseboard-jsf/fetch_libs.py --check    # only say what is there

Only the Python standard library. The jars are not committed (lib/ is Git-ignored). The
checksums below are the ones Maven Central publishes beside each jar (`<jar>.sha1`); a file
that does not match is deleted and the script fails.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE / "lib"
CENTRAL = "https://repo1.maven.org/maven2/"
JETTY = "9.4.57.v20241219"

# (group path, artifact, version, sha1, bytes)
JARS = [
    ("javax/servlet", "javax.servlet-api", "3.1.0", "3cd63d075497751784b2fa84be59432f4905bf7c", 95806),
    ("javax/servlet/jsp", "javax.servlet.jsp-api", "2.3.3", "81191ab80e342912dc9cea735c30ff4eddc64de3", 54200),
    ("javax/enterprise", "cdi-api", "1.2", "53bba91dc3968adf411e076df020cf207283d7dc", 71075),
    ("javax/inject", "javax.inject", "1", "6975da39a7040257bd51d21a231b76c915872d38", 2497),
    ("org/glassfish", "javax.faces", "2.3.9", "ce120b9cb924b78fd3b465e6f8a402c1e5c25b84", 3578976),
    ("org/glassfish", "javax.el", "3.0.0", "dd532526e7c8de48e40419e6af1183658a973379", 224014),
    ("org/eclipse/jetty", "jetty-server", JETTY, "ad3baf52b98b4a32f5714fe2e58ac0e502b4e4d8", 739348),
    ("org/eclipse/jetty", "jetty-servlet", JETTY, "3e648eebddbf5ad0c0f7698e50c6a69c4a77fd95", 146064),
    ("org/eclipse/jetty", "jetty-webapp", JETTY, "3ef93ab9a81547d0def15b8a2d1d6bed7da4bb9c", 140308),
    ("org/eclipse/jetty", "jetty-util", JETTY, "7bf7ea75644ac064199e1e32c66ccd312239f2dc", 588962),
    ("org/eclipse/jetty", "jetty-http", JETTY, "c7a3a9c599346708894cf355e03105937f45f427", 249911),
    ("org/eclipse/jetty", "jetty-io", JETTY, "bd0ca6e5c4314972cd91f427fa09dedfe3b84ff5", 183011),
    ("org/eclipse/jetty", "jetty-security", JETTY, "2b545f68d45b947fdc6e279a0e8ae3630ec10e05", 118496),
    ("org/eclipse/jetty", "jetty-xml", JETTY, "bfec69b645695b670435016d3ec733656f4f6818", 68894),
]  # fmt: skip


def jar_name(artifact: str, version: str) -> str:
    return f"{artifact}-{version}.jar"


def jar_paths() -> list[Path]:
    return [LIB / jar_name(a, v) for _, a, v, _, _ in JARS]


def sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()  # noqa: S324 - Maven Central's checksum


def missing() -> list[str]:
    """The jars that are not in lib/ or do not match their checksum (empty: ready to build)."""
    return [
        jar_name(a, v)
        for _, a, v, digest, _ in JARS
        if not (LIB / jar_name(a, v)).is_file() or sha1(LIB / jar_name(a, v)) != digest
    ]


def fetch() -> None:
    LIB.mkdir(exist_ok=True)
    for group, artifact, version, digest, size in JARS:
        name = jar_name(artifact, version)
        path = LIB / name
        if path.is_file() and sha1(path) == digest:
            print(f"ok       {name}")
            continue
        url = f"{CENTRAL}{group}/{artifact}/{version}/{name}"
        with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - fixed https URL
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
        print(f"{len(JARS) - len(lacking)} of {len(JARS)} jars present and verified, {total} bytes")
        return 1 if lacking else 0
    fetch()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
