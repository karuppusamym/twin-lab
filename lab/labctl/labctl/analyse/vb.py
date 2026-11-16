"""Just enough reading of UiPath expressions to follow how an address is put together.

An expression is split on top-level `+` / `&` into parts: string literals, plain variable names,
and anything else (kept as opaque text). Nothing is evaluated.
"""

from __future__ import annotations

import re
from typing import NamedTuple

IDENT = re.compile(r"^([A-Za-z_]\w*)(?:\.ToString(?:\(\))?)?$")


class Part(NamedTuple):
    kind: str  # "lit" | "var" | "expr" | "bind"
    value: str


def parse(raw: str | None) -> list[Part]:
    """`[in_Url + "/cases/" + caseNo]` -> lit/var/expr parts. Text without brackets is a literal."""
    if raw is None:
        return []
    text = raw.strip()
    if not (text.startswith("[") and text.endswith("]")):
        return [Part("lit", text)]
    return [_classify(tok) for tok in _split(text[1:-1]) if tok.strip()]


def _split(src: str) -> list[str]:
    tokens, current, depth, in_string, i = [], [], 0, False, 0
    while i < len(src):
        ch = src[i]
        if in_string:
            current.append(ch)
            if ch == '"':
                if src[i + 1 : i + 2] == '"':  # doubled quote is an escaped quote
                    current.append('"')
                    i += 1
                else:
                    in_string = False
        elif ch == '"':
            in_string = True
            current.append(ch)
        elif ch in "([{":
            depth += 1
            current.append(ch)
        elif ch in ")]}":
            depth -= 1
            current.append(ch)
        elif ch in "+&" and depth == 0:
            tokens.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    tokens.append("".join(current))
    return tokens


def _classify(token: str) -> Part:
    tok = token.strip()
    if (
        len(tok) >= 2
        and tok[0] == '"'
        and tok[-1] == '"'
        and '"' not in tok[1:-1].replace('""', "")
    ):
        return Part("lit", tok[1:-1].replace('""', '"'))
    m = IDENT.match(tok)
    if m:
        return Part("var", m.group(1))
    return Part("expr", tok)


def variable(raw: str | None) -> str | None:
    """The single variable an expression names, e.g. `[in_CaseNo]` -> `in_CaseNo`. Else None."""
    parts = parse(raw)
    return parts[0].value if len(parts) == 1 and parts[0].kind == "var" else None


def resolve(parts: list[Part], env: dict[str, list[Part]], depth: int = 0) -> list[Part]:
    """Replace variables whose definition is known. Unknown variables stay as they are."""
    out: list[Part] = []
    for part in parts:
        if part.kind == "var" and part.value in env and depth < 8:
            out.extend(resolve(env[part.value], env, depth + 1))
        else:
            out.append(part)
    return out
