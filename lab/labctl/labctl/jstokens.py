"""A small JavaScript tokeniser and literal reader, to read downloaded code without running it.

Nothing here executes code. `tokens(source)` turns a script into tokens: names, numbers,
strings (decoded), template literals (their text parts and the tokens of each `${...}`),
regular expression literals, punctuators. Comments are skipped; licence banners (`/*!`,
`@license`, `@preserve`) are kept apart in `Tokens.banners`, because they name a library and its
version.

The hard parts of JavaScript's grammar for a tokeniser, and what is done about each:

- `/` starts a regular expression or is a division: it is a regular expression at the start,
  after an operator or an opening bracket, after a keyword that takes an expression (`return`,
  `typeof`, `case` ...); after `)`, `]`, a name, a number or a string it is a division. After `}`
  (a block or an object) it is a regular expression only when one follows in full and the text
  after it continues as an expression would.
- A string or a regular expression never runs past the end of its line: an unterminated one
  (a broken file, a quote in JSX text) ends there, so one stray quote cannot swallow a file.
- Template literals nest: `${` opens an expression that may hold strings, objects and further
  templates, read with the same tokeniser up to its matching `}`.
- Identifiers may hold escapes (`\\u0275fac`, as Angular writes them): they are decoded.

`Tokens` also gives the matching bracket of every bracket and the innermost bracket around every
token, and `literal()` reads a JavaScript literal (object, array, string, number, boolean) into
Python values, leaving anything else as an `Expr` (a span of tokens).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_WS = re.compile(r"[ \t\n\r\v\f   -     　﻿]+")
_IDENT = re.compile(
    r"(?:[A-Za-z_$]|[^\x00-\x7f\s  ﻿]|\\u[0-9a-fA-F]{4}|\\u\{[0-9a-fA-F]+\})"
    r"(?:[\w$]|[^\x00-\x7f\s  ﻿]|\\u[0-9a-fA-F]{4}|\\u\{[0-9a-fA-F]+\})*"
)
_NUM = re.compile(
    r"(?:0[xX][\da-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+"
    r"|(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d+)?)n?"
)
_STR = {
    "'": re.compile(r"'((?:[^'\\\n\r]|\\(?:\r\n|[\s\S]))*)('?)"),
    '"': re.compile(r'"((?:[^"\\\n\r]|\\(?:\r\n|[\s\S]))*)("?)'),
}
_REGEX = re.compile(
    r"/(?![*/])((?:[^\\/\[\n\r]|\\[^\n\r]|\[(?:[^\]\\\n\r]|\\[^\n\r])*\])+)/([A-Za-z]*)"
)
_TEMPLATE_PART = re.compile(r"[^`\\$]+|\\(?:\r\n|[\s\S])|\$\{|\$|`")
_PUNCT = sorted(
    ">>>= ... === !== **= <<= >>= >>> &&= ||= ??= => == != <= >= && || ?? ?. ++ -- += -= *= /= "
    "%= &= |= ^= ** << >> { } ( ) [ ] ; , < > + - * / % & | ^ ! ~ ? : = . @ #".split(),
    key=len,
    reverse=True,
)
_PUNCT_RE = re.compile("|".join(re.escape(p) for p in _PUNCT))
# Keywords after which `/` begins a regular expression (they take an expression).
_EXPR_KEYWORDS = frozenset(
    "return typeof instanceof in of new delete void throw case do else yield await extends".split()
)
_AFTER_REGEX = re.compile(r"\s*(?:[.),;\]}?:|&]|$)")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_BANNER = re.compile(r"^\s*[*!]|@license|@preserve|@version|\bv?\d+\.\d+\.\d+", re.IGNORECASE)


@dataclass(slots=True)
class Tok:
    """One token. `kind`: name, num, str, tmpl, regex or punct. `value`: the name, the number's
    text, the decoded string, a `Template`, the regular expression's source, the punctuator."""

    kind: str
    value: object
    pos: int

    def is_p(self, *values: str) -> bool:
        return self.kind == "punct" and self.value in values

    def is_n(self, *values: str) -> bool:
        return self.kind == "name" and (not values or self.value in values)


