"""Request matching for condition rules: `"GET /api/cases/search*"`, method optional, `*` glob.

- `"GET /api/cases/*"`: method GET (case-insensitive), path glob.
- `"/api/cases/*"` or `"* /api/cases/*"`: any method.
- `"*"`: everything.
- `*` matches any run of characters, `/` included. No other character is special.
- The glob is matched against the path only, unless it contains `?`; then against
  `path?query`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_METHOD = re.compile(r"^[A-Za-z]+$")


class PatternError(ValueError):
    pass


@dataclass(frozen=True)
class Matcher:
    method: str | None  # upper case, None = any
    regex: re.Pattern[str]
    with_query: bool
    source: str

    def matches(self, method: str, path: str, query: str = "") -> bool:
        if self.method is not None and method.upper() != self.method:
            return False
        target = f"{path}?{query}" if self.with_query else path
        return self.regex.fullmatch(target) is not None


def compile_match(pattern: str) -> Matcher:
    text = pattern.strip()
    if not text:
        raise PatternError("match is empty")
    method: str | None = None
    glob = text
    head, sep, tail = text.partition(" ")
    if sep:
        tail = tail.strip()
        if head == "*":
            method = None
        elif _METHOD.match(head):
            method = head.upper()
        else:
            raise PatternError(f"{pattern!r}: expected 'METHOD /path', got method {head!r}")
        glob = tail
    elif _METHOD.match(text) and text != "*":
        raise PatternError(f"{pattern!r}: a path is needed after the method")
    if not glob or (glob[0] not in "/*"):
        raise PatternError(f"{pattern!r}: the path must start with '/' or '*'")
    if " " in glob:
        raise PatternError(f"{pattern!r}: the path cannot contain spaces")
    regex = re.compile(".*".join(re.escape(part) for part in glob.split("*")), re.DOTALL)
    return Matcher(method=method, regex=regex, with_query="?" in glob, source=pattern)
