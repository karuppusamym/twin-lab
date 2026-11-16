"""Source analysers: read a real application's code and describe its screens, routes and tables.

Everything is static. No file from the analysed source is imported or executed.
"""

from __future__ import annotations

import re
from pathlib import Path

SKIP_DIRS = {
    "node_modules", ".git", ".venv", "venv", "env", "__pycache__", "dist", "build", ".next",
    "coverage", "tests", "test", "__tests__", ".pytest_cache", ".ruff_cache",
}  # fmt: skip
MAX_FILE_BYTES = 2 * 1024 * 1024


class UnsupportedStack(Exception):
    """The source uses a stack no analyser exists for yet."""


def files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    """Source files under `root`, skipping dependencies, builds, tests and oversized files."""
    out = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel[:-1]):
            continue
        if path.suffix in suffixes and path.is_file() and path.stat().st_size <= MAX_FILE_BYTES:
            out.append(path)
    return out


def segments(path: str) -> list[str | None]:
    """`/cases/:id`, `/cases/{id}` and `/cases/${id}` all become ["cases", None]."""
    path = re.split(r"[?#]", path, maxsplit=1)[0]
    out: list[str | None] = []
    for seg in path.strip("/").split("/"):
        if not seg:
            continue
        param = seg.startswith(":") or seg.startswith("{") or seg.startswith("${") or seg == "*"
        out.append(None if param else seg)
    return out


def match_score(pattern: str, path: str) -> int | None:
    """How well `path` fits `pattern`, or None if it cannot.

    Equal literal segments score 2, a variable meeting a parameter scores 1, and a variable
    meeting a literal (or the reverse) scores 0: `/cases/${id}` prefers `/cases/{case_no}` over
    `/cases/search`, while `/cases/search` prefers the literal route.
    """
    a, b = segments(pattern), segments(path)
    if len(a) != len(b):
        return None
    score = 0
    for x, y in zip(a, b, strict=True):
        if x is not None and y is not None:
            if x != y:
                return None
            score += 2
        elif x is None and y is None:
            score += 1
    return score


def same_shape(a: str, b: str) -> bool:
    """Whether two paths describe the same route: literals equal, parameters in the same places."""
    x, y = segments(a), segments(b)
    return len(x) == len(y) and all(p == q for p, q in zip(x, y, strict=True))


def best_match(path: str, candidates: list, key) -> object | None:
    """The candidate whose pattern fits `path` with the most literal segments (router order)."""
    best, best_score = None, -1
    for cand in candidates:
        score = match_score(key(cand), path)
        if score is not None and score > best_score:
            best, best_score = cand, score
    return best