@dataclass(slots=True)
class Template:
    """A template literal: `strings` are the text parts (decoded), `exprs` the tokens of each
    `${...}` between them (one fewer than `strings`)."""

    strings: list[str]
    exprs: list[list[Tok]]


def cook(raw: str) -> str:
    """The value of a string literal's text: escapes decoded, a line continuation removed."""
    if "\\" not in raw:
        return raw
    out: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c != "\\" or i + 1 >= n:
            out.append(c)
            i += 1
            continue
        e = raw[i + 1]
        if e in _ESCAPES and not (e == "0" and i + 2 < n and raw[i + 2].isdigit()):
            out.append(_ESCAPES[e])
            i += 2
        elif e == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", raw[i + 2 : i + 4]):
            out.append(chr(int(raw[i + 2 : i + 4], 16)))
            i += 4
        elif e == "u" and raw[i + 2 : i + 3] == "{":
            end = raw.find("}", i + 3)
            try:
                out.append(chr(int(raw[i + 3 : end], 16)))
                i = end + 1
            except (ValueError, OverflowError):
                out.append(e)
                i += 2
        elif e == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", raw[i + 2 : i + 6]):
            out.append(chr(int(raw[i + 2 : i + 6], 16)))
            i += 6
        elif e == "\r":
            i += 3 if raw[i + 2 : i + 3] == "\n" else 2
        elif e in "\n  ":
            i += 2
        else:
            out.append(e)
            i += 2
    return "".join(out)


def _ident(raw: str) -> str:
    return cook(raw) if "\\" in raw else raw


class _Scanner:
    def __init__(self, src: str) -> None:
        self.src = src
        self.banners: list[str] = []

    def regex_ok(self, prev: Tok | None) -> bool:
        if prev is None:
            return True
        if prev.kind == "punct":
            return prev.value not in (")", "]", "}", "++", "--")
        if prev.kind == "name":
            return prev.value in _EXPR_KEYWORDS
        return False

    def scan(self, i: int, until_brace: bool = False) -> tuple[list[Tok], int]:
        src, n = self.src, len(self.src)
        out: list[Tok] = []
        depth = 0
        if i == 0 and src.startswith("#!"):
            i = src.find("\n") if "\n" in src else n
        while i < n:
            m = _WS.match(src, i)
            if m:
                i = m.end()
                continue
            c = src[i]
            if c == "/" and src.startswith("//", i):
                end = src.find("\n", i)
                end = n if end < 0 else end
                if src.startswith("//!", i) or "@license" in src[i:end]:
                    self.banners.append(src[i + 2 : end].strip())
                i = end
                continue
            if c == "/" and src.startswith("/*", i):
                end = src.find("*/", i + 2)
                end = n if end < 0 else end
                text = src[i + 2 : end]
                if _BANNER.search(text[:400]) and len(self.banners) < 200:
                    self.banners.append(text[:400].strip())
                i = end + 2
                continue
            if c == "<" and src.startswith("<!--", i):  # an HTML comment opener: a line comment
                end = src.find("\n", i)
                i = n if end < 0 else end
                continue
            prev = out[-1] if out else None
            if c in "'\"":
                m = _STR[c].match(src, i)
                out.append(Tok("str", cook(m.group(1)), i))
                i = m.end()
                continue
            if c == "`":
                tok, i = self.template(i)
                out.append(tok)
                continue
            if c == "/" and self.regex_ok(prev) or (c == "/" and prev is not None
                                                    and prev.is_p("}")):  # fmt: skip
                m = _REGEX.match(src, i)
                if m and (not prev or not prev.is_p("}") or _AFTER_REGEX.match(src, m.end())):
                    out.append(Tok("regex", m.group(1), i))
                    i = m.end()
                    continue
            if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
                m = _NUM.match(src, i)
                if m:
                    out.append(Tok("num", m.group(), i))
                    i = m.end()
                    continue
            m = _IDENT.match(src, i)
            if m:
                out.append(Tok("name", _ident(m.group()), i))
                i = m.end()
                continue
            m = _PUNCT_RE.match(src, i)
            if not m:  # a character JavaScript does not have: skip it
                i += 1
                continue
            p = m.group()
            if p == "?." and i + 2 < n and src[i + 2].isdigit():
                p = "?"
            if until_brace:
                if p == "{":
                    depth += 1
                elif p == "}":
                    if depth == 0:
                        return out, i + 1
                    depth -= 1
            out.append(Tok("punct", p, i))
            i += len(p)
        return out, i

    def template(self, start: int) -> tuple[Tok, int]:
        src, n = self.src, len(self.src)
        strings: list[str] = []
        exprs: list[list[Tok]] = []
        buf: list[str] = []
        i = start + 1
        while i < n:
            m = _TEMPLATE_PART.match(src, i)
            if not m:
                break
            part = m.group()
            if part == "`":
                i = m.end()
                break
            if part == "${":
                strings.append(cook("".join(buf)))
                buf = []
                toks, i = self.scan(m.end(), until_brace=True)
                exprs.append(toks)
                continue
            buf.append(part)
            i = m.end()
        else:
            i = n
        strings.append(cook("".join(buf)))
        return Tok("tmpl", Template(strings, exprs), start), i


@dataclass
class Tokens:
    """The tokens of one script, with the matching bracket of each bracket (`pairs`) and the
    index of the innermost open bracket around each token (`parent`, -1 at the top)."""

    toks: list[Tok]
    banners: list[str] = field(default_factory=list)
    pairs: dict[int, int] = field(default_factory=dict)
    parent: list[int] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.toks)

    def __getitem__(self, i: int) -> Tok:
        return self.toks[i]

    def at(self, i: int) -> Tok | None:
        return self.toks[i] if 0 <= i < len(self.toks) else None


def _link(toks: list[Tok]) -> tuple[dict[int, int], list[int]]:
    pairs: dict[int, int] = {}
    parent: list[int] = []
    stack: list[int] = []
    close = {")": "(", "]": "[", "}": "{"}
    for i, t in enumerate(toks):
        if t.kind == "punct" and t.value in close:
            # an unbalanced closer is ignored; a mismatched one closes back to its opener
            while stack and toks[stack[-1]].value != close[t.value]:
                stack.pop()
            if stack:
                o = stack.pop()
                pairs[o] = i
                pairs[i] = o
        parent.append(stack[-1] if stack else -1)
        if t.kind == "punct" and t.value in ("(", "[", "{"):
            stack.append(i)
    return pairs, parent


def tokens(source: str) -> Tokens:
    """Tokenise a script. Never raises on bad input: what cannot be read is skipped."""
    sc = _Scanner(source)
    toks, _ = sc.scan(0)
    pairs, parent = _link(toks)
    return Tokens(toks, sc.banners, pairs, parent)


def sub(toks: list[Tok]) -> Tokens:
    """A `Tokens` over a token list (a template literal's expression)."""
    pairs, parent = _link(toks)
    return Tokens(toks, [], pairs, parent)


# --- literals ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Expr:
    """Something that is not a literal: the tokens [start, end) of the expression."""

    start: int
    end: int


class Obj(dict):
    """An object literal: keys to values (a literal, or an `Expr`), with its span and the span of
    each value (`value_spans`)."""

    def __init__(self, start: int, end: int) -> None:
        super().__init__()
        self.span = (start, end)
        self.value_spans: dict[str, tuple[int, int]] = {}


class Arr(list):
    """An array literal, with its span and each element's span."""

    def __init__(self, start: int, end: int) -> None:
        super().__init__()
        self.span = (start, end)
        self.spans: list[tuple[int, int]] = []


def skip_expr(t: Tokens, i: int, stop: tuple[str, ...] = (",", ";")) -> int:
    """The index just after one expression starting at `i`: before a `,`, `;` or a closing
    bracket at the same depth."""
    n = len(t)
    while i < n:
        tok = t.toks[i]
        if tok.kind == "punct":
            if tok.value in ("(", "[", "{"):
                i = t.pairs.get(i, n - 1) + 1
                continue
            if tok.value in (")", "]", "}") or tok.value in stop:
                return i
        i += 1
    return i


def value(t: Tokens, start: int, end: int, depth: int = 0):
    """The value of the expression [start, end): a literal, else an `Expr`."""
    if end <= start:
        return Expr(start, end)
    first = t.toks[start]
    if end - start == 1:
        if first.kind == "str":
            return first.value
        if first.kind == "num":
            try:
                text = str(first.value).replace("_", "").rstrip("n")
                return int(text, 0) if re.fullmatch(r"0[xXbBoO].*|\d+", text) else float(text)
            except ValueError:
                return Expr(start, end)
        if first.kind == "tmpl" and not first.value.exprs:
            return first.value.strings[0]
        if first.is_n("true", "false"):
            return first.value == "true"
        if first.is_n("null"):
            return None
    if end - start == 2 and first.is_p("!") and t.toks[start + 1].kind == "num":
        return t.toks[start + 1].value == "0"  # `!0` is true, `!1` false (minified booleans)
    if end - start == 2 and first.is_p("-") and t.toks[start + 1].kind == "num":
        v = value(t, start + 1, end, depth)
        return -v if isinstance(v, (int, float)) else Expr(start, end)
    if depth < 40 and first.is_p("{") and t.pairs.get(start) == end - 1:
        return obj(t, start, depth + 1)
    if depth < 40 and first.is_p("[") and t.pairs.get(start) == end - 1:
        return arr(t, start, depth + 1)
    return Expr(start, end)


def obj(t: Tokens, start: int, depth: int = 0) -> Obj:
    """Read the object literal whose `{` is at `start`."""
    close = t.pairs.get(start, len(t) - 1)
    out = Obj(start, close + 1)
    i = start + 1
    while i < close:
        tok = t.toks[i]
        if tok.is_p(","):
            i += 1
            continue
        if tok.is_p("..."):
            i = skip_expr(t, i + 1)
            continue
        key = None
        # accessors and methods: get x(){}, async x(){}, *x(){}
        if tok.is_p("*"):
            i += 1
            tok = t.toks[i] if i < close else tok
        if (tok.is_n("get", "set", "async", "static") and i + 1 < close
                and t.toks[i + 1].kind in ("name", "str", "num")
                and i + 2 < close and t.toks[i + 2].is_p("(")):  # fmt: skip
            i += 1
            tok = t.toks[i]
        if tok.kind in ("name", "str", "num"):
            key = str(tok.value)
            i += 1
        elif tok.is_p("["):
            i = t.pairs.get(i, close) + 1
        else:
            i = skip_expr(t, i + 1)
            continue
        nxt = t.toks[i] if i < close else None
        if nxt is not None and nxt.is_p(":"):
            end = skip_expr(t, i + 1)
            if key is not None:
                out[key] = value(t, i + 1, end, depth)
                out.value_spans[key] = (i + 1, end)
            i = end
        elif nxt is not None and nxt.is_p("("):  # a method: its parameters, then its body
            j = t.pairs.get(i, close) + 1
            body_end = t.pairs.get(j, j) + 1 if j < close and t.toks[j].is_p("{") else j
            if key is not None:
                out[key] = Expr(i, body_end)
                out.value_spans[key] = (i, body_end)
            i = body_end
        elif nxt is not None and nxt.is_p("="):  # a default in a pattern: `{a = 1}`
            i = skip_expr(t, i + 1)
        else:  # shorthand `{a, b}`
            if key is not None:
                out[key] = Expr(i - 1, i)
                out.value_spans[key] = (i - 1, i)
    return out


def arr(t: Tokens, start: int, depth: int = 0) -> Arr:
    """Read the array literal whose `[` is at `start`."""
    close = t.pairs.get(start, len(t) - 1)
    out = Arr(start, close + 1)
    i = start + 1
    while i < close:
        if t.toks[i].is_p(","):
            i += 1
            continue
        end = skip_expr(t, i)
        if t.toks[i].is_p("..."):
            out.append(Expr(i, end))
        else:
            out.append(value(t, i, end, depth))
        out.spans.append((i, end))
        i = end
    return out


def call_args(t: Tokens, open_paren: int) -> list[tuple[int, int]]:
    """The spans of the arguments of the call whose `(` is at `open_paren`."""
    close = t.pairs.get(open_paren)
    if close is None:
        return []
    out = []
    i = open_paren + 1
    while i < close:
        end = skip_expr(t, i)
        out.append((i, end))
        i = end + 1  # past the comma (or a stray `;`, as in `for(;;)`)
    return out
