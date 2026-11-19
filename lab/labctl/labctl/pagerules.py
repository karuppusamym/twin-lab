"""Field rules and page behaviour: what each recorded page checks and does, for the builder.

A rebuilt application behaves the same only when it checks what the original checks. A
recording shows the rules a person happened to trigger (a message appeared) and the rules the
HTML declares (`required`, `maxlength`, ...). The rest is in the application's JavaScript, which
the browser downloaded and the HAR holds. This module reads all three, never runs a script, and
writes per screen and per field:

- **Field rules**: from the DOM snapshots (`required`, `minlength`, `maxlength`, `min`, `max`,
  `step`, `pattern`, the input `type`, `readonly`, `disabled`, `autocomplete`, `inputmode`, the
  placeholder, tooltip, label, help text, character counter, option list, ASP.NET unobtrusive
  `data-val-*`, jQuery Validate `data-rule-*` / `data-msg-*`, PrimeFaces `data-p-*`, messages the
  page holds hidden), and from the application's own scripts: Angular `Validators.*` on a form
  control, jQuery Validate `rules` / `messages`, `setCustomValidity` with its message, plain
  checks (`if (!x.value)`, `.length < n`, `/re/.test(x.value)`, numbers compared) with the
  message shown beside them.
- **Cross-field rules**: two fields compared (equal, one not before the other), one of two
  required, jQuery `equalTo`, ASP.NET `data-val-equalto`.
- **Behaviour while typing or choosing**: masks and formatting (upper-casing, characters
  removed, a mask plugin), fields shown, hidden, enabled, disabled or made required by another
  field, dependent drop-downs (options loaded by an API call: linked to the API catalogue and the
  step), a submit button disabled until the form is valid, JSF / PrimeFaces partial updates. The
  recording first (the page before and after a typing or choosing step), then the code.
- **Page state**: what is disabled or read-only on load, tooltips, counters, and session-timeout
  warnings or sign-outs scheduled by `setTimeout` / `setInterval`, with their delay.

Every rule carries its evidence and a level: `seen` (the recording triggered it: its message
appeared), `declared` (in the HTML), `in code, not triggered` (the application's script has it;
the recording never set it off) or `in code, not tied to a field` (found in the script; which
field it belongs to could not be told).

How scripts are read. Only scripts of the application's own origin are analysed: the script
files the HAR holds and the inline scripts of its pages. A third-party script and a library file
of the application's own origin (jQuery, Angular's runtime, JSF's own script, ... by its name or
its banner) is named, not analysed. Each script is tokenised (strings, regular expressions as
opposed to division, template literals and comments are handled) and the token stream is matched
against the shapes above; nothing is executed. Minified code keeps strings, property names and
the shapes of calls, so a rule tied to a field id, name or form-control key, or to a message,
survives minification; what could not be tied to a field is listed as such.

Privacy. Scripts are not carried: only the rules and messages taken from them. A message is
interface text and kept literally, through `sanitize.mask_message` (a value-like word masked)
and the look's data check (`lookfeel.known_data`): no value the recording shows as data is
written. Labels, help texts, placeholders and options are read from the sanitised snapshots, as
`apps/<app>/pages/` has them. With `TWINLAB_PAGE_SCRIPTS=carry` (an owner who has the right to
share them) the application's own script files are also carried under `apps/<app>/scripts/`,
source maps stripped; the brief and the package refuse them when a script holds anything that
looks like a secret, a host other than the application's own, or a value the recording shows as
data. Library files are never carried.

Files: `apps/<app>/rules.json` (and with the opt-in `apps/<app>/scripts/`); the brief's section
*Field rules and page behaviour*.
"""

from __future__ import annotations

import ast
import hashlib
import json
import operator
import os
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from labctl import sanitize

FILE = "rules.json"
FORMAT = "twinlab-page-rules/1"
SCRIPTS_DIR = "scripts"
SCRIPTS_MANIFEST = "manifest.json"
SCRIPTS_ENV = "TWINLAB_PAGE_SCRIPTS"  # "carry": also carry the application's own script files
HEADING = "Field rules and page behaviour"

SEEN = "seen"
DECLARED = "declared"
CODE = "in code, not triggered"
LOOSE = "in code, not tied to a field"
LEVELS = (SEEN, DECLARED, CODE, LOOSE)
_RANK = {SEEN: 0, DECLARED: 1, CODE: 2, LOOSE: 3}

MAX_SCRIPT = 6 * 1024 * 1024  # bytes of one script analysed
MAX_LOOSE = 40  # rules not tied to a field, listed per application


def carry_scripts() -> bool:
    return (os.environ.get(SCRIPTS_ENV) or "").strip().lower() in ("carry", "1", "yes", "true")


# =================================================================================================
# The tokeniser
# =================================================================================================


@dataclass(frozen=True)
class Tok:
    """One JavaScript token. `text` is the decoded value of a string or a template piece, the
    body of a regular expression (`flags` its flags), the source of anything else. A template
    piece's `flags` says where it stands: `full` (no expression), `head`, `mid` or `tail`."""

    kind: str  # name | num | str | tmpl | regex | punct | comment
    text: str
    pos: int
    flags: str = ""


_PUNCT = sorted(
    """>>>= ... === !== **= <<= >>= >>> &&= ||= ??= => == != <= >= && || ?? ?. ++ -- += -= *= /=
    %= &= |= ^= ** << >> { } ( ) [ ] ; , < > + - * / % & | ^ ! ~ ? : = . @ #""".split(),
    key=len,
    reverse=True,
)
_REGEX_AFTER_WORD = {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
                     "throw", "case", "do", "else", "yield", "await"}  # fmt: skip
_NAME = re.compile(r"[A-Za-z_$\u0080-\uffff][\w$\u0080-\uffff]*")
_NUMBER = re.compile(
    r"0[xX][0-9a-fA-F_]+n?|0[bB][01_]+n?|0[oO][0-7_]+n?"
    r"|(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d+)?n?"
)
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_SPACE = set(" \t\r\n\f\v\u00a0\ufeff\u2028\u2029")


def _escape(src: str, j: int, out: list[str]) -> int:
    """Decode the escape at src[j] (a backslash); return the index after it."""
    nxt = src[j + 1 : j + 2]
    if nxt == "u":
        if src[j + 2 : j + 3] == "{":
            end = src.find("}", j)
            try:
                out.append(chr(int(src[j + 3 : end], 16)))
            except (ValueError, OverflowError):
                pass
            return end + 1 if end > 0 else len(src)
        try:
            out.append(chr(int(src[j + 2 : j + 6], 16)))
        except ValueError:
            out.append("u")
            return j + 2
        return j + 6
    if nxt == "x":
        try:
            out.append(chr(int(src[j + 2 : j + 4], 16)))
        except ValueError:
            out.append("x")
            return j + 2
        return j + 4
    if nxt == "\r":  # a line continuation
        return j + 3 if src[j + 2 : j + 3] == "\n" else j + 2
    if nxt in ("\n", "\u2028", "\u2029"):
        return j + 2
    out.append(_ESCAPES.get(nxt, nxt))
    return j + 2


def _string(src: str, i: int) -> tuple[str, int]:
    quote, j, out = src[i], i + 1, []
    while j < len(src):
        ch = src[j]
        if ch == quote:
            return "".join(out), j + 1
        if ch == "\\":
            j = _escape(src, j, out)
            continue
        if ch == "\n":  # not closed on its line: what there is
            return "".join(out), j
        out.append(ch)
        j += 1
    return "".join(out), j


def _template_part(src: str, j: int) -> tuple[str, int, bool]:
    """A template literal's text from src[j] up to its closing backtick or the next `${`:
    (text, index after it, True when the template ends there)."""
    out = []
    while j < len(src):
        ch = src[j]
        if ch == "`":
            return "".join(out), j + 1, True
        if ch == "\\":
            j = _escape(src, j, out)
            continue
        if ch == "$" and src[j + 1 : j + 2] == "{":
            return "".join(out), j + 2, False
        out.append(ch)
        j += 1
    return "".join(out), j, True


def _regex_body(src: str, i: int) -> tuple[str, str, int] | None:
    """A regular expression literal starting at src[i] (a slash): (body, flags, end) or None."""
    j, in_class = i + 1, False
    while j < len(src):
        ch = src[j]
        if ch == "\\":
            j += 2
            continue
        if ch in "\n\r":
            return None
        if in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "/":
            m = re.compile(r"[A-Za-z]*").match(src, j + 1)
            return src[i + 1 : j], m.group(), m.end()
        j += 1
    return None


def _regex_allowed(prev: Tok | None) -> bool:
    """Whether a slash after `prev` starts a regular expression (else it divides)."""
    if prev is None:
        return True
    if prev.kind == "punct":
        return prev.text not in (")", "]", "++", "--")
    if prev.kind == "name":
        return prev.text in _REGEX_AFTER_WORD
    if prev.kind == "tmpl":
        return prev.flags in ("head", "mid")
    return False


def scan(src: str) -> tuple[list[Tok], list[Tok]]:
    """The tokens of a script and, apart, its comments. Never fails: what it cannot read is a
    one-character punctuator."""
    toks: list[Tok] = []
    comments: list[Tok] = []
    templates: list[int] = []  # brace depth at each open `${`
    depth, i, n = 0, 0, len(src)
    while i < n:
        c = src[i]
        if c in _SPACE:
            i += 1
            continue
        if c == "/" and src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            comments.append(Tok("comment", src[i:j], i))
            i = j
            continue
        if c == "/" and src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            comments.append(Tok("comment", src[i:j], i))
            i = j
            continue
        if c in "'\"":
            text, j = _string(src, i)
            toks.append(Tok("str", text, i))
            i = j
            continue
        if c == "`":
            text, j, done = _template_part(src, i + 1)
            toks.append(Tok("tmpl", text, i, "full" if done else "head"))
            if not done:
                templates.append(depth)
            i = j
            continue
        if c == "}" and templates and templates[-1] == depth:
            templates.pop()
            text, j, done = _template_part(src, i + 1)
            toks.append(Tok("tmpl", text, i, "tail" if done else "mid"))
            if not done:
                templates.append(depth)
            i = j
            continue
        if c.isdigit() or (c == "." and src[i + 1 : i + 2].isdigit()):
            m = _NUMBER.match(src, i)
            toks.append(Tok("num", m.group(), i))
            i = m.end()
            continue
        m = _NAME.match(src, i)
        if m:
            toks.append(Tok("name", m.group(), i))
            i = m.end()
            continue
        if c == "#" and _NAME.match(src, i + 1):  # a private name
            m = _NAME.match(src, i + 1)
            toks.append(Tok("name", "#" + m.group(), i))
            i = m.end()
            continue
        if c == "/" and _regex_allowed(toks[-1] if toks else None):
            found = _regex_body(src, i)
            if found:
                body, flags, j = found
                toks.append(Tok("regex", body, i, flags))
                i = j
                continue
        for p in _PUNCT:
            if src.startswith(p, i):
                break
        else:
            p = c
        if p == "{":
            depth += 1
        elif p == "}":
            depth -= 1
        toks.append(Tok("punct", p, i))
        i += len(p)
    return toks, comments


def tokenize(src: str) -> list[Tok]:
    """The tokens of a script, comments left out (`scan` gives them too)."""
    return scan(src)[0]


def number(text: str) -> float | int | None:
    """The value of a number token."""
    t = text.replace("_", "").rstrip("n")
    try:
        if t[:2].lower() in ("0x", "0b", "0o"):
            return int(t, 0)
        value = float(t)
    except ValueError:
        return None
    return int(value) if value.is_integer() and "." not in t and "e" not in t.lower() else value


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv}  # fmt: skip


def arithmetic(expr: str) -> float | None:
    """A constant arithmetic expression (`15 * 60 * 1e3`): numbers, + - * / and parentheses only.
    Read with Python's parser and walked by hand: nothing is evaluated that is not arithmetic."""
    if not re.fullmatch(r"[\d.eE+\-*/() ]{1,120}", expr or ""):
        return None
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = walk(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            a, b = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Div) and b == 0:
                raise ValueError
            return _OPS[type(node.op)](a, b)
        raise ValueError

    try:
        return float(walk(tree))
    except (ValueError, TypeError, RecursionError):
        return None


# =================================================================================================
# Messages
# =================================================================================================

# What a message about a field says, firmly enough to take a lone string for one.
FIELD_MESSAGE = re.compile(
    r"\b(required|must|invalid|not valid|at least \d|at most \d|no more than|too (?:short|long)|"
    r"format|please (?:enter|give|choose|select|fill)|(?:enter|give|choose|select|provide) "
    r"(?:a|an|the|your|one|at)|do(?:es)? not match|must match|differs?|not allowed|is missing|"
    r"cannot be (?:empty|blank)|can't be (?:empty|blank)|mandatory|is needed|whole number|"
    r"only (?:letters|digits|numbers)|exceeds?)\b",
    re.IGNORECASE,
)


def is_message(text: str) -> bool:
    """Whether a string reads as a message to a person (two words or more, no code)."""
    s = (text or "").strip()
    if not 4 <= len(s) <= 300 or len(s.split()) < 2:
        return False
    if re.search(r"[{}<>;=\\]|^[^\w\"'“‘(]|\.(js|css|html?|json)\b|https?:|\$\{", s):
        return False
    return len(re.findall(r"[^\W\d_]{2,}", s)) >= 2


_KINDS = [
    ("equals", r"\b(match|matches|differ|differs|same as|do not agree|does not agree|identical)\b"),
    ("order", r"\b(after|before|later than|earlier than|not precede)\b"),
    ("minlength", r"\b(at least \d+ (characters?|letters?|digits?)|too short|minimum of \d+)\b"),
    ("maxlength", r"\b(at most \d+|no more than \d+|too long|up to \d+ (characters?|letters?)|"
                  r"maximum of \d+ characters?)\b"),
    ("range", r"\bbetween\b"),
    ("email", r"\be-?mail\b"),
    ("number", r"\b(whole number|a number|numbers? only|digits? only|numeric)\b"),
    ("required", r"\b(required|is missing|cannot be empty|can't be (empty|blank)|mandatory|"
                 r"is needed|fill in|enter|give|choose|select|provide)\b"),
    ("pattern", r"\b(format|valid|such as|only letters|letters and digits|like)\b"),
]  # fmt: skip


def message_kind(text: str) -> str:
    """The rule a message speaks of, guessed from its words ("check" when it does not say)."""
    low = (text or "").lower()
    for kind, rx in _KINDS:
        if re.search(rx, low):
            return kind
    return "check"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"^[\W_]+|[\W_]+$", "", (text or "").lower())).strip()


def same_message(shown: str | None, written: str | None) -> bool:
    """Whether a message as the recording shows it (the sanitiser may have masked a word of it:
    `Xxxxx your full name.`) is the message the page or its code writes."""
    a, b = (shown or "").split(), (written or "").split()
    if not a or len(a) != len(b):
        return False
    return all(
        x == y or x == sanitize.mask(y) or y == sanitize.mask(x) for x, y in zip(a, b, strict=True)
    )


# =================================================================================================
# Reading a script
# =================================================================================================

ELEMENT_CALLS = {"getElementById", "querySelector", "querySelectorAll", "$", "jQuery",
                 "namedItem", "getElementsByName", "getElementsByClassName"}  # fmt: skip
VALUE_PROPS = {"value", "checked", "files", "selectedIndex", "valueAsNumber", "valueAsDate"}
COMPARE = {"<", ">", "<=", ">=", "==", "===", "!=", "!=="}
NG_VALIDATORS = {"required", "requiredTrue", "email", "minLength", "maxLength", "min", "max",
                 "pattern"}  # fmt: skip
EVENTS = {"change", "input", "keyup", "keydown", "keypress", "blur", "focusout", "click",
          "submit", "paste"}  # fmt: skip
HIDE_CLASSES = re.compile(r"^(hidden|d-none|hide|is-hidden|invisible|collapse|show|visible|"
                          r"ng-hide|u-hidden|sr-only)$")  # fmt: skip
SESSION = re.compile(r"session|sign[_ -]?out|signed[_ -]out|log[_ -]?out|logged[_ -]out|logoff|expire|"
                     r"expiry|idle|inactiv|keep[_ -]?alive", re.IGNORECASE)  # fmt: skip
MIN_SESSION_DELAY = 10_000  # ms: a timer shorter than this is not a session timer
ERROR_CONTEXT = re.compile(r"^(setCustomValidity|preventDefault|reportValidity|error|errors|"
                           r"invalid|showError|setError|addError|markInvalid|alert)$|error|invalid",
                           re.IGNORECASE)  # fmt: skip
_END = Tok("end", "", -1)


def selector_key(selector: str) -> str | None:
    """The id or name a CSS selector names in its last part (`form #email`, `input[name=x]`)."""
    last = re.split(r"[\s>+~]+", (selector or "").strip())[-1] if selector else ""
    m = re.search(r"#((?:\\.|[\w-])+)", last)
    if m:
        return m.group(1).replace("\\", "")
    m = re.search(r"""\[\s*name\s*[~|^$*]?=\s*["']?([^"'\]]+)["']?\s*\]""", last)
    if m:
        return m.group(1)
    return None


class Code:
    """One script, tokenised, with the indexes the matchers use."""

    def __init__(self, src: str, name: str = "script", me: str | None = None):
        self.name = name
        self.me = me  # the field an inline handler (`oninput="..."`) belongs to
        self.toks, self.comments = scan(src or "")
        self.n = len(self.toks)
        self.close: dict[int, int] = {}
        self.open: dict[int, int] = {}
        self.parent: list[int] = []
        stack: list[int] = []
        pair = {")": "(", "]": "[", "}": "{"}
        for i, t in enumerate(self.toks):
            self.parent.append(stack[-1] if stack else -1)
            if t.kind != "punct":
                continue
            if t.text in "([{":
                stack.append(i)
            elif t.text in ")]}":
                while stack and self.toks[stack[-1]].text != pair[t.text]:
                    stack.pop()  # unbalanced code: forget the stray opener
                if stack:
                    o = stack.pop()
                    self.close[o] = i
                    self.open[i] = o
        self.vars: dict[str, list[tuple[int, str]]] = {}
        self.consts: dict[str, float] = {}
        self._bindings()
        self.handlers: list[tuple[int, int, str]] = []  # (lo, hi, key of the element)

    # --- tokens ---------------------------------------------------------------------------------

    def t(self, i: int) -> Tok:
        return self.toks[i] if 0 <= i < self.n else _END

    def p(self, i: int, *texts: str) -> bool:
        t = self.t(i)
        return t.kind == "punct" and t.text in texts

    def w(self, i: int, *texts: str) -> bool:
        t = self.t(i)
        return t.kind == "name" and (not texts or t.text in texts)

    def end_of(self, i: int) -> int:
        """The index after the bracket group opening at i (or i + 1)."""
        return self.close.get(i, i) + 1

    def expression_end(self, i: int, stop: tuple[str, ...] = (",", ";")) -> int:
        """The index of the first `stop` punctuator or closing bracket at this level from i."""
        j = i
        while j < self.n:
            t = self.toks[j]
            if t.kind == "punct":
                if t.text in stop or t.text in ")]}":
                    return j
                if t.text in "([{":
                    j = self.end_of(j)
                    continue
            j += 1
        return j

    def args(self, i: int) -> list[tuple[int, int]]:
        """The top-level arguments of the call whose `(` is at i: [(lo, hi), ...]."""
        if not self.p(i, "("):
            return []
        end = self.close.get(i, i)
        out, lo = [], i + 1
        while lo < end:
            hi = self.expression_end(lo)
            hi = min(hi, end)
            out.append((lo, hi))
            lo = hi + 1
        return out

    def strings(self, lo: int, hi: int):
        """(index, text) of every string in a range; a template with expressions is joined with
        `{…}` where an expression stood."""
        i = lo
        while i < hi:
            t = self.toks[i]
            if t.kind == "str":
                yield i, t.text
            elif t.kind == "tmpl" and t.flags == "full":
                yield i, t.text
            elif t.kind == "tmpl" and t.flags == "head":
                parts, j = [t.text], i + 1
                while j < self.n and j < hi + 200:
                    u = self.toks[j]
                    if u.kind == "tmpl" and u.flags in ("mid", "tail"):
                        parts.append(u.text)
                        if u.flags == "tail":
                            break
                    j += 1
                yield i, "{…}".join(parts)
            i += 1

    def message_in(self, lo: int, hi: int) -> tuple[int, str] | None:
        for i, text in self.strings(lo, hi):
            if is_message(text):
                return i, text.strip()
        return None

    def literal_value(self, lo: int, hi: int):
        """A number, string or regular expression standing alone in a range, else None."""
        if hi - lo == 1:
            t = self.toks[lo]
            if t.kind == "num":
                return number(t.text)
            if t.kind == "str" or (t.kind == "tmpl" and t.flags == "full"):
                return t.text
            if t.kind == "regex":
                return "/" + t.text + "/" + t.flags
        if hi - lo == 2 and self.p(lo, "-") and self.t(lo + 1).kind == "num":
            v = number(self.t(lo + 1).text)
            return -v if v is not None else None
        return self.numeric(lo, hi)

    def numeric(self, lo: int, hi: int) -> float | int | None:
        """A constant number written as arithmetic over numbers and named constants."""
        parts = []
        for i in range(lo, hi):
            t = self.toks[i]
            if t.kind == "num":
                v = number(t.text)
                if v is None:
                    return None
                parts.append(repr(v))
            elif t.kind == "name" and t.text in self.consts:
                parts.append(repr(self.consts[t.text]))
            elif t.kind == "punct" and t.text in "+-*/()":
                parts.append(t.text)
            else:
                return None
        value = arithmetic(" ".join(parts)) if parts else None
        if value is None:
            return None
        return int(value) if float(value).is_integer() else value

    def literal(self, i: int):
        """A literal at i (object, array, string, number, boolean, regex): (value, next index).
        Anything else is skipped as an expression and given as `...`."""
        t = self.t(i)
        if self.p(i, "{"):
            end = self.close.get(i, i)
            obj: dict = {}
            j = i + 1
            while j < end:
                k = self.t(j)
                if k.kind in ("name", "str", "num") and self.p(j + 1, ":"):
                    value, j = self.literal(j + 2)
                    obj[k.text] = value
                else:
                    j = self.expression_end(j)
                if self.p(j, ","):
                    j += 1
                elif j < end:
                    j = self.expression_end(j + 1)
                    j += 1 if self.p(j, ",") else 0
            return obj, end + 1
        if self.p(i, "["):
            end = self.close.get(i, i)
            items, j = [], i + 1
            while j < end:
                value, j = self.literal(j)
                items.append(value)
                if self.p(j, ","):
                    j += 1
                elif j < end:
                    j = self.expression_end(j) + 1
            return items, end + 1
        stop = self.expression_end(i)
        if t.kind in ("str", "num", "regex") or (t.kind == "tmpl" and t.flags == "full"):
            if stop == i + 1:
                return self.literal_value(i, i + 1), i + 1
        if self.w(i, "true", "false") and stop == i + 1:
            return t.text == "true", i + 1
        if self.p(i, "!") and self.t(i + 1).kind == "num" and stop == i + 2:
            return self.t(i + 1).text == "0", i + 2
        value = self.numeric(i, stop)
        if value is not None:
            return value, stop
        return Ellipsis, stop

    # --- elements -------------------------------------------------------------------------------

    def element_at(self, i: int) -> tuple[str, int] | None:
        """An element looked up at i (`document.getElementById("x")`, `$("#x")`,
        `form.elements["x"]`, ...): (its id or name, the index after it)."""
        j, hops = i, 0
        while (self.w(j) and self.p(j + 1, ".") and self.w(j + 2) and hops < 4
               and self.t(j).text not in ELEMENT_CALLS):  # fmt: skip
            j += 2
            hops += 1
            if self.t(j).text in ELEMENT_CALLS or self.t(j).text == "elements":
                break
        t = self.t(j)
        if t.kind != "name":
            return None
        if t.text == "elements":
            if self.p(j + 1, "[") and self.t(j + 2).kind == "str" and self.p(j + 3, "]"):
                return self.t(j + 2).text, j + 4
            if self.p(j + 1, ".") and self.w(j + 2):
                return self.t(j + 2).text, j + 3
            return None
        if t.text not in ELEMENT_CALLS or not self.p(j + 1, "("):
            return None
        if not (self.t(j + 2).kind in ("str", "tmpl") and self.p(j + 3, ")")):
            return None
        raw = self.t(j + 2).text
        key = raw if t.text in ("getElementById", "namedItem", "getElementsByName") else (
            selector_key(raw))  # fmt: skip
        if not key:
            return None
        end = j + 4
        if self.p(end, "[") and self.t(end + 1).kind == "num" and self.p(end + 2, "]"):
            end += 3
        return key, end

    def _bindings(self) -> None:
        """Names bound to an element (`const pw = document.getElementById("pw")`) and to a
        constant number (`const WARN = 14 * 60 * 1000`)."""
        for i in range(self.n - 2):
            if not (self.w(i) and self.p(i + 1, "=")):
                continue
            name = self.toks[i].text
            found = self.element_at(i + 2)
            if found and self.expression_end(i + 2) == found[1]:
                self.vars.setdefault(name, []).append((i, found[0]))
                continue
            stop = self.expression_end(i + 2)
            value = self.numeric(i + 2, stop)
            if value is not None and stop - (i + 2) >= 1:
                self.consts[name] = value

    def resolve(self, name: str, at: int) -> str | None:
        seen = self.vars.get(name)
        if not seen:
            return None
        before = [key for pos, key in seen if pos < at]
        return before[-1] if before else seen[0][1]

    def me_at(self, i: int) -> str | None:
        """The element a handler around token i was added to (what `this` is there)."""
        best = None
        for lo, hi, key in self.handlers:
            if lo <= i <= hi and (best is None or lo >= best[0]):
                best = (lo, hi, key)
        return best[2] if best else self.me

    def ref_at(self, i: int) -> tuple[str, int] | None:
        """An expression at i that names an element: (key, index after it). `@this` is the
        element of the handler around it."""
        found = self.element_at(i)
        if found:
            return found
        if self.w(i, "this"):
            me = self.me_at(i)
            return (me, i + 1) if me else None
        if (self.w(i) and self.p(i + 1, ".") and self.w(i + 2, "target", "currentTarget")
                and self.t(i).text in ("e", "ev", "evt", "event")):  # fmt: skip
            me = self.me_at(i)
            return (me, i + 3) if me else None
        if self.w(i) and not self.p(i - 1, ".") and not self.p(i + 1, "("):
            key = self.resolve(self.toks[i].text, i)
            if key:
                return key, i + 1
        return None

    def ref_before(self, k: int) -> tuple[str, int] | None:
        """The element named by the expression that ends just before token k: (key, start)."""
        e = k - 1
        t = self.t(e)
        if self.p(e, ")", "]") and e in self.open:
            o = self.open[e]
            start = o - 1
            while self.p(start - 1, ".") and self.w(start - 2):
                start -= 2
            found = self.element_at(start)
            if found and found[1] == k:
                return found[0], start
            if self.p(e, "]") and self.p(o - 1, ")"):
                return self.ref_before(o)
            return None
        if t.kind == "name":
            if self.p(e - 1, "."):
                if self.w(e - 2, "elements"):
                    return t.text, e - 2
                if t.text in ("target", "currentTarget") and self.w(e - 2):
                    me = self.me_at(e)
                    return (me, e - 2) if me else None
                return None
            if t.text == "this":
                me = self.me_at(e)
                return (me, e) if me else None
            key = self.resolve(t.text, e)
            if key:
                return key, e
        return None

    # --- operands and conditions ----------------------------------------------------------------

    def operand(self, i: int):
        """A value a check compares, at i: (description, index after it) or (None, i).
        Descriptions: ("read", key, prop), ("len", key), ("num", v), ("str", s), ("date", key),
        ("number", key), ("match", key, regex)."""
        t = self.t(i)
        if self.w(i, "new") and self.w(i + 1, "Date") and self.p(i + 2, "("):
            d, _ = self.operand(i + 3)
            if d and d[0] == "read":
                return ("date", d[1]), self.end_of(i + 2)
            return None, i
        if self.w(i, "Number", "parseInt", "parseFloat") and self.p(i + 1, "("):
            d, _ = self.operand(i + 2)
            if d and d[0] == "read":
                return ("number", d[1]), self.end_of(i + 1)
            return None, i
        if t.kind == "num":
            return ("num", number(t.text)), i + 1
        if t.kind == "str" or (t.kind == "tmpl" and t.flags == "full"):
            return ("str", t.text), i + 1
        ref = self.ref_at(i)
        if not ref:
            return None, i
        key, j = ref
        if self.p(j, ".") and self.t(j + 1).text in VALUE_PROPS:
            prop = self.t(j + 1).text
            j += 2
        elif (self.p(j, ".") and self.w(j + 1, "val") and self.p(j + 2, "(")
              and self.p(j + 3, ")")):  # fmt: skip
            prop = "value"
            j += 4
        else:
            return None, i
        while (self.p(j, ".") and self.w(j + 1, "trim", "toLowerCase", "toUpperCase")
               and self.p(j + 2, "(") and self.p(j + 3, ")")):  # fmt: skip
            j += 4
        if self.p(j, ".") and self.w(j + 1, "length"):
            return ("len", key), j + 2
        if (self.p(j, ".") and self.w(j + 1, "match") and self.p(j + 2, "(")
                and self.t(j + 3).kind == "regex"):  # fmt: skip
            r = self.t(j + 3)
            return ("match", key, "/" + r.text + "/" + r.flags), self.end_of(j + 2)
        return ("read", key, prop), j

    def checks(self, lo: int, hi: int) -> list[dict]:
        """The rules a failure condition states (`!x.value`, `x.value.length < 3`,
        `!/re/.test(x.value)`, `a.value !== b.value`, ...): a condition that, when true, refuses."""
        out: list[dict] = []
        bare: list[str] = []
        i = lo
        while i < hi:
            neg = self.p(i - 1, "!")
            t = self.t(i)
            if (t.kind == "regex" and self.p(i + 1, ".") and self.w(i + 2, "test")
                    and self.p(i + 3, "(")):  # fmt: skip
                d, _ = self.operand(i + 4)
                if d and d[0] == "read":
                    out.append({"rule": "pattern" if neg else "pattern (must not match)",
                                "field": d[1], "value": "/" + t.text + "/" + t.flags})  # fmt: skip
                i = self.end_of(i + 3)
                continue
            d, e = self.operand(i)
            if d is None:
                i += 1
                continue
            if self.p(e, *COMPARE):
                op = self.t(e).text
                d2, e2 = self.operand(e + 1)
                if d2 is not None:
                    rule = _compare(d, op, d2)
                    if rule:
                        out.append(rule)
                    i = e2
                    continue
            if d[0] == "match":
                out.append({"rule": "pattern" if neg else "pattern (must not match)",
                            "field": d[1], "value": d[2]})  # fmt: skip
            elif neg and d[0] in ("read", "len"):
                kind = "must be ticked" if d[0] == "read" and d[2] == "checked" else "required"
                out.append({"rule": kind, "field": d[1]})
                bare.append(d[1])
            i = e
        # `!a.value && !b.value`: one of the two is required, not each
        between = {self.t(k).text for k in range(lo, hi) if self.t(k).kind == "punct"}
        keys = list(dict.fromkeys(bare))
        if len(keys) >= 2 and "&&" in between and "||" not in between:
            out = [r for r in out if not (r["rule"] == "required" and r["field"] in keys)]
            out.append({"rule": "one of", "fields": keys})
        return out

    # --- statements -----------------------------------------------------------------------------

    def body_after(self, c: int) -> tuple[int, int]:
        """The statement after token c (a closing parenthesis of a condition)."""
        if self.p(c + 1, "{"):
            return c + 2, self.close.get(c + 1, c + 1)
        return c + 1, min(self.expression_end(c + 1, (";",)), c + 80)

    def error_context(self, lo: int, hi: int) -> bool:
        for k in range(lo, hi):
            t = self.toks[k]
            if t.kind == "name" and ERROR_CONTEXT.search(t.text):
                return True
            if t.kind == "str" and ERROR_CONTEXT.search(t.text) and len(t.text) < 40:
                return True
            if self.w(k, "return") and self.w(k + 1, "false"):
                return True
        return False

    def condition_start(self, q: int) -> int:
        """Where the condition of a `?` at q starts: after `=`, `(`, `,`, `return`, ... at its
        level."""
        j = q - 1
        while j >= 0:
            t = self.toks[j]
            if t.kind == "punct" and t.text in ")]}" and j in self.open:
                j = self.open[j] - 1
                continue
            if t.kind == "punct" and t.text in ("=", "(", ",", ";", "{", "[", ":", "=>", "?",
                                                "&&=", "||=", "??="):  # fmt: skip
                return j + 1
            if self.w(j, "return"):
                return j + 1
            j -= 1
        return 0


def _compare(a, op: str, b) -> dict | None:
    """The rule a failing comparison `a op b` states."""
    swap = {"<": ">", ">": "<", "<=": ">=", ">=": "<="}
    if a[0] in ("num", "str") and b[0] not in ("num", "str"):
        a, b, op = b, a, swap.get(op, op)
    if a[0] == "len" and b[0] == "num" and isinstance(b[1], (int, float)):
        n = b[1]
        rule = {"<": ("minlength", n), "<=": ("minlength", n + 1), ">": ("maxlength", n),
                ">=": ("maxlength", n - 1)}.get(op)  # fmt: skip
        if rule:
            return {"rule": rule[0], "value": rule[1], "field": a[1]}
        if op in ("==", "===") and n == 0:
            return {"rule": "required", "field": a[1]}
        return None
    if a[0] == "read" and b[0] == "str" and b[1] == "" and op in ("==", "==="):
        return {"rule": "required", "field": a[1]}
    if a[0] == "number" and b[0] == "num" and isinstance(b[1], (int, float)):
        n = b[1]
        rule = {"<": ("min", n), "<=": ("min", n + 1), ">": ("max", n), ">=": ("max", n - 1)}.get(
            op
        )
        return {"rule": rule[0], "value": rule[1], "field": a[1]} if rule else None
    if a[0] in ("read", "date", "number") and b[0] in ("read", "date", "number") and a[1] != b[1]:
        if op in ("!=", "!=="):
            return {"rule": "equals", "fields": [a[1], b[1]]}
        if op in ("==", "==="):
            return {"rule": "differs from", "fields": [a[1], b[1]]}
        if op == "<":
            return {"rule": "not before", "fields": [a[1], b[1]]}
        if op == "<=":
            return {"rule": "after", "fields": [a[1], b[1]]}
        if op == ">":
            return {"rule": "not before", "fields": [b[1], a[1]]}
        if op == ">=":
            return {"rule": "after", "fields": [b[1], a[1]]}
    return None


# =================================================================================================
# The shapes found in a script
# =================================================================================================


def _finding(code: Code, at: int, shape: str, **kw) -> dict:
    out = {"shape": shape, "script": code.name, "at": at}
    out.update({k: v for k, v in kw.items() if v is not None})
    return out


def _ng_key(code: Code, i: int) -> str | None:
    """The form-control key an Angular validator at i belongs to (`title: new FormControl("",
    {validators: [V.required]})`, `title: ["", [V.required]]`, `addControl("title", ...)`)."""
    p, levels = code.parent[i] if i < code.n else -1, 0
    while p >= 0 and levels < 6:
        op = code.t(p).text
        if op == "[":
            if code.p(p - 1, ":") and code.w(p - 2, "validators", "asyncValidators"):
                pass
            elif code.p(p - 1, ":") and code.t(p - 2).kind in ("name", "str"):
                return code.t(p - 2).text  # FormBuilder: key: [initial, [validators]]
        elif op == "(":
            if code.w(p - 1) and code.w(p - 2, "new"):
                if code.p(p - 3, ":") and code.t(p - 4).kind in ("name", "str"):
                    return code.t(p - 4).text
                if code.p(p - 3, ",") and code.t(p - 4).kind == "str" and code.p(p - 5, "("):
                    return code.t(p - 4).text  # addControl("key", new FormControl(...))
                return None
            if code.w(p - 1, "control") and code.p(p - 2, "."):  # key: this.fb.control(...)
                j = p - 1
                while code.p(j - 1, ".") and code.w(j - 2):
                    j -= 2
                if code.p(j - 1, ":") and code.t(j - 2).kind in ("name", "str"):
                    return code.t(j - 2).text
                return None
            if not code.w(p - 1, "compose", "composeAsync"):
                return None
        p = code.parent[p]
        levels += 1
    return None


def angular_rules(code: Code) -> list[dict]:
    """Angular `Validators.*` on reactive form controls; the class name is renamed by minifiers,
    its members are not."""
    out = []
    for i in range(1, code.n - 1):
        if not (code.p(i, ".") and code.w(i + 1) and code.t(i + 1).text in NG_VALIDATORS
                and code.w(i - 1)):  # fmt: skip
            continue
        name = code.t(i + 1).text
        if code.p(i + 2, "=", "(") and name in ("required", "requiredTrue", "email"):
            continue  # an assignment or a call: not the validator used as a function
        value = None
        if name in ("minLength", "maxLength", "min", "max", "pattern"):
            if not code.p(i + 2, "("):
                continue
            (lo, hi), *_ = code.args(i + 2) or [(i + 3, i + 3)]
            value = code.literal_value(lo, hi)
            if value is None:
                continue
        key = _ng_key(code, i - 1)
        if not key:
            continue
        rule = {"minLength": "minlength", "maxLength": "maxlength", "requiredTrue": "must be ticked"
                }.get(name, name)  # fmt: skip
        out.append(_finding(code, i, "Angular Validators", field=key, rule=rule, value=value))
    # `[disabled]="form.invalid"` compiled: ("disabled", ctx.form.invalid)
    for i in range(code.n - 3):
        if code.t(i).kind == "str" and code.t(i).text == "disabled" and code.p(i + 1, ","):
            window = [code.t(k).text for k in range(i + 2, min(i + 9, code.n))]
            if "invalid" in window or ("!" in window and "valid" in window):
                out.append(_finding(code, i, "Angular template binding", kind="submit",
                                    what="disabled while the form is invalid"))  # fmt: skip
    return out


_JQV_RULES = {"required": "required", "minlength": "minlength", "maxlength": "maxlength",
              "min": "min", "max": "max", "email": "email", "url": "url", "number": "number",
              "digits": "digits", "date": "date", "dateISO": "date", "pattern": "pattern",
              "step": "step", "creditcard": "credit card", "remote": "checked by the server",
              "accept": "file type", "extension": "file extension"}  # fmt: skip


def _jqv(code: Code, at: int, key: str, rules: dict, messages) -> list[dict]:
    out = []
    msgs = messages if isinstance(messages, dict) else {}
    one = messages if isinstance(messages, str) else None
    for name, value in (rules or {}).items():
        msg = msgs.get(name) if isinstance(msgs.get(name), str) else one
        if name == "equalTo":
            other = selector_key(value) if isinstance(value, str) else None
            if other:
                out.append(_finding(code, at, "jQuery Validate", kind="cross", rule="equals",
                                    fields=[key, other], message=msg))  # fmt: skip
            continue
        if name == "rangelength" and isinstance(value, list) and len(value) == 2:
            out.append(_finding(code, at, "jQuery Validate", field=key, rule="minlength",
                                value=value[0], message=msg))  # fmt: skip
            out.append(_finding(code, at, "jQuery Validate", field=key, rule="maxlength",
                                value=value[1], message=msg))  # fmt: skip
            continue
        if name == "range" and isinstance(value, list) and len(value) == 2:
            out.append(_finding(code, at, "jQuery Validate", field=key, rule="min",
                                value=value[0], message=msg))  # fmt: skip
            out.append(_finding(code, at, "jQuery Validate", field=key, rule="max",
                                value=value[1], message=msg))  # fmt: skip
            continue
        if name not in _JQV_RULES or value is False:
            continue
        rule = _JQV_RULES[name]
        if name == "required" and value is not True:
            rule = "required (on a condition)"
            value = selector_key(value) if isinstance(value, str) else None
        elif value is True or value is Ellipsis:
            value = None
        out.append(_finding(code, at, "jQuery Validate", field=key, rule=rule,
                            value=value if not isinstance(value, (dict, list)) else None,
                            message=msg))  # fmt: skip
    return out


def jquery_rules(code: Code) -> list[dict]:
    """jQuery Validate: `.validate({rules: {...}, messages: {...}})` and
    `$(...).rules("add", {...})`."""
    out = []
    for i in range(code.n - 2):
        if code.w(i, "validate") and code.p(i - 1, ".") and code.p(i + 1, "(") and code.p(i + 2,
                                                                                          "{"):  # fmt: skip
            options, _ = code.literal(i + 2)
            if not isinstance(options, dict):
                continue
            rules, messages = options.get("rules"), options.get("messages")
            for key, value in (rules if isinstance(rules, dict) else {}).items():
                if isinstance(value, dict):
                    out += _jqv(code, i, key, value, (messages or {}).get(key)
                                if isinstance(messages, dict) else None)  # fmt: skip
                elif isinstance(value, str):  # `name: "required"`: a class rule
                    out += _jqv(code, i, key, {value: True}, (messages or {}).get(key)
                                if isinstance(messages, dict) else None)  # fmt: skip
        if (code.w(i, "rules") and code.p(i - 1, ".") and code.p(i + 1, "(")
                and code.t(i + 2).kind == "str" and code.t(i + 2).text == "add"):  # fmt: skip
            ref = code.ref_before(i - 1)
            options, _ = code.literal(i + 4) if code.p(i + 3, ",") else (None, i)
            if ref and isinstance(options, dict):
                msgs = options.pop("messages", None)
                out += _jqv(code, i, ref[0], options, msgs)
    return out


def custom_validity(code: Code) -> list[dict]:
    """`el.setCustomValidity("message")`, also as `cond ? "message" : ""`."""
    out = []
    for i in range(1, code.n - 1):
        if not (code.w(i, "setCustomValidity") and code.p(i - 1, ".") and code.p(i + 1, "(")):
            continue
        ref = code.ref_before(i - 1)
        args = code.args(i + 1)
        if not ref or not args:
            continue
        lo, hi = args[0]
        q = next((k for k in range(lo, hi) if code.p(k, "?") and code.parent[k] == i + 1), None)
        if q is None:
            found = code.message_in(lo, hi)
            if found:
                out.append(_finding(code, found[0], "setCustomValidity", field=ref[0],
                                    rule="custom", message=found[1]))  # fmt: skip
            continue
        colon = code.expression_end(q + 1, (":",))
        yes, no = code.message_in(q + 1, colon), code.message_in(colon + 1, hi)
        found = yes or no
        if not found:
            continue
        rules = code.checks(lo, q)
        if no and not yes:  # `ok ? "" : "message"`: the condition says what is right
            rules = [{"rule": "custom", "field": ref[0]}]
        for r in rules or [{"rule": "custom", "field": ref[0]}]:
            out.append(_rule_finding(code, found[0], "setCustomValidity", r, found[1]))
    return out


def _rule_finding(code: Code, at: int, shape: str, r: dict, message: str | None) -> dict:
    if r.get("fields"):
        return _finding(code, at, shape, kind="cross", rule=r["rule"], fields=r["fields"],
                        message=message)  # fmt: skip
    return _finding(code, at, shape, field=r["field"], rule=r["rule"], value=r.get("value"),
                    message=message)  # fmt: skip


def plain_checks(code: Code) -> list[dict]:
    """`if (<failure>) { ... "message" ... }` and `x = <failure> ? "message" : ""`."""
    out = []
    for i in range(code.n - 1):
        if code.w(i, "if") and code.p(i + 1, "("):
            c = code.close.get(i + 1)
            if c is None:
                continue
            rules = code.checks(i + 2, c)
            if not rules:
                continue
            lo, hi = code.body_after(c)
            found = code.message_in(lo, hi)
            if not found and not code.error_context(lo, hi):
                continue
            for r in rules:
                out.append(_rule_finding(code, found[0] if found else i, "plain check", r,
                                         found[1] if found else None))  # fmt: skip
        elif code.p(i, "?"):
            start = code.condition_start(i)
            if code.w(start - 1, "setCustomValidity") or (
                    code.p(start - 1, "(") and code.w(start - 2, "setCustomValidity")):  # fmt: skip
                continue
            colon = code.expression_end(i + 1, (":",))
            if not code.p(colon, ":"):
                continue
            end = code.expression_end(colon + 1)
            yes = code.message_in(i + 1, colon)
            other = [code.t(k) for k in range(colon + 1, end)]
            empty = len(other) == 1 and other[0].kind in ("str", "tmpl") and not other[0].text
            if not yes or not (
                empty or (len(other) == 1 and other[0].text in ("null", "undefined"))
            ):
                continue
            for r in code.checks(start, i):
                out.append(_rule_finding(code, yes[0], "plain check", r, yes[1]))
    return out


def _handler_calls(code: Code) -> list[tuple[str, str, int, int]]:
    """(element key, event, lo, hi) of every handler added to an element."""
    out = []
    for i in range(1, code.n - 2):
        t = code.t(i)
        if t.kind != "name":
            continue
        if t.text == "addEventListener" and code.p(i - 1, ".") and code.p(i + 1, "("):
            ref = code.ref_before(i - 1)
            args = code.args(i + 1)
            if ref and len(args) >= 2 and code.t(args[0][0]).kind == "str":
                out.append((ref[0], code.t(args[0][0]).text, args[1][0], args[1][1]))
        elif (t.text.startswith("on") and t.text[2:] in EVENTS and code.p(i - 1, ".")
              and code.p(i + 1, "=")):  # fmt: skip
            ref = code.ref_before(i - 1)
            if ref:
                out.append((ref[0], t.text[2:], i + 2, code.expression_end(i + 2, (";",))))
        elif t.text == "on" and code.p(i - 1, ".") and code.p(i + 1, "("):
            ref = code.ref_before(i - 1)
            args = code.args(i + 1)
            if ref and len(args) >= 2 and code.t(args[0][0]).kind == "str":
                for event in code.t(args[0][0]).text.split():
                    out.append((ref[0], event.split(".")[0], args[-1][0], args[-1][1]))
        elif t.text in EVENTS and code.p(i - 1, ".") and code.p(i + 1, "(") and code.args(i + 1):
            ref = code.ref_before(i - 1)
            if ref:
                lo, hi = code.args(i + 1)[0]
                out.append((ref[0], t.text, lo, hi))
    return out


def _assigned(code: Code, k: int) -> tuple[str, str] | None:
    """For an `=` at k: (element key, property) when it assigns a property of an element."""
    if not code.p(k, "=") or not code.w(k - 1) or not code.p(k - 2, "."):
        return None
    prop = code.t(k - 1).text
    if code.w(k - 3, "style") and code.p(k - 4, "."):
        ref = code.ref_before(k - 4)
        return (ref[0], "style." + prop) if ref else None
    ref = code.ref_before(k - 2)
    return (ref[0], prop) if ref else None


def handler_behaviour(code: Code) -> list[dict]:
    """What handlers do to fields: masks, fields shown, hidden, enabled or made required,
    options loaded from an address, counters, a submit button enabled when the form is valid."""
    out = []
    calls = _handler_calls(code)
    code.handlers = [(lo, hi, key) for key, _ev, lo, hi in calls]
    for key, event, lo, hi in calls:
        urls = []
        for k in range(lo, hi):
            if code.w(k, "fetch", "getJSON", "get", "post", "ajax") and code.p(k + 1, "("):
                first = next(iter(code.strings(k + 2, code.close.get(k + 1, k + 2))), None)
                if first and first[1].startswith(("/", "http", ".")) or (
                        first and re.match(r"^[\w-]+/", first[1])):  # fmt: skip
                    urls.append(first[1])
        targets_options: set[str] = set()
        for k in range(lo, hi):
            got = _assigned(code, k)
            if got:
                target, prop = got
                rhs_end = code.expression_end(k + 1, (";", ","))
                rhs = {code.t(j).text for j in range(k + 1, rhs_end)}
                strs = [s for _, s in code.strings(k + 1, rhs_end)]
                if prop in ("hidden", "style.display", "style.visibility") and target != key:
                    out.append(_finding(code, k, "event handler", kind="toggle", source=key,
                                        target=target, what="shown or hidden", event=event))  # fmt: skip
                elif prop == "disabled" and target != key:
                    what = ("enabled when the form is valid" if "checkValidity" in rhs
                            or "valid" in rhs else "enabled or disabled")  # fmt: skip
                    kind = "submit" if what.startswith("enabled when") else "toggle"
                    out.append(_finding(code, k, "event handler", kind=kind, source=key,
                                        target=target, what=what, event=event))  # fmt: skip
                elif prop == "required" and target != key:
                    out.append(_finding(code, k, "event handler", kind="toggle", source=key,
                                        target=target, what="required or not", event=event))  # fmt: skip
                elif prop == "readOnly" and target != key:
                    out.append(_finding(code, k, "event handler", kind="toggle", source=key,
                                        target=target, what="read-only or not", event=event))  # fmt: skip
                elif prop == "value" and target == key:
                    mask = _mask_text(code, k + 1, rhs_end)
                    if mask:
                        out.append(_finding(code, k, "event handler", kind="mask", field=key,
                                            what=mask, event=event))  # fmt: skip
                elif prop in ("textContent", "innerText") and target != key and "length" in rhs:
                    out.append(_finding(code, k, "event handler", kind="counter", source=key,
                                        target=target, what="counts the characters typed",
                                        event=event))  # fmt: skip
                elif prop in ("innerHTML", "length") and urls:
                    targets_options.add(target)
                elif prop in ("textContent", "innerText") and strs and target != key:
                    pass
            if (code.w(k) and code.t(k).text in ("add", "appendChild", "append", "replaceChildren",
                                                   "options") and code.p(k - 1, ".") and urls):  # fmt: skip
                ref = code.ref_before(k - 1)
                if ref and ref[0] != key:
                    targets_options.add(ref[0])
            if (code.w(k, "toggle", "add", "remove") and code.p(k - 1, ".")
                    and code.w(k - 2, "classList") and code.p(k + 1, "(")
                    and code.t(k + 2).kind == "str" and HIDE_CLASSES.match(code.t(k + 2).text)):  # fmt: skip
                ref = code.ref_before(k - 3) if code.p(k - 3, ".") else None
                if ref and ref[0] != key:
                    out.append(_finding(code, k, "event handler", kind="toggle", source=key,
                                        target=ref[0], what="shown or hidden", event=event))  # fmt: skip
            if (code.w(k, "show", "hide", "toggle", "slideDown", "slideUp", "fadeIn", "fadeOut")
                    and code.p(k - 1, ".") and code.p(k + 1, "(")):  # fmt: skip
                ref = code.ref_before(k - 1)
                if ref and ref[0] != key:
                    out.append(_finding(code, k, "event handler", kind="toggle", source=key,
                                        target=ref[0], what="shown or hidden", event=event))  # fmt: skip
            if (code.w(k, "prop", "attr", "setAttribute", "removeAttribute") and code.p(k - 1, ".")
                    and code.p(k + 1, "(") and code.t(k + 2).kind == "str"
                    and code.t(k + 2).text in ("disabled", "hidden", "required", "readonly")):  # fmt: skip
                ref = code.ref_before(k - 1)
                if ref and ref[0] != key:
                    what = {"disabled": "enabled or disabled", "hidden": "shown or hidden",
                            "required": "required or not", "readonly": "read-only or not"
                            }[code.t(k + 2).text]  # fmt: skip
                    out.append(_finding(code, k, "event handler", kind="toggle", source=key,
                                        target=ref[0], what=what, event=event))  # fmt: skip
        for target in sorted(targets_options):
            out.append(_finding(code, lo, "event handler", kind="dependent", source=key,
                                target=target, url=urls[0], event=event))  # fmt: skip
    return out  # a call that fills no field is the API catalogue's, not a rule


def _mask_text(code: Code, lo: int, hi: int) -> str | None:
    """What an assignment to a field's own value does to what was typed."""
    names = [code.t(k).text for k in range(lo, hi) if code.t(k).kind == "name"]
    regexes = ["/" + code.t(k).text + "/" + code.t(k).flags for k in range(lo, hi)
               if code.t(k).kind == "regex"]  # fmt: skip
    parts = []
    if "toUpperCase" in names:
        parts.append("upper-cases what is typed")
    if "toLowerCase" in names:
        parts.append("lower-cases what is typed")
    if "replace" in names or "replaceAll" in names:
        parts.append("replaces " + ", ".join(f"`{r}`" for r in regexes) if regexes else
                     "replaces characters")  # fmt: skip
    if "slice" in names or "substring" in names or "substr" in names:
        parts.append("cuts what is typed to a length")
    if "padStart" in names or "padEnd" in names:
        parts.append("pads what is typed")
    if "toFixed" in names or "toLocaleString" in names or "format" in names:
        parts.append("formats a number")
    return "; ".join(parts) or None


def mask_plugins(code: Code) -> list[dict]:
    """Mask libraries called by the application: `$("#x").mask("99/99/9999")`,
    `Inputmask("...").mask(el)`, `IMask(el, {mask: "..."})`."""
    out = []
    for i in range(1, code.n - 2):
        if code.w(i, "mask") and code.p(i - 1, ".") and code.p(i + 1, "("):
            first = code.t(i + 2)
            ref = code.ref_before(i - 1)
            if ref and first.kind == "str":
                out.append(_finding(code, i, "mask plugin", kind="mask", field=ref[0],
                                    what=f"mask `{first.text}`"))  # fmt: skip
            elif (code.p(i - 2, ")") and code.w(code.open.get(i - 2, 0) - 1, "Inputmask")
                    and code.args(i + 1)):  # fmt: skip
                o = code.open[i - 2]
                pattern = code.t(o + 1)
                lo, _ = code.args(i + 1)[0]
                found = code.ref_at(lo)
                if found and pattern.kind == "str":
                    out.append(_finding(code, i, "mask plugin", kind="mask", field=found[0],
                                        what=f"mask `{pattern.text}`"))  # fmt: skip
        if code.w(i, "IMask") and code.p(i + 1, "("):
            args = code.args(i + 1)
            if len(args) >= 2:
                found = code.ref_at(args[0][0])
                options, _ = code.literal(args[1][0])
                if found and isinstance(options, dict) and isinstance(options.get("mask"), str):
                    out.append(_finding(code, i, "mask plugin", kind="mask", field=found[0],
                                        what=f"mask `{options['mask']}`"))  # fmt: skip
    return out


def timers(code: Code) -> list[dict]:
    """`setTimeout` / `setInterval` whose callback speaks of the session, signing out, idling or
    warning: the delay, the messages and where it sends the browser."""
    out = []
    for i in range(code.n - 1):
        if not (code.w(i, "setTimeout", "setInterval") and code.p(i + 1, "(")):
            continue
        args = code.args(i + 1)
        if len(args) < 2:
            continue
        delay = code.numeric(*args[-1])
        lo, hi = args[0]
        ranges = [(lo, hi)]
        if hi - lo == 1 and code.w(lo):  # a named function: its body
            name = code.t(lo).text
            for k in range(code.n - 2):
                if code.w(k, "function") and code.w(k + 1, name) and code.p(k + 2, "("):
                    b = code.end_of(k + 2)
                    if code.p(b, "{"):
                        ranges.append((b, code.close.get(b, b)))
                if code.w(k, name) and code.p(k + 1, "=") and k + 2 < code.n:
                    ranges.append((k + 2, code.expression_end(k + 2, (";",))))
        words = " ".join(code.t(k).text for a, b in [*ranges, args[-1]] for k in range(a, b)
                         if code.t(k).kind in ("name", "str", "tmpl"))  # fmt: skip
        window = " ".join(code.t(k).text for k in range(max(0, i - 12), i)
                          if code.t(k).kind in ("name", "str"))  # fmt: skip
        if not SESSION.search(words + " " + window):
            continue
        if not isinstance(delay, (int, float)) or delay < MIN_SESSION_DELAY:
            continue
        messages = [s.strip() for a, b in ranges for _, s in code.strings(a, b) if is_message(s)]
        goes = None
        for a, b in ranges:
            for k in range(a, b):
                if (code.w(k, "href") and code.p(k + 1, "=") and code.t(k + 2).kind == "str") or (
                        code.w(k, "assign", "replace") and code.p(k + 1, "(")
                        and code.w(k - 2, "location") and code.t(k + 2).kind == "str"):  # fmt: skip
                    goes = code.t(k + 2).text
        out.append(_finding(code, i, "timer", kind="timer", repeat=code.t(i).text == "setInterval",
                            delay_ms=delay, messages=messages[:4] or None,
                            goes_to=sanitize.route_pattern(goes) if goes else None))  # fmt: skip
    return out


def loose_messages(code: Code, used: set[int]) -> list[dict]:
    """Messages that read as validation and that no shape above took: kept with the words beside
    them that could name a field (tied later, by key or by label)."""
    out = []
    for i, text in code.strings(0, code.n):
        s = text.strip()
        if i in used or not is_message(s) or not FIELD_MESSAGE.search(s):
            continue
        if not (s[:1].isupper() and s[-1:] in ".!?"):  # a sentence to a person, not a code text
            continue
        near = [code.t(k).text for k in range(max(0, i - 30), min(code.n, i + 30))
                if code.t(k).kind == "str" and re.fullmatch(r"[A-Za-z_][\w:.-]{0,60}",
                                                            code.t(k).text)]  # fmt: skip
        out.append(_finding(code, i, "message", kind="message", message=text.strip(),
                            near=sorted(set(near))[:20]))  # fmt: skip
    return out


def analyse(src: str, name: str = "script", me: str | None = None) -> list[dict]:
    """Every rule and behaviour one script states, in the order of the shapes above."""
    code = Code(src[:MAX_SCRIPT], name, me)
    found: list[dict] = []
    found += handler_behaviour(code)  # first: it tells what `this` is inside each handler
    for part in (angular_rules, jquery_rules, custom_validity, plain_checks, mask_plugins, timers):
        try:
            found += part(code)
        except (RecursionError, IndexError, KeyError, ValueError):  # noqa: PERF203
            continue  # a shape that cannot be read in this script: the others still are
    used = {f["at"] for f in found}
    said = {_norm(f["message"]) for f in found if f.get("message")}
    found += [m for m in loose_messages(code, used) if _norm(m["message"]) not in said]
    for f in found:
        f.setdefault("kind", "rule")
    return found


# =================================================================================================
# Reading pages
# =================================================================================================

RULE_ATTRS = ("data-val", "data-rule-", "data-msg", "data-p-", "data-inputmask", "data-mask",
              "data-valmsg-for", "on")  # fmt: skip
FIELD_TAGS = {"input", "select", "textarea"}
NOT_FIELDS = {"hidden", "submit", "button", "reset", "image"}
INPUT_TYPES = {"email", "number", "date", "datetime-local", "time", "month", "week", "tel", "url",
               "range", "color", "file", "password", "search"}  # fmt: skip
# Matched against each class and the id of an element beside a field.
HELP = re.compile(r"(help|hint|form-text|description|instructions|tip)\b|[\w-]*(Help|Hint)$",
                  re.IGNORECASE)  # fmt: skip
COUNTER = re.compile(r"(count|counter|remaining|chars|characters)$", re.IGNORECASE)
ERROR_ID = re.compile(r"^(.+?)[-_.:]?(error|err|errors|msg|message|feedback|validation|invalid|"
                      r"warning)s?$", re.IGNORECASE)  # fmt: skip


class _RawBuilder(HTMLParser):
    """A raw snapshot as nodes (the shape of `behaviour.dom`): record numbers in identifier
    attributes masked, rule attributes (`data-val-*`, `on*`, ...) as written. Element text is
    never read from it: it holds data."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        from labctl.behaviour import dom as domlib

        self.domlib = domlib
        self.root = domlib.Node("#document", {})
        self.stack = [self.root]

    def _node(self, tag, attrs):
        keep = [(k, v) for k, v in attrs if k.lower().startswith(RULE_ATTRS)]
        rest = sanitize.mask_attributes([(k, v) for k, v in attrs
                                         if not k.lower().startswith(RULE_ATTRS)])  # fmt: skip
        node = self.domlib.Node(tag, {k: (v if v is not None else "") for k, v in [*rest, *keep]},
                                self.stack[-1])  # fmt: skip
        self.stack[-1].children.append(node)
        return node

    def handle_starttag(self, tag, attrs):
        node = self._node(tag, attrs)
        if tag not in self.domlib.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._node(tag, attrs)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return


def raw_tree(html: str):
    b = _RawBuilder()
    b.feed(html or "")
    b.close()
    return b.root


def _walk(node):
    from labctl.behaviour import dom as domlib

    return domlib._walk(node)


def _is_field(node) -> bool:
    if node.tag == "input":
        return (node.attrs.get("type") or "text").lower() not in NOT_FIELDS
    return node.tag in ("select", "textarea")


def _is_submit(node) -> bool:
    kind = (node.attrs.get("type") or "").lower()
    if node.tag == "button":
        return kind in ("", "submit")
    return node.tag == "input" and kind in ("submit", "image")


def field_keys(node) -> set[str]:
    """Every name code may use for a field: id (and a JSF id's last part), name, the form-control
    key of Angular, AngularJS, Vue or Knockout."""
    out: set[str] = set()
    for attr in ("id", "name", "formcontrolname", "ng-model", "data-ng-model", "v-model",
                 "x-model", "wire:model", "asp-for"):  # fmt: skip
        v = node.attrs.get(attr)
        if v:
            out.add(v)
            out.add(re.split(r"[:.$]", v)[-1])
    bind = node.attrs.get("data-bind") or ""
    m = re.search(r"\b(?:value|textInput|checked)\s*:\s*([\w.$]+)", bind)
    if m:
        out.add(m.group(1).split(".")[-1])
    return {k for k in out if k and not re.search(r"9{3}", k)}


def _selector(node) -> str:
    from labctl import navmap

    return navmap._frame_prefix(node) + navmap.canon(node)


@dataclass
class Snap:
    seq: int
    when: str
    screen: str
    raw: object
    safe: object
    messages: frozenset = frozenset()  # (selector, text) shown, as the sanitiser reads them


@dataclass
class Rec:
    """One recording part, read for the rules."""

    name: str
    ctx: object
    snaps: list[Snap] = field(default_factory=list)


def _rec(ctx) -> Rec:
    from labctl import stepfacts
    from labctl.behaviour import dom as domlib

    out = Rec(ctx.name, ctx)
    part = ctx.part
    previous = None
    met_by: dict[int, str | None] = {}
    for s in part.raw:
        # A step's address is the page after it; the page as the step met it is the previous one
        # (for a submit a click caused, the one the click met).
        screen = stepfacts._pattern(s.get("url"))
        met, previous = previous or screen, screen or previous
        if s.get("caused_by") in met_by:
            met = met_by[s["caused_by"]] or met
        met_by[s["seq"]] = met
        if not screen:
            continue
        for when, store in (("before", part.befores), ("after", part.doms)):
            html = store.get(s["seq"])
            if html is None or (when == "before" and s.get("before_scope") == "frame"):
                continue
            if when == "before" and s.get("action") == "goto":
                continue  # the page being left, or nothing yet
            if when == "after" and s.get("action") != "goto" and screen != met:
                continue  # caught between two pages; the page load that follows shows the new one
            here = met if when == "before" else screen
            info = sanitize.page(html, ctx.known, ctx.vocab)
            shown = frozenset((m["selector"], m["text"]) for m in info.messages if not m["hidden"])
            out.snaps.append(Snap(s["seq"], when, here, raw_tree(html),
                                  domlib.parse_html(info.html), shown))  # fmt: skip
    return out


# =================================================================================================
# The document: screens, fields, rules
# =================================================================================================


class Doc:
    """What `rules.json` will hold, built up as the evidence is read."""

    def __init__(self, known: set[str]):
        self.known = known
        self.screens: dict[str, dict] = {}
        self.loose: list[dict] = []
        self.timers: list[tuple[dict, str]] = []

    def msg(self, text: str | None) -> str | None:
        if not text:
            return None
        return sanitize.mask_message(re.sub(r"\s+", " ", text), self.known) or None

    def screen(self, path: str) -> dict:
        return self.screens.setdefault(path, {"path": path, "fields": {}, "cross": [],
                                              "behaviour": [], "on_load": [], "messages": []})  # fmt: skip

    def add_rule(self, fld: dict, rule: str, level: str, evidence: str, value=None,
                 message: str | None = None) -> dict:  # fmt: skip
        """Add a rule to a field or merge it with the same rule: the strongest level and every
        piece of evidence kept, a message added where there was none."""
        message = self.msg(message)
        for r in fld["rules"]:
            same_value = value is None or r.get("value") in (None, value)
            if r["rule"] == rule and same_value:
                if value is not None:
                    r["value"] = value
                if message and not r.get("message"):
                    r["message"] = message
                if _RANK[level] < _RANK[r["level"]]:
                    r["level"] = level
                if evidence not in r["evidence"]:
                    r["evidence"].append(evidence)
                return r
        r = {"rule": rule}
        if value is not None:
            r["value"] = value
        if message:
            r["message"] = message
        r.update({"level": level, "evidence": [evidence]})
        fld["rules"].append(r)
        return r


def _new_field(node, safe_node, labels: dict[str, str]) -> dict:
    from labctl import navmap

    a = node.attrs
    kind = node.tag if node.tag != "input" else f"input ({(a.get('type') or 'text').lower()})"
    fld = {"selector": _selector(node), "id": a.get("id"), "name": a.get("name"),
           "key": a.get("formcontrolname") or a.get("ng-model") or a.get("v-model"), "type": kind,
           "label": navmap._label(safe_node, labels) if safe_node is not None else ""}  # fmt: skip
    fld = {k: v for k, v in fld.items() if v}
    fld["rules"] = []
    fld["_keys"] = field_keys(node)
    fld["_describedby"] = " ".join(
        [a.get("aria-describedby") or "", a.get("aria-errormessage") or ""]
    )
    return fld


def _labels(tree) -> dict[str, str]:
    from labctl.behaviour import dom as domlib

    return {n.attrs["for"]: domlib._text(n)[:80] for n in domlib._walk(tree)
            if n.tag == "label" and n.attrs.get("for")}  # fmt: skip


def _pair(raw, safe) -> dict[int, object]:
    """Raw node -> the same element in the sanitised tree (by selector and order)."""
    safe_by: dict[str, list] = {}
    for n in _walk(safe):
        safe_by.setdefault(_selector(n), []).append(n)
    out, count = {}, {}
    for n in _walk(raw):
        sel = _selector(n)
        k = count.get(sel, 0)
        count[sel] = k + 1
        if k < len(safe_by.get(sel, [])):
            out[id(n)] = safe_by[sel][k]
    return out


def _text(node) -> str:
    from labctl.behaviour import dom as domlib

    return domlib._text(node) if node is not None else ""


_DATA_VAL = {"required": "required", "email": "email", "url": "url", "phone": "phone number",
             "number": "number", "digits": "digits", "date": "date", "creditcard": "credit card",
             "remote": "checked by the server"}  # fmt: skip


def _declared(doc: Doc, scr: dict, fld: dict, node, safe, by_id: dict, where: str) -> None:
    """The rules, hints and state the HTML of one field declares."""
    a = node.attrs
    ev = f"HTML of {where}"
    kind = (a.get("type") or "text").lower() if node.tag == "input" else node.tag
    if "required" in a or a.get("aria-required") == "true":
        doc.add_rule(fld, "required", DECLARED, ev + ": `required`")
    for attr in ("minlength", "maxlength"):
        v = number(a.get(attr) or "")
        if isinstance(v, int) and v >= 0:
            doc.add_rule(fld, attr, DECLARED, ev + f": `{attr}`", value=v)
    for attr in ("min", "max", "step"):
        raw = a.get(attr)
        if raw:
            v = number(raw)
            if v is None:  # a date or a time: its shape only (it may be today's, or data)
                v = sanitize.mask(raw)
            doc.add_rule(fld, attr, DECLARED, ev + f": `{attr}`", value=v)
    if a.get("pattern"):
        doc.add_rule(fld, "pattern", DECLARED, ev + ": `pattern`", value=a["pattern"])
    if kind in INPUT_TYPES:
        doc.add_rule(fld, "type", DECLARED, ev + ": `type`", value=kind)
    if "accept" in a and a["accept"]:
        doc.add_rule(fld, "file type", DECLARED, ev + ": `accept`", value=a["accept"])
    for attr in ("autocomplete", "inputmode", "multiple", "size", "rows", "cols", "spellcheck"):
        if attr in a:
            fld.setdefault("attributes", {})[attr] = a[attr] or True
    if safe is not None:
        for attr in ("placeholder", "title", "aria-label"):
            if safe.attrs.get(attr):
                fld[{"aria-label": "aria_label"}.get(attr, attr)] = safe.attrs[attr]
    # ASP.NET unobtrusive validation
    if a.get("data-val") == "true":
        for name, message in a.items():
            m = re.fullmatch(r"data-val-([a-z]+)", name)
            if not m:
                continue
            rule = m.group(1)
            param = {k[len(name) + 1 :]: v for k, v in a.items() if k.startswith(name + "-")}
            evr = ev + f": `{name}`"
            if rule in ("length", "maxlength", "minlength"):
                for p, r in (("min", "minlength"), ("max", "maxlength")):
                    if number(param.get(p) or "") is not None:
                        doc.add_rule(fld, r, DECLARED, evr, value=number(param[p]), message=message)
            elif rule == "range":
                for p in ("min", "max"):
                    if param.get(p):
                        doc.add_rule(fld, p, DECLARED, evr, value=number(param[p]) or param[p],
                                     message=message)  # fmt: skip
            elif rule == "regex" and param.get("pattern"):
                doc.add_rule(fld, "pattern", DECLARED, evr, value=param["pattern"], message=message)
            elif rule == "equalto" and param.get("other"):
                other = param["other"].split(".")[-1]
                _cross(doc, scr, "equals", [fld["selector"], other], DECLARED, evr, message)
            elif rule in _DATA_VAL:
                doc.add_rule(fld, _DATA_VAL[rule], DECLARED, evr, message=message)
            else:
                doc.add_rule(fld, rule, DECLARED, evr, message=message)
    # jQuery Validate as attributes
    for name, value in a.items():
        m = re.fullmatch(r"data-rule-([a-z]+)", name)
        if m and value != "false":
            rule = _JQV_RULES.get(m.group(1), m.group(1))
            v = number(value) if number(value) is not None else (None if value in ("true", "")
                                                                  else value)  # fmt: skip
            doc.add_rule(fld, rule, DECLARED, ev + f": `{name}`", value=v,
                         message=a.get("data-msg-" + m.group(1)) or a.get("data-msg"))  # fmt: skip
    # PrimeFaces client-side validation
    if a.get("data-p-required") == "true":
        doc.add_rule(fld, "required", DECLARED, ev + ": `data-p-required`",
                     message=a.get("data-p-rmsg"))  # fmt: skip
    for attr, rule in (("data-p-minlength", "minlength"), ("data-p-maxlength", "maxlength"),
                       ("data-p-minvalue", "min"), ("data-p-maxvalue", "max")):  # fmt: skip
        if number(a.get(attr) or "") is not None:
            doc.add_rule(fld, rule, DECLARED, ev + f": `{attr}`", value=number(a[attr]),
                         message=a.get("data-p-vmsg"))  # fmt: skip
    if a.get("data-p-con"):
        doc.add_rule(fld, "converter", DECLARED, ev + ": `data-p-con`",
                     value=a["data-p-con"].split(".")[-1], message=a.get("data-p-cmsg"))  # fmt: skip
    for attr in ("data-inputmask", "data-mask"):
        if a.get(attr):
            _behave(doc, scr, {"kind": "mask", "field": fld["selector"],
                               "what": f"mask `{a[attr]}`"}, DECLARED, ev + f": `{attr}`")  # fmt: skip
    # help text, counters and tooltips, from the sanitised page
    described = (a.get("aria-describedby") or "").split()
    helps = [_text(by_id[d]) for d in described if d in by_id and not _is_error_node(by_id[d])]
    if node.parent is not None and safe is not None and safe.parent is not None:
        for sib in safe.parent.children:
            if sib is safe or _is_error_node(sib) or _is_field(sib) or sib.tag in ("label",
                                                                                   "select"):  # fmt: skip
                continue
            names = (sib.attrs.get("class") or "").split() + [sib.attrs.get("id") or ""]
            if any(COUNTER.search(x) for x in names if x):
                fld.setdefault("counter", _text(sib) or True)
            elif any(HELP.match(x) for x in names if x) and _text(sib):
                helps.append(_text(sib))
    helps = [h for h in dict.fromkeys(helps) if h]
    if helps:
        fld["help"] = " ".join(helps)[:300]
    if safe is not None and (
        a.get("data-bs-toggle") == "tooltip" or a.get("data-toggle") == "tooltip"
    ):
        fld["tooltip"] = safe.attrs.get("title") or True


def _is_error_node(node) -> bool:
    ident = " ".join([node.attrs.get("id") or "", node.attrs.get("class") or ""])
    if re.search(r"error|invalid|valmsg|validation|feedback", ident, re.IGNORECASE):
        return True
    if (node.attrs.get("role") or "").lower() == "alert":
        return True
    # a list of messages whose items are errors (JSF `h:messages`: `<ul><li class="error">`)
    return node.tag in ("ul", "ol") and any(
        c.tag == "li" and re.search(r"error", c.attrs.get("class") or "", re.IGNORECASE)
        for c in node.children
    )


def _cross(doc: Doc, scr: dict, rule: str, fields: list[str], level: str, evidence: str,
           message: str | None = None) -> dict:  # fmt: skip
    message = doc.msg(message)
    for c in scr["cross"]:
        if c["rule"] == rule and c["fields"] == fields:
            if message and not c.get("message"):
                c["message"] = message
            if _RANK[level] < _RANK[c["level"]]:
                c["level"] = level
            if evidence not in c["evidence"]:
                c["evidence"].append(evidence)
            return c
    c = {"rule": rule, "fields": fields}
    if message:
        c["message"] = message
    c.update({"level": level, "evidence": [evidence]})
    scr["cross"].append(c)
    return c


def _behave(doc: Doc, scr: dict, item: dict, level: str, evidence: str) -> dict:
    keyed = {k: v for k, v in item.items() if k in ("kind", "field", "source", "target", "what")}
    for b in scr["behaviour"]:
        if {k: v for k, v in b.items() if k in keyed} == keyed:
            if _RANK[level] < _RANK[b["level"]]:
                b["level"] = level
            for k, v in item.items():
                b.setdefault(k, v)
            if evidence not in b["evidence"]:
                b["evidence"].append(evidence)
            return b
    b = dict(item)
    b.update({"level": level, "evidence": [evidence]})
    scr["behaviour"].append(b)
    return b


def _field_of(scr: dict, key: str | None) -> dict | None:
    """The field of a screen a key names (id, name, form-control key, or a selector)."""
    if not key:
        return None
    if key in scr["fields"]:
        return scr["fields"][key]
    hits = [f for f in scr["fields"].values() if key in f["_keys"]]
    if not hits and selector_key(key):
        hits = [f for f in scr["fields"].values() if selector_key(key) in f["_keys"]]
    return hits[0] if hits else None


def _ajax_targets(text: str) -> list[str]:
    """What a JSF or PrimeFaces partial update in an event attribute renders."""
    code = Code(text)
    for i in range(code.n - 1):
        if code.w(i, "ab") and code.w(i - 2, "mojarra"):
            args = code.args(i + 1)
            if len(args) >= 5 and code.t(args[4][0]).kind == "str":
                return code.t(args[4][0]).text.split()
        if code.w(i, "request") and code.w(i - 2, "ajax") and code.p(i + 1, "("):
            for lo, _hi in code.args(i + 1)[2:3]:
                options, _ = code.literal(lo)
                if isinstance(options, dict) and isinstance(options.get("render"), str):
                    return options["render"].split()
        if code.w(i, "ab") and code.w(i - 2, "PrimeFaces") and code.p(i + 1, "("):
            options, _ = code.literal(i + 2)
            if isinstance(options, dict) and isinstance(options.get("u"), str):
                return options["u"].split()
    return []


def read_pages(doc: Doc, recs: list[Rec]) -> list[tuple[str, str, str]]:
    """Fields, declared rules, hidden messages, on-load state and the code of inline handlers.
    Returns the inline handlers as (screen, field selector, code)."""
    handlers: list[tuple[str, str, str]] = []
    loaded: set[str] = set()
    for rec in recs:
        for snap in rec.snaps:
            scr = doc.screen(snap.screen)
            first = snap.screen not in loaded
            loaded.add(snap.screen)
            pair = _pair(snap.raw, snap.safe)
            labels = _labels(snap.safe)
            by_id = {n.attrs["id"]: n for n in _walk(snap.safe) if n.attrs.get("id")}
            where = f"`{snap.screen}`"
            radios: dict[str, list] = {}
            for node in _walk(snap.raw):
                safe = pair.get(id(node))
                if _is_field(node):
                    kind = (node.attrs.get("type") or "").lower()
                    if node.tag == "input" and kind == "radio" and node.attrs.get("name"):
                        radios.setdefault(node.attrs["name"], []).append((node, safe))
                        continue
                    sel = _selector(node)
                    fld = scr["fields"].get(sel)
                    if fld is None:
                        fld = scr["fields"][sel] = _new_field(node, safe, labels)
                    _declared(doc, scr, fld, node, safe, by_id, where)
                    if node.tag == "select" and safe is not None and "options" not in fld:
                        fld["options"] = [{"value": o.attrs.get("value", _text(o)),
                                           "label": _text(o)} for o in _walk(safe)
                                          if o.tag == "option"][:40]  # fmt: skip
                    if first:
                        _on_load(doc, scr, fld, node, where)
                    for attr, value in node.attrs.items():
                        if attr.startswith("on") and value:
                            targets = _ajax_targets(value)
                            if targets:
                                _behave(doc, scr, {"kind": "partial update", "source": sel,
                                                   "target": ", ".join(targets),
                                                   "what": f"`{attr[2:]}` re-renders "
                                                           + ", ".join(f"`{t}`" for t in targets)},
                                        DECLARED, f"HTML of {where}: `{attr}`")  # fmt: skip
                            else:
                                handlers.append((snap.screen, sel, value))
                elif _is_submit(node) and first and "disabled" in node.attrs:
                    _state(scr, _selector(node), "disabled on load", where)
                if node.tag in ("span", "div", "p", "small", "li") and _is_error_node(node):
                    _hidden_message(doc, scr, node, safe, where)
            for name, group in radios.items():
                node, safe = group[0]
                sel = f'input[name="{name}"]'
                fld = scr["fields"].get(sel)
                if fld is None:
                    fld = scr["fields"][sel] = _new_field(node, safe, labels)
                    fld["selector"], fld["type"] = sel, "radio buttons"
                    box = safe.parent if safe is not None else None
                    while box is not None and box.tag not in ("fieldset", "#document"):
                        box = box.parent
                    legend = next((c for c in box.children if c.tag == "legend"), None) if (
                        box is not None and box.tag == "fieldset") else None  # fmt: skip
                    if legend is not None and _text(legend):
                        fld["label"] = _text(legend)[:80]
                    fld["_keys"] |= {k for n, _ in group for k in field_keys(n)}
                    fld["options"] = [{"value": n.attrs.get("value", ""),
                                       "label": labels.get(n.attrs.get("id") or "", "")}
                                      for n, _ in group][:40]  # fmt: skip
                    fld.pop("id", None)
                _declared(doc, scr, fld, node, safe, by_id, where)
            if first:
                for node in _walk(snap.safe):
                    if node.attrs.get("title") and not _is_field(node) and node.tag in (
                            "button", "a", "span", "i", "abbr", "img", "label"):  # fmt: skip
                        _state(scr, _selector(node), f"tooltip: {node.attrs['title']}", where)
    return handlers


def _state(scr: dict, selector: str, what: str, where: str) -> None:
    for s in scr["on_load"]:
        if s["selector"] == selector and s["what"] == what:
            return
    scr["on_load"].append({"selector": selector, "what": what, "level": DECLARED,
                           "evidence": [f"HTML of {where} as first recorded"]})  # fmt: skip


def _on_load(doc: Doc, scr: dict, fld: dict, node, where: str) -> None:
    from labctl.behaviour import dom as domlib

    if "disabled" in node.attrs or not domlib._enabled(node):
        _state(scr, fld["selector"], "disabled on load", where)
    if "readonly" in node.attrs:
        _state(scr, fld["selector"], "read-only on load", where)
    if not domlib._visible(node):
        _state(scr, fld["selector"], "hidden on load", where)


def _hidden_message(doc: Doc, scr: dict, node, safe, where: str) -> None:
    """A message the page holds before it is shown (`<span class="error" hidden>...`): its text
    is the application's, tied to its field by `data-valmsg-for`, its id or `aria-describedby`."""
    from labctl.behaviour import dom as domlib

    text = _text(safe)
    if not text or domlib._visible(node) or not is_message(text):
        return
    if re.search(r"\bX+x+\b|\b9{2,}\b", text):  # the sanitiser took part of it for data
        return
    fld = _message_field(scr, node)
    if fld is None:
        return
    doc.add_rule(fld, message_kind(text), DECLARED, f"HTML of {where}: message `{_selector(node)}`, "
                 "shown by the page when the rule fails", message=text)  # fmt: skip


def _message_field(scr: dict, node) -> dict | None:
    """The field a message element belongs to, from its markup only."""
    target = node.attrs.get("data-valmsg-for")
    if target:
        return _field_of(scr, target)
    ident = node.attrs.get("id") or ""
    for fld in scr["fields"].values():
        if ident and ident in fld.get("_describedby", "").split():
            return fld
    m = ERROR_ID.match(ident)
    if m:
        hit = _field_of(scr, m.group(1))
        if hit is not None:
            return hit
    return None


# =================================================================================================
# What the recording showed: messages and changes after typing or choosing
# =================================================================================================


def _field_by_selector(scr: dict, raw_tree_, selector: str | None) -> dict | None:
    from labctl import navmap

    if not selector:
        return None
    if selector in scr["fields"]:
        return scr["fields"][selector]
    for node in _walk(raw_tree_):
        if _is_field(node) and navmap._matches(selector, node):
            sel = _selector(node)
            if sel in scr["fields"]:
                return scr["fields"][sel]
            name = node.attrs.get("name")
            if name and f'input[name="{name}"]' in scr["fields"]:
                return scr["fields"][f'input[name="{name}"]']
    return None


def _label_field(scr: dict, text: str) -> dict | None:
    """The one field whose label (or key) a message names as a word."""
    low = (text or "").lower()
    hits = []
    for fld in scr["fields"].values():
        names = {fld.get("label") or ""} | {k for k in fld["_keys"] if len(k) > 2}
        best = 0
        for name in names:
            name = re.sub(r"[*:]+$", "", name.strip()).lower()
            spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).lower()
            for cand in {name, spaced}:
                if len(cand) > 2 and re.search(r"(?<![\w-])" + re.escape(cand) + r"(?![\w-])", low):
                    best = max(best, len(cand))
        if best:
            hits.append((best, fld))
    if len(hits) != 1:  # none, or a message naming two fields ("user name or password"): the form
        return None
    return hits[0][1]


def _seen_message_field(scr: dict, raw_after, selector: str, text: str,
                        by_label: bool = True) -> dict | None:  # fmt: skip
    from labctl import navmap

    node = next((n for n in _walk(raw_after) if navmap._matches(selector, n)), None)
    if node is not None:
        hit = _message_field(scr, node)
        if hit is not None:
            return hit
        # the nearest container that holds exactly one field
        cur, hops = node.parent, 0
        while cur is not None and cur.tag != "#document" and hops < 3:
            inside = [n for n in _walk(cur) if _is_field(n)]
            if len(inside) == 1:
                return _field_by_selector(scr, raw_after, _selector(inside[0]))
            if len(inside) > 1:
                break
            cur, hops = cur.parent, hops + 1
    return _label_field(scr, text) if by_label else None


def _visible_state(tree) -> dict[str, tuple[bool, bool, tuple]]:
    """selector -> (visible, enabled, option values) for fields, submit buttons and containers
    with an id."""
    from labctl.behaviour import dom as domlib

    out = {}
    for n in _walk(tree):
        if _is_error_node(n):  # a message shown or hidden is a rule's message, not a field
            continue
        if _is_field(n) or _is_submit(n) or (n.attrs.get("id") and n.tag in (
                "div", "fieldset", "section", "p", "span", "li", "tr", "details")):  # fmt: skip
            opts = tuple(o.attrs.get("value", "") for o in _walk(n) if o.tag == "option")
            out[_selector(n)] = (domlib._visible(n), domlib._enabled(n), opts, _is_field(n),
                                 _is_submit(n))  # fmt: skip
    return out


def read_steps(doc: Doc, recs: list[Rec], endpoints: dict[str, str]) -> None:
    """Messages that appeared, and what changed after a step that typed, chose or ticked."""
    for rec in recs:
        ctx = rec.ctx
        rows = {r.get("n"): r for r in ctx.steps()}
        by_seq: dict[int, dict[str, Snap]] = {}
        for snap in rec.snaps:
            by_seq.setdefault(snap.seq, {})[snap.when] = snap
        prev_after: Snap | None = None
        submit_was_disabled: dict[tuple[str, str], int] = {}
        for s in ctx.part.raw:
            seq = s["seq"]
            got = by_seq.get(seq, {})
            before = got.get("before") or prev_after
            after = got.get("after")
            row = rows.get(seq) or {}
            # Against the page the previous step left: a message a blur shows may already be in
            # this step's own "before" snapshot (the field lost the focus as the click began).
            base = prev_after if prev_after is not None else before
            loaded = row.get("action") in ("goto", "back", "forward", "reload")
            if loaded and row.get("caused_by") in rows:  # a postback: the action's own answer
                row, loaded = rows[row["caused_by"]], False
            if after is not None and not loaded:
                scr = doc.screen(after.screen)
                new = set(after.messages) - set(base.messages if base is not None else ())
                found = {(m.get("selector"), m.get("text")) for m in row.get("messages") or []}
                for sel, text in sorted(new | found):
                    _seen(doc, scr, after, row, {"selector": sel, "text": text})
            if base is not None and after is not None and base.screen == after.screen:
                _changes(doc, ctx, rows, row, base, after, endpoints, submit_was_disabled)
            if after is not None:
                prev_after = after


def _step_text(row: dict) -> str:
    return (f"step {row.get('n')} ({row.get('action')} `{row.get('selector') or '-'}`)"
            if row else "a step")  # fmt: skip


def _seen(doc: Doc, scr: dict, after: Snap, row: dict, m: dict) -> None:
    text = m.get("text") or ""
    if not is_message(text) and len(text.split()) < 2:
        return
    if any(f.get("help") and _norm(f["help"]) == _norm(text) for f in scr["fields"].values()):
        return  # a help text, not a message
    from labctl import navmap

    node = next((n for n in _walk(after.raw) if navmap._matches(m.get("selector") or "", n)), None)
    if node is not None and node.tag in ("td", "th", "dd", "li") and not _is_error_node(node):
        return  # a cell or an item of a list: data, not a message
    errorish = node is not None and _is_error_node(node)
    if not (errorish or FIELD_MESSAGE.search(text) or message_kind(text) != "check"):
        return  # a status text (a page count, "Saved."): the navigation map has it, not a rule
    message = doc.msg(text)
    ev = f"recording: {_step_text(row)} showed `{m.get('selector')}`"
    kind = message_kind(text)
    for c in scr["cross"]:  # a rule between fields that the code or the HTML wrote
        if c.get("message") and same_message(message, c["message"]):
            c["level"] = SEEN
            if ev not in c["evidence"]:
                c["evidence"].append(ev)
            return
    fld = _seen_message_field(scr, after.raw, m.get("selector") or "", text,
                              by_label=kind != "check" or bool(FIELD_MESSAGE.search(text)))  # fmt: skip
    if fld is None:
        hit = next((x for x in scr["messages"] if _norm(x["message"]) == _norm(message)), None)
        if hit is None:
            scr["messages"].append({"message": message, "selector": m.get("selector"),
                                    "rule": kind, "level": SEEN, "evidence": [ev]})  # fmt: skip
        elif ev not in hit["evidence"]:
            hit["evidence"].append(ev)
        return
    same = [r for r in fld["rules"] if r.get("message") and same_message(message, r["message"])]
    if not same:  # masked by the sanitiser, but the rule of the HTML or the code writes it in full
        for r in fld["rules"]:
            if r.get("message") and same_message(text, r["message"]):
                same.append(r)
    if same:
        for r in same:
            r["level"] = SEEN
            if ev not in r["evidence"]:
                r["evidence"].append(ev)
        return
    target = next((r for r in fld["rules"] if r["rule"] == kind and not r.get("message")), None)
    if target is not None:
        target["message"] = message
        target["level"] = SEEN
        target["evidence"].append(ev)
        return
    doc.add_rule(fld, kind, SEEN, ev, message=text)


def _changes(doc, ctx, rows, row, before: Snap, after: Snap, endpoints, disabled_seen) -> None:
    scr = doc.screen(after.screen)
    action = row.get("action") or ""
    old, new = _visible_state(before.raw), _visible_state(after.raw)
    # a submit button disabled, then enabled after typing: disabled until the form is valid
    for sel, (_vis, ena, _o, _f, sub) in new.items():
        if sub and sel in old and not old[sel][1] and ena and action in ("type", "select",
                                                                          "check", "click"):  # fmt: skip
            _behave(doc, scr, {"kind": "submit", "target": sel,
                               "what": "disabled until the form is valid"},
                    SEEN, f"recording: disabled before, enabled after {_step_text(row)}")  # fmt: skip
    if action not in ("type", "select", "check", "uncheck", "click"):
        return
    source = _field_by_selector(scr, before.raw, row.get("selector"))
    if source is None:
        return
    caused = [r for r in rows.values() if r.get("caused_by") == row.get("n")]
    requests = [q for r in [row, *caused] for q in r.get("requests") or []]
    for sel, (vis, ena, opts, is_f, sub) in new.items():
        if sel == source["selector"] or sub:
            continue
        was = old.get(sel)
        target_fld = scr["fields"].get(sel)
        label = sel
        if was is None or (not was[0] and vis):
            if was is None and not vis:
                continue
            if not is_f and not any(f for f in _fields_inside(after.raw, sel)):
                continue
            _behave(doc, scr, {"kind": "toggle", "source": source["selector"], "target": label,
                               "what": "shown"}, SEEN, f"recording: after {_step_text(row)}")  # fmt: skip
        elif was[0] and not vis:
            _behave(doc, scr, {"kind": "toggle", "source": source["selector"], "target": label,
                               "what": "hidden"}, SEEN, f"recording: after {_step_text(row)}")  # fmt: skip
        if was is not None and is_f and was[1] != ena:
            _behave(doc, scr, {"kind": "toggle", "source": source["selector"], "target": label,
                               "what": "enabled" if ena else "disabled"},
                    SEEN, f"recording: after {_step_text(row)}")  # fmt: skip
        if was is not None and is_f and opts != was[2] and target_fld is not None:
            api = [q for q in requests if not q.split()[1].endswith((".js", ".css"))]
            item = {
                "kind": "dependent",
                "source": source["selector"],
                "target": label,
                "what": "options replaced",
                "step": row.get("n"),
            }
            if api:
                method, route = api[0].split()[:2]
                item["api"] = f"{method} {route}"
                if f"{method} {route}" in endpoints:
                    item["api_file"] = endpoints[f"{method} {route}"]
            _behave(doc, scr, item, SEEN, f"recording: after {_step_text(row)}"
                    + (f", which sent {api[0]}" if api else ""))  # fmt: skip


def _fields_inside(tree, selector: str) -> list:
    for n in _walk(tree):
        if _selector(n) == selector:
            return [x for x in _walk(n) if _is_field(x)]
    return []


# =================================================================================================
# The application's scripts
# =================================================================================================

LIBRARY_NAMES = [
    (r"jquery[.-]validate[.-]unobtrusive", "jQuery Validation Unobtrusive"),
    (r"jquery[.-]validat", "jQuery Validation"),
    (r"jquery[.-]?mask|jquery[.-]inputmask|inputmask", "Inputmask"),
    (r"jquery[.-]ui", "jQuery UI"), (r"sweet-?alert", "SweetAlert"),
    (r"jquery", "jQuery"),
    (r"(^|/)angular(js)?([.-][\d.]+)?(\.min)?\.js$|/angularjs/", "AngularJS"),
    (r"react(-dom)?([.-]production|[.-]development)?(\.min)?\.js$", "React"),
    (r"(^|/)vue([.-][\d.]+|\.global|\.runtime|\.esm)?[^/]*\.js$", "Vue"),
    (r"bootstrap", "Bootstrap"), (r"popper", "Popper"), (r"lodash", "Lodash"),
    (r"underscore", "Underscore"), (r"moment", "Moment"), (r"knockout", "Knockout"),
    (r"backbone", "Backbone"), (r"(^|/)(jsf|faces)(-uncompressed)?\.js", "JSF"),
    (r"javax\.faces\.resource|jakarta\.faces\.resource", "JSF"), (r"primefaces", "PrimeFaces"),
    (r"select2", "Select2"), (r"datatables", "DataTables"), (r"chart(\.umd)?(\.min)?\.js", "Chart.js"),
    (r"(^|/)d3(\.v\d)?(\.min)?\.js", "D3"), (r"zone(\.min)?\.js", "zone.js"),
    (r"(^|/)polyfills?([.-][\w]+)?\.js", "polyfills"), (r"core-js", "core-js"),
    (r"(^|/)(vendors?|chunk-vendors)([.~-][\w]+)?\.js", "vendor bundle"), (r"htmx", "htmx"),
    (r"alpine", "Alpine.js"), (r"axios", "axios"), (r"webforms|microsoftajax|webresource\.axd|"
                                                   r"scriptresource\.axd", "ASP.NET Web Forms"),
    (r"(^|/)(gtag|gtm|analytics|ga)\.js|googletagmanager|hotjar|newrelic|sentry", "analytics"),
]  # fmt: skip
_BANNER = re.compile(r"^\s*/\*[!*]?\s*(?:@license\s+)?([A-Z][\w.]*(?: [A-Z][\w.]*){0,3})\s+v?(\d+\.\d+"
                     r"(?:\.\d+)?)", re.IGNORECASE)  # fmt: skip


def library_name(path: str, text: str | None) -> str | None:
    """The library a script file is, by its name or its banner (None: the application's own).
    The file's own name is read first, then the package folder it is served from, then the rest
    of its address: `/node_modules/bootstrap-sweetalert/dist/sweetalert.min.js` is SweetAlert,
    not Bootstrap."""
    low = (path or "").lower()
    file = low.rsplit("/", 1)[-1]
    for rx, name in LIBRARY_NAMES:
        if file and re.search(rx, file):
            return name
    # A file served from a package folder is a package, not the application's own code
    # (`/node_modules/videojs-contrib-hls/dist/...`): named by its package.
    pkg = re.search(r"(?:^|/)(?:node_modules|bower_components)/((?:@[\w.-]+/)?[\w.-]+)/", low)
    if pkg:
        return pkg.group(1)
    for rx, name in LIBRARY_NAMES:
        if re.search(rx, low):
            return name
    head = (text or "")[:600]
    m = _BANNER.match(head)
    if m and not re.search(r"\bapp(lication)?\b", m.group(1), re.IGNORECASE):
        return f"{m.group(1)} {m.group(2)}"
    if re.search(r"@license|\(c\) \d{4}.*(?:MIT|Apache|BSD)", head) and len(text or "") > 20000:
        return "a library (licence banner)"
    return None


@dataclass
class Script:
    path: str  # the address path (record numbers masked) or `inline in <screen>`
    text: str
    own: bool
    library: str | None
    file: bool  # a script file (an inline script is not)


class _Inline(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.inside = False
        self.parts: list[str] = []
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            a = dict(attrs)
            kind = (a.get("type") or "text/javascript").lower()
            self.inside = "src" not in a and ("javascript" in kind or kind == "module")
            self.parts = []

    def handle_endtag(self, tag):
        if tag == "script" and self.inside:
            self.out.append("".join(self.parts))
            self.inside = False

    def handle_data(self, data):
        if self.inside:
            self.parts.append(data)


def inline_scripts(html: str) -> list[str]:
    p = _Inline()
    try:
        p.feed(html or "")
        p.close()
    except (AssertionError, ValueError):
        pass
    return [s for s in p.out if s.strip()]


def scripts_of(ctxs: list) -> tuple[list[Script], set[str]]:
    """Every script the recordings hold and the application's own hosts."""
    import base64

    from labctl import har, stepfacts

    out: list[Script] = []
    own: set[str] = set()
    seen: set[str] = set()
    for c in ctxs:
        own |= {urlsplit(str(s.get("url") or "")).netloc.lower() for s in c.part.raw
                if str(s.get("url") or "").startswith("http")}  # fmt: skip
    own.discard("")
    for c in ctxs:
        try:
            doc = har.load(c.part.path).har
        except (OSError, ValueError):
            continue
        for entry in (doc.get("log") or {}).get("entries") or []:
            req, resp = entry.get("request") or {}, entry.get("response") or {}
            url = str(req.get("url") or "")
            parts = urlsplit(url)
            content = resp.get("content") or {}
            mime = str(content.get("mimeType") or "").lower()
            text = content.get("text")
            if parts.scheme not in ("http", "https") or resp.get("status") != 200 or text is None:
                continue
            if content.get("encoding") == "base64":
                try:
                    text = base64.b64decode(text).decode("utf-8", "replace")
                except ValueError:
                    continue
            path = sanitize.mask_identifiers(parts.path or "/")
            host = parts.netloc.lower()
            if "javascript" in mime or "ecmascript" in mime or parts.path.endswith((".js", ".mjs")):
                digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)
                lib = library_name(parts.path, text)
                out.append(Script(path, text, host in own, lib, True))
            elif "html" in mime and host in own:
                screen = stepfacts._pattern(url) or "/"
                for body in inline_scripts(text):
                    digest = hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()
                    if digest not in seen:
                        seen.add(digest)
                        out.append(Script(f"inline in {screen}", body, True,
                                          library_name("", body), False))  # fmt: skip
        for html in [*c.part.doms.values()]:  # scripts a page wrote into itself
            for body in inline_scripts(html):
                digest = hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()
                if digest not in seen:
                    seen.add(digest)
                    out.append(Script("inline in a recorded page", body, True, library_name("", body),
                                      False))  # fmt: skip
    return out, own


DIALOG_CALL = re.compile(
    r"(?<![\w$.])(?:window\.)?(alert|confirm|prompt)\s*\(\s*(?P<q>[\"'`])"
    r"(?P<text>(?:\\.|(?!(?P=q)).){1,300})(?P=q)\s*[,)]"
)


def dialog_texts(src: str) -> list[tuple[str, str]]:
    """The browser dialogs a script shows with a text of its own: `alert("Product added.")` ->
    (alert, Product added.). A message built from values (`alert(data.msg)`) is not listed."""
    out: list[tuple[str, str]] = []
    for m in DIALOG_CALL.finditer(src or ""):
        text = m.group("text")
        if m.group("q") == "`" and "${" in text:
            continue
        text = re.sub(r"\\(.)", lambda x: {"n": " ", "t": " "}.get(x.group(1), x.group(1)), text)
        item = (m.group(1), " ".join(text.split()))
        if item[1] and item not in out:
            out.append(item)
    return out


def dialogs(scripts: list[Script], ctxs: list, data) -> list[dict]:
    """The browser dialogs (alert, confirm, prompt) of the application: those the recording
    showed (`seen`, with the message the recorder kept) and those its own scripts show with a
    text of their own (`in code, not triggered`). Messages masked as any message is."""
    out: dict[tuple[str, str], dict] = {}
    for c in ctxs:
        for row in c.part.facts.steps:
            d = row.get("dialog") or {}
            if d.get("message"):
                item = out.setdefault(
                    (d["kind"], _norm(d["message"])),
                    {"kind": d["kind"], "message": d["message"], "level": SEEN, "evidence": []},
                )
                where = f"recording step {row.get('global') or row['n']} on `{row.get('screen')}`"
                if where not in item["evidence"]:
                    item["evidence"].append(where)
    for s in scripts:
        if not s.own or s.library:
            continue
        for kind, text in dialog_texts(s.text):
            text = sanitize.mask_message(text, data.names if data is not None else set())
            item = out.setdefault((kind, _norm(text)), {"kind": kind, "message": text,
                                                         "level": CODE, "evidence": []})  # fmt: skip
            ev = f"script {s.path}"
            if ev not in item["evidence"]:
                item["evidence"].append(ev)
    return sorted(out.values(), key=lambda d: (_RANK[d["level"]], d["kind"], d["message"]))[:60]


def _tie_code(doc: Doc, findings: list[dict], handlers: list[tuple[str, str, str]]) -> None:
    """Put each finding of the scripts on the screens whose fields it names."""
    for screen, sel, text in handlers:  # inline handlers: `this` is the field
        scr = doc.screens[screen]
        fld = scr["fields"].get(sel)
        me = sorted(fld["_keys"])[0] if fld and fld["_keys"] else None
        for f in analyse(text, f"`on...` attribute of `{sel}` on `{screen}`", me):
            f["_screen"] = screen
            findings.append(f)
    loose: list[dict] = []
    for f in findings:
        screens = [doc.screens[f["_screen"]]] if f.get("_screen") else list(doc.screens.values())
        placed = False
        ev = f"script `{f['script']}`: {f['shape']}"
        for scr in screens:
            placed |= _place(doc, scr, f, ev)
        if not placed and f.get("kind") not in ("timer",):
            loose.append(f)
        if f.get("kind") == "timer":
            for scr in screens if f.get("_screen") else []:
                _behave(doc, scr, _timer_item(doc, f), CODE, ev)
            if not f.get("_screen"):
                doc.timers.append((_timer_item(doc, f), ev))
    for f in loose[: MAX_LOOSE * 3]:
        item = {k: f[k] for k in ("kind", "rule", "field", "fields", "value", "source", "target",
                                  "what", "url") if f.get(k) is not None}  # fmt: skip
        if f.get("message"):
            item["message"] = doc.msg(f["message"])
        if f.get("url"):
            item["url"] = sanitize.route_pattern(f["url"].split("?")[0])
        item.update({"level": LOOSE, "evidence": [f"script `{f['script']}`: {f['shape']}"]})
        if item not in doc.loose and len(doc.loose) < MAX_LOOSE:
            doc.loose.append(item)


def _timer_item(doc: Doc, f: dict) -> dict:
    delay = f.get("delay_ms")
    what = ("repeats every" if f.get("repeat") else "after") + (
        f" {delay:g} ms ({delay / 1000:g} s)" if isinstance(delay, (int, float)) else
        " a delay the code computes")  # fmt: skip
    item = {"kind": "session timer", "what": what}
    if f.get("messages"):
        item["messages"] = [doc.msg(m) for m in f["messages"]]
    if f.get("goes_to"):
        item["goes_to"] = f["goes_to"]
    if isinstance(delay, (int, float)):
        item["delay_ms"] = delay
    return item


def _place(doc: Doc, scr: dict, f: dict, ev: str) -> bool:
    kind = f.get("kind")
    if kind == "rule":
        fld = _field_of(scr, f.get("field"))
        if fld is None:
            return False
        doc.add_rule(
            fld, f["rule"], CODE, ev, value=_value(f.get("value")), message=f.get("message")
        )
        return True
    if kind == "cross":
        flds = [_field_of(scr, k) for k in f.get("fields") or []]
        if not flds or any(x is None for x in flds):
            return False
        _cross(doc, scr, f["rule"], [x["selector"] for x in flds], CODE, ev, f.get("message"))
        return True
    if kind == "message":
        fld = None
        for key in f.get("near") or []:
            fld = _field_of(scr, key)
            if fld is not None:
                break
        fld = fld or _label_field(scr, f["message"])
        if fld is None:
            return False
        msg = doc.msg(f["message"])
        kind_ = message_kind(f["message"])
        if any(r.get("message") and _norm(r["message"]) == _norm(msg) for r in fld["rules"]):
            return True
        target = next(
            (r for r in fld["rules"] if r["rule"] == kind_ and not r.get("message")), None
        )
        if target is not None:
            target["message"] = msg
            target["evidence"].append(ev + " (its message)")
            return True
        doc.add_rule(fld, kind_, CODE, ev + " (a message; the rule is read from its words)",
                     message=f["message"])  # fmt: skip
        return True
    if kind in ("mask",):
        fld = _field_of(scr, f.get("field"))
        if fld is None:
            return False
        _behave(doc, scr, {"kind": "mask", "field": fld["selector"], "what": f["what"]}, CODE, ev)
        return True
    if kind == "submit":  # the handler is on the form; the button is what matters
        tgt = _field_of(scr, f.get("target"))
        tgt_sel = tgt["selector"] if tgt else _container(scr, f.get("target"))
        if tgt_sel is None:  # which button, the code does not say: not tied
            return False
        _behave(doc, scr, {"kind": "submit", "target": tgt_sel,
                           "what": "disabled until the form is valid"}, CODE, ev)  # fmt: skip
        return True
    if kind in ("toggle", "dependent", "counter"):
        src = _field_of(scr, f.get("source")) if f.get("source") else None
        tgt = _field_of(scr, f.get("target")) if f.get("target") else None
        tgt_sel = tgt["selector"] if tgt else _container(scr, f.get("target"))
        if f.get("source") and src is None:
            return False
        if f.get("target") and tgt_sel is None:
            return False
        if not f.get("source") and not f.get("target"):
            return False
        item = {"kind": kind, "what": f.get("what") or {"dependent": "options replaced",
                                                         "loads": "loads data"}.get(kind, kind)}  # fmt: skip
        if src:
            item["source"] = src["selector"]
        if tgt_sel:
            item["target"] = tgt_sel
        if f.get("url"):
            item["url"] = sanitize.route_pattern(f["url"].split("?")[0])
        _behave(doc, scr, item, CODE, ev + (f" on `{f['event']}`" if f.get("event") else ""))
        return True
    return False


def _container(scr: dict, key: str | None) -> str | None:
    """A non-field element named by id that a screen holds (a block shown or hidden)."""
    if not key:
        return None
    for sel in scr.get("_ids", {}).get(key, []):
        return sel
    return None


def _value(v):
    return None if v is Ellipsis else v


# =================================================================================================
# Writing
# =================================================================================================


def _endpoints(root: Path, app_id: str) -> dict[str, str]:
    path = Path(root) / "apps" / app_id / "api" / "catalogue.json"
    try:
        cat = json.loads(path.read_text("utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        cat = {}
    return {f"{e.get('method')} {e.get('path')}": e.get("file") for e in cat.get("endpoints") or []
            if e.get("file")}  # fmt: skip


def _pages(root: Path, app_id: str) -> dict[str, str]:
    path = Path(root) / "apps" / app_id / "navigation.json"
    try:
        nav = json.loads(path.read_text("utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        nav = {}
    return {s.get("path"): s.get("page") for s in nav.get("screens") or [] if s.get("page")}


def build(root: Path, app_id: str, ctxs: list | None = None) -> dict | None:
    """The rules document of an application from its recordings (None without any)."""
    from labctl import apicatalog

    ctxs = apicatalog.load(Path(root), app_id) if ctxs is None else ctxs
    if not ctxs:
        return None
    known: set[str] = set()
    for c in ctxs:
        known |= c.known
    doc = Doc(known)
    recs = [_rec(c) for c in ctxs]
    handlers = read_pages(doc, recs)
    for rec in recs:  # containers by id, for blocks a handler shows or hides
        for snap in rec.snaps:
            ids = doc.screen(snap.screen).setdefault("_ids", {})
            for n in _walk(snap.raw):
                if n.attrs.get("id") and not _is_field(n):
                    ids.setdefault(n.attrs["id"], [])
                    if _selector(n) not in ids[n.attrs["id"]]:
                        ids[n.attrs["id"]].append(_selector(n))
    scripts, own = scripts_of(ctxs)
    findings: list[dict] = []
    analysed, libraries = [], []
    for s in scripts:
        if not s.own or s.library:
            libraries.append({"file": s.path.rsplit("/", 1)[-1] if s.file else s.path,
                              "name": s.library or "a script of another site",
                              "origin": "this application" if s.own else "another site"})  # fmt: skip
            continue
        found = analyse(s.text, s.path)
        findings += found
        analysed.append({"script": s.path, "kind": "file" if s.file else "inline",
                         "bytes": len(s.text.encode("utf-8", "replace")),
                         "findings": len(found)})  # fmt: skip
    _tie_code(doc, findings, handlers)
    read_steps(doc, recs, _endpoints(root, app_id))
    # a message of the code the recording showed for the whole form is that message, not a loose rule
    shown = {_norm(m["message"]): m for scr in doc.screens.values() for m in scr["messages"]}
    for item in list(doc.loose):
        hit = shown.get(_norm(item.get("message") or ""))
        if hit is not None:
            doc.loose.remove(item)
            hit["evidence"].append(item["evidence"][0] + " (the same message)")
    pages = _pages(root, app_id)
    data = _known_data(ctxs)
    screens = []
    for path, scr in sorted(doc.screens.items()):
        fields = []
        for fld in scr["fields"].values():
            fld = {k: v for k, v in fld.items() if not k.startswith("_")}
            fld["rules"] = sorted(fld["rules"], key=lambda r: (_RANK[r["level"]], r["rule"]))
            fields.append(fld)
        if not (fields or scr["behaviour"] or scr["messages"] or scr["cross"]):
            continue
        out = {"path": path}
        if pages.get(path):
            out["page"] = pages[path]
        out.update({"fields": fields, "cross_field": scr["cross"], "behaviour": scr["behaviour"],
                    "on_load": scr["on_load"], "messages": scr["messages"]})  # fmt: skip
        screens.append(out)
    timers = []
    for item, ev in doc.timers:
        hit = next((t for t in timers if {k: v for k, v in t.items() if k not in ("level",
                                                                                  "evidence")}
                    == item), None)  # fmt: skip
        if hit is None:
            timers.append({**item, "level": CODE, "evidence": [ev]})
        elif ev not in hit["evidence"]:
            hit["evidence"].append(ev)
    result = {
        "format": FORMAT,
        "app": app_id,
        "about": ABOUT,
        "levels": {
            SEEN: "the recording triggered it: its message appeared",
            DECLARED: "the page's HTML declares it",
            CODE: "the application's own script has it; the recording never set it off",
            LOOSE: "found in the application's own script; the field it belongs to could "
            "not be told",
        },  # fmt: skip
        "screens": screens,
        "dialogs": dialogs(scripts, ctxs, data),
        "session": timers,
        "not_tied": doc.loose,
        "scripts": {
            "analysed": analysed,
            "libraries": _dedupe(libraries),
            "not_recovered": NOT_RECOVERED,
        },  # fmt: skip
    }
    scrub(result, data)
    result["_scripts"] = scripts
    result["_own"] = own
    result["_data"] = data
    return result


ABOUT = (
    "Per screen and per field, every validation rule the recording showed, the page's HTML "
    "declares or the application's own JavaScript has, with its message (interface text, "
    "kept literally; a value-like word masked) and its level and evidence; rules between "
    "fields; what the page does while a person types or chooses; its state on load; "
    "session timers. Implement every rule at level `seen` or `declared` exactly, with its "
    "message letter for letter, and every rule `in code, not triggered` too: the real "
    "application has it. Selectors are those of `navigation.json` and the page snapshots."
)
NOT_RECOVERED = [
    "Rules checked only on the server and never triggered in the recording (a JSF validator, a "
    "server-side check): no HTML attribute and no script states them.",
    "Custom validator functions whose logic is a function call the parser does not follow "
    "(an Angular custom validator, a validator imported from a library): their messages are "
    "listed when they read as messages, the condition is not.",
    "Rules built from data at run time (a pattern or a limit read from an API answer): the "
    "value is not in the code.",
    "Library code (by name or banner) is not analysed; the application's use of it is.",
]


def _dedupe(items: list[dict]) -> list[dict]:
    out = []
    for i in items:
        if i not in out:
            out.append(i)
    return out


def _known_data(ctxs: list):
    from labctl import lookfeel

    try:
        return lookfeel.known_data([c.part.path for c in ctxs])
    except Exception:  # noqa: BLE001 - without it, mask_message alone has run
        return None


_TEXT_KEYS = {"message", "messages", "label", "help", "placeholder", "title", "aria_label",
              "tooltip", "counter", "what"}  # fmt: skip


def scrub(value, data, key: str = ""):
    """Every interface text of the document through the look's data check: a word the recording
    shows as data is masked where it is."""
    if data is None:
        return value
    if isinstance(value, dict):
        for k, v in list(value.items()):
            if k == "options" and isinstance(v, list):
                for o in v:
                    if isinstance(o, dict) and isinstance(o.get("label"), str):
                        o["label"] = _clean(o["label"], data)
            else:
                value[k] = scrub(v, data, k)
        return value
    if isinstance(value, list):
        return [scrub(v, data, key) for v in value]
    if isinstance(value, str) and key in _TEXT_KEYS:
        return _clean(value, data)
    return value


def _clean(text: str, data) -> str:
    for hit in data.findings(text, label=True):
        if hit == "an e-mail address":
            text = sanitize.EMAIL.sub(lambda m: m.group() if sanitize.EXAMPLE_EMAIL.search(m.group())
                                      else sanitize.mask(m.group()), text)  # fmt: skip
        else:
            text = re.sub(r"(?<![\w])" + re.escape(hit) + r"(?![\w])", sanitize.mask(hit), text)
    return text


def write(root: Path, app_id: str, ctxs: list | None = None) -> dict | None:
    """Write `apps/<app>/rules.json` (and, with the opt-in, `apps/<app>/scripts/`); returns the
    document (None, and no file, without a recording)."""
    out = Path(root) / "apps" / app_id
    doc = build(root, app_id, ctxs)
    scripts_dir = out / SCRIPTS_DIR
    if doc is None:
        (out / FILE).unlink(missing_ok=True)
        _clear_scripts(scripts_dir)
        return None
    scripts, own, data = doc.pop("_scripts"), doc.pop("_own"), doc.pop("_data")
    _clear_scripts(scripts_dir)
    if carry_scripts():
        doc["scripts"]["carried"] = _carry(scripts_dir, scripts, own, data)
    else:
        doc["scripts"]["carried"] = {"opt_in": False, "note": f"Script files are not carried. An "
                                     f"owner with the right to share them sets {SCRIPTS_ENV}=carry "
                                     "when the brief is made."}  # fmt: skip
    out.mkdir(parents=True, exist_ok=True)
    (out / FILE).write_bytes((json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    return doc


def _clear_scripts(folder: Path) -> None:
    if folder.is_dir():
        for p in folder.iterdir():
            if p.is_file():
                p.unlink()
        try:
            folder.rmdir()
        except OSError:
            pass


# --- the opt-in: the application's own script files ---------------------------------------------

SOURCE_MAP = re.compile(r"^[ \t]*(//[#@]\s*sourceMappingURL=[^\n]*|/\*[#@]\s*sourceMappingURL=[^*]*"
                        r"\*/)[ \t]*(?:\r?\n|$)", re.MULTILINE)  # fmt: skip
SECRET_SHAPES = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key block"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "a JWT"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "a GitHub token"),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), "a Slack token"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "a Google API key"),
    (re.compile(r"\b[sr]k_(live|test)_[0-9A-Za-z]{16,}\b"), "a Stripe key"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*"), "a bearer token"),
    (re.compile(r"\b(?:postgres(?:ql)?|mysql|mssql|mongodb(?:\+srv)?|redis|amqp)://[^\s'\"`:]+:"
                r"[^\s'\"`@]+@"), "a connection string with a password"),
]  # fmt: skip
NAMESPACE_HOSTS = {"www.w3.org", "w3.org"}  # XML namespaces: names, not places a page connects to
URL_HOST = re.compile(r"\b(?:https?|wss?|ftp)://([A-Za-z0-9.-]+)", re.IGNORECASE)


def _reserved(host: str) -> bool:
    host = host.lower().rstrip(".")
    return (host in ("localhost", "127.0.0.1") or host.endswith((".test", ".invalid", ".example",
            ".localhost")) or host in ("example.com", "example.net", "example.org")
            or host.endswith((".example.com", ".example.net", ".example.org")))  # fmt: skip


def script_findings(text: str, own_hosts: set[str], data=None) -> list[str]:
    """Why a script file may not be carried, by kind and place only (never the value): something
    shaped like a secret, a literal assigned to a credential-like name (`har.SECRET_KEY`), a host
    other than the application's own, a value the recording shows as data (the look's check)."""
    from labctl import har

    found: list[str] = []
    for rx, what in SECRET_SHAPES:
        for m in rx.finditer(text):
            found.append(f"{what} at character {m.start()}")
    toks, comments = scan(text)
    for i in range(len(toks) - 2):
        a, b, c = toks[i], toks[i + 1], toks[i + 2]
        if (a.kind in ("name", "str") and b.kind == "punct" and b.text in ("=", ":")
                and c.kind == "str" and har.SECRET_KEY.search(a.text)):  # fmt: skip
            v = c.text
            if (len(v) >= 8 and not re.search(r"\s", v) and re.search(r"\d", v)
                    and re.search(r"[A-Za-z]", v) and not re.fullmatch(r"[A-Z0-9_-]+", v)
                    and not v.startswith(("/", "#", "."))):  # fmt: skip
                found.append(f"a literal assigned to `{a.text}` at character {c.pos}")
    hosts = {h.split(":")[0].lower() for h in own_hosts}
    for t in [*toks, *comments]:
        if t.kind not in ("str", "tmpl", "comment", "regex"):
            continue
        for m in URL_HOST.finditer(t.text):
            host = m.group(1).lower().rstrip(".")
            if host not in hosts and host not in NAMESPACE_HOSTS and not _reserved(host):
                found.append(f"an address of another host at character {t.pos}")
                break
        if data is not None and t.kind in ("str", "tmpl", "comment"):
            if data.findings(t.text, label=True):
                found.append(f"a value the recording shows as data at character {t.pos}")
    return found


def _script_name(path: str, taken: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", path.rstrip("/").rsplit("/", 1)[-1]).strip("-.")
    base = base or "script.js"
    if not base.endswith((".js", ".mjs")):
        base += ".js"
    name, n = base, 1
    while name.lower() in taken:
        n += 1
        stem, ext = os.path.splitext(base)
        name = f"{stem}-{n}{ext}"
    taken.add(name.lower())
    return name


def _carry(folder: Path, scripts: list[Script], own: set[str], data) -> dict:
    """The opt-in: the application's own script files under `scripts/`, source maps stripped,
    after the check; one failure and none is carried."""
    chosen = [s for s in scripts if s.own and s.file and not s.library]
    problems: list[str] = []
    files: list[tuple[str, str, str]] = []
    taken: set[str] = set()
    for s in chosen:
        text = SOURCE_MAP.sub("", s.text)
        for p in script_findings(text, own, data):
            problems.append(f"`{s.path}`: {p}")
        files.append((_script_name(s.path, taken), s.path, text))
    if problems:
        return {"opt_in": True, "refused": problems[:30],
                "note": "Nothing is carried: a script failed the check. Remove what it names from "
                        "the application's scripts, or switch the opt-in off."}  # fmt: skip
    folder.mkdir(parents=True, exist_ok=True)
    listing = []
    for name, path, text in files:
        data_bytes = text.replace("\r\n", "\n").encode("utf-8")
        (folder / name).write_bytes(data_bytes)
        listing.append({"file": f"{SCRIPTS_DIR}/{name}", "from": path,
                        "sha256": hashlib.sha256(data_bytes).hexdigest(), "bytes": len(data_bytes)})  # fmt: skip
    (folder / SCRIPTS_MANIFEST).write_bytes(
        (json.dumps({"files": listing}, indent=1) + "\n").encode("utf-8"))  # fmt: skip
    return {"opt_in": True, "files": listing,
            "not_carried": "library files, scripts of other sites, inline scripts, source maps"}  # fmt: skip


def package_files(root: Path, app_id: str) -> dict[str, Path]:
    """The files of `rules.json` and the opt-in scripts for the build package."""
    app = Path(root) / "apps" / app_id
    out: dict[str, Path] = {}
    if (app / FILE).is_file():
        out[f"apps/{app_id}/{FILE}"] = app / FILE
    folder = app / SCRIPTS_DIR
    if folder.is_dir():
        for p in sorted(folder.iterdir()):
            if p.is_file() and not p.is_symlink():
                out[f"apps/{app_id}/{SCRIPTS_DIR}/{p.name}"] = p
    return out


def package_check(root: Path, app_id: str) -> list[str]:
    """Why the package may not carry the scripts (the opt-in): the check refused them when the
    brief was made, a file is not one the check passed (its sha256), a source map or library is
    there, or the check fails now. [] when there is nothing to say."""
    from labctl import stepfacts

    app = Path(root) / "apps" / app_id
    problems: list[str] = []
    try:
        doc = json.loads((app / FILE).read_text("utf-8")) if (app / FILE).is_file() else {}
    except (OSError, ValueError):
        doc = {}
    carried = (doc.get("scripts") or {}).get("carried") or {}
    if carried.get("refused"):
        problems += [f"the scripts failed the check when the brief was made: {p}"
                     for p in carried["refused"]]  # fmt: skip
    folder = app / SCRIPTS_DIR
    if not folder.is_dir():
        return problems
    try:
        listing = json.loads((folder / SCRIPTS_MANIFEST).read_text("utf-8"))["files"]
    except (OSError, ValueError, KeyError, TypeError):
        return problems + [f"{SCRIPTS_DIR}/ has no readable {SCRIPTS_MANIFEST}"]
    sums = {Path(f["file"]).name: f["sha256"] for f in listing}
    own: set[str] = set()
    for part in stepfacts.parts_of(Path(root), app_id):
        for s in stepfacts._steps(part):
            if str(s.get("url") or "").startswith("http"):
                own.add(urlsplit(s["url"]).netloc.lower())
    for p in sorted(folder.iterdir()):
        if p.name == SCRIPTS_MANIFEST:
            continue
        if p.suffix.lower() == ".map":
            problems.append(f"{SCRIPTS_DIR}/{p.name}: a source map is never carried")
            continue
        if library_name(p.name, None):
            problems.append(f"{SCRIPTS_DIR}/{p.name}: a library file is never carried")
            continue
        data = p.read_bytes()
        if sums.get(p.name) != hashlib.sha256(data).hexdigest():
            problems.append(f"{SCRIPTS_DIR}/{p.name}: not a file the check passed (sha256)")
            continue
        text = data.decode("utf-8", "replace")
        if SOURCE_MAP.search(text):
            problems.append(f"{SCRIPTS_DIR}/{p.name}: holds a source-map reference")
        for f in script_findings(text, own, None):
            if own or "another host" not in f:  # without the evidence, the sha256 vouches for it
                problems.append(f"{SCRIPTS_DIR}/{p.name}: {f}")
    return problems


# =================================================================================================
# The brief
# =================================================================================================


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def rule_text(r: dict) -> str:
    """A rule in words for the brief's table."""
    rule, v = r["rule"], r.get("value")
    words = {
        "required": "required", "minlength": f"at least {v} characters",
        "maxlength": f"at most {v} characters", "min": f"at least `{v}`", "max": f"at most `{v}`",
        "step": f"in steps of `{v}`", "pattern": f"matches `{v}`",
        "pattern (must not match)": f"does not match `{v}`", "type": f"type `{v}`",
        "email": "an e-mail address", "url": "an address (URL)", "number": "a number",
        "digits": "digits only", "must be ticked": "must be ticked",
        "converter": f"converted to {v}" if v else "converted", "custom": "a custom check",
        "check": "a check (said by its message)", "file type": f"file types `{v}`",
    }.get(rule)  # fmt: skip
    if words is None:
        words = rule if v is None else f"{rule} `{v}`"
    return words


def _field_name(fld: dict) -> str:
    label = fld.get("label")
    return f"`{fld['selector']}`" + (f" ({_cell(label)})" if label else "")


def brief_section(app_id: str, doc: dict | None) -> str:
    """The brief's section *Field rules and page behaviour*: per screen a table of the rules
    (field, rule, message, level), then rules between fields, behaviour, the state on load."""
    if not doc or not (
        doc.get("screens") or doc.get("session") or doc.get("not_tied") or doc.get("dialogs")
    ):
        return ""
    app = f"apps/{app_id}"
    out = [f"## {HEADING}\n",
           f"What each screen checks and does, from the recording (`{SEEN}`: the message "
           f"appeared), from the page's HTML (`{DECLARED}`) and from the application's own "
           f"JavaScript (`{CODE}`: the real application has the rule although the recording "
           f"never set it off; `{LOOSE}`: in the code, the field could not be told). "
           f"`{app}/{FILE}` holds every rule with its evidence. **Implement every rule at level "
           f"`{SEEN}` or `{DECLARED}` exactly, with its message letter for letter, and every "
           f"rule `{CODE}` too: the real application has it.** A message shows where the "
           "snapshot shows it; a value-like word in it is masked (`Xxxx`, `9999`).\n"]  # fmt: skip
    for scr in doc.get("screens") or []:
        rows = []
        for fld in scr["fields"]:
            for r in fld["rules"]:
                rows.append(f"| {_field_name(fld)} | {_cell(rule_text(r))} | "
                            f"{_cell(r.get('message') or '')} | {r['level']} |")  # fmt: skip
        for m in scr.get("messages") or []:
            rows.append(f"| (the form; shown in `{m.get('selector')}`) | {_cell(m['rule'])} | "
                        f"{_cell(m['message'])} | {m['level']} |")  # fmt: skip
        extra = []
        for c in scr.get("cross_field") or []:
            extra.append(f"- Between {', '.join(f'`{x}`' for x in c['fields'])}: "
                         f"{_cell(c['rule'])}" + (f', message "{c["message"]}"' if c.get("message")
                                                  else "") + f" ({c['level']}).")  # fmt: skip
        for b in scr.get("behaviour") or []:
            extra.append("- " + behaviour_text(b))
        state = [f"`{s['selector']}` {s['what']}" for s in scr.get("on_load") or []]
        hints = []
        for fld in scr["fields"]:
            bits = []
            for k, name in (("placeholder", "placeholder"), ("title", "tooltip"),
                            ("help", "help text"), ("counter", "character counter")):  # fmt: skip
                if fld.get(k) not in (None, True, ""):
                    bits.append(f'{name} "{_cell(fld[k])}"')
                elif fld.get(k) is True:
                    bits.append(name)
            if fld.get("options"):
                bits.append(f"{len(fld['options'])} option(s)")
            if bits:
                hints.append(f"`{fld['selector']}`: " + "; ".join(bits))
        if not (rows or extra or state or hints):
            continue
        out.append(f"### `{scr['path']}`\n")
        if rows:
            out += ["| Field | Rule | Message | Level |", "|---|---|---|---|", *rows, ""]
        if extra:
            out += ["**Between fields, and while typing or choosing:**", "", *extra, ""]
        if state:
            out.append("**On load:** " + "; ".join(state[:30]) + ".\n")
        if hints:
            out.append("**Hints shown with the fields:** " + "; ".join(hints[:30]) + ".\n")
    if doc.get("dialogs"):
        out.append("**Browser dialogs** (`alert`, `confirm`, `prompt`; show each with this text, "
                   "letter for letter):\n")  # fmt: skip
        for d in doc["dialogs"]:
            out.append(f"- `{d['kind']}`: \"{_cell(d['message'])}\" ({d['level']}; "
                       f"{', '.join(d['evidence'][:3])})")  # fmt: skip
        out.append("")
    for t in doc.get("session") or []:
        out.append("**Session timer** (" + t["level"] + "): " + behaviour_text(t) + "\n")
    if doc.get("not_tied"):
        out.append(f"**In the code, not tied to a field** ({LOOSE}): implement each where the "
                   "screen that shows such a field is built, and list your choice in "
                   "BUILD-NOTES.md.\n")  # fmt: skip
        for item in doc["not_tied"][:20]:
            what = item.get("rule") or item.get("what") or item.get("kind")
            on = item.get("field") or ", ".join(item.get("fields") or []) or item.get("source")
            out.append(f"- {_cell(what)}" + (f" on `{on}` (no recorded screen has it)" if on else "")
                       + (f': "{_cell(item["message"])}"' if item.get("message") else "")
                       + f" ({item['evidence'][0]}).")  # fmt: skip
        out.append("")
    scripts = doc.get("scripts") or {}
    names = [f"`{s['script']}`" for s in scripts.get("analysed") or []]
    libs = sorted({f"{x['name']} (`{x['file']}`)" for x in scripts.get("libraries") or []})
    out.append("**Scripts read:** " + (", ".join(names[:20]) if names else "none of the "
               "application's own") + ". " + ("Named, not analysed: " + ", ".join(libs[:20]) + ". "
               if libs else "") + "No script is carried" + (
               f" except, on the owner's request, `{app}/{SCRIPTS_DIR}/`" if (
                   scripts.get("carried") or {}).get("files") else "") + ".\n")  # fmt: skip
    refused = (scripts.get("carried") or {}).get("refused")
    if refused:
        out.append(f"**Scripts not carried:** {SCRIPTS_ENV}=carry was set, but the check refused "
                   "them: " + "; ".join(refused[:5]) + ".\n")  # fmt: skip
    return "\n".join(out) + "\n"


def behaviour_text(b: dict) -> str:
    kind = b.get("kind")
    src, tgt = b.get("source"), b.get("target")
    ev = b["evidence"][0] if b.get("evidence") else ""
    if kind == "dependent":
        api = f" from `{b['api']}`" if b.get("api") else (f" from `{b['url']}`" if b.get("url")
                                                           else "")  # fmt: skip
        text = f"Choosing in `{src}` replaces the options of `{tgt}`{api}"
        if b.get("api_file"):
            text += f" (`{b['api_file']}`)"
    elif kind == "toggle":
        text = f"`{src}` makes `{tgt}` {b.get('what')}"
    elif kind == "mask":
        text = f"`{b.get('field')}` {b.get('what')}"
    elif kind == "submit":
        text = f"`{tgt or 'the submit button'}` is {b.get('what')}"
    elif kind == "counter":
        text = f"`{tgt}` {b.get('what')} into `{src}`"
    elif kind == "partial update":
        text = f"`{src}`: {b.get('what')} (a partial update without a page load)"
    elif kind == "session timer":
        text = f"{b.get('what')}" + (f', shows "{"; ".join(b["messages"])}"' if b.get("messages")
                                    else "") + (f", then goes to `{b['goes_to']}`"
                                               if b.get("goes_to") else "")  # fmt: skip
    elif kind == "loads":
        text = f"`{src}` loads `{b.get('url')}`"
    else:
        text = f"{kind}: {b.get('what')}"
    return f"{text} ({b.get('level')}; {ev})."


def extend(root: Path, app_id: str, model, text: str) -> str:
    """Write `rules.json` and put the brief's section into `text` (the factory's one hook).
    A desktop application has no web page: nothing is written."""
    out = Path(root) / "apps" / app_id
    if getattr(getattr(model, "stack", None), "desktop", None):
        (out / FILE).unlink(missing_ok=True)
        _clear_scripts(out / SCRIPTS_DIR)
        return text
    doc = write(root, app_id)
    section = brief_section(app_id, doc)
    if not section:
        return text
    for marker in ("## Behaviour seen in the recordings", "## Part of a flow across applications",
                   "## Running in the lab"):  # fmt: skip
        if marker in text:
            return text.replace(marker, section + marker, 1)
    return f"{text}\n{section}"


def task_rule(app: str) -> str:
    """The rule the build task adds when the package carries `rules.json`."""
    return (
        f"**Same field rules and page behaviour.** Every rule in `{app}/{FILE}` (the brief's "
        f"*{HEADING}*) at level `{SEEN}` or `{DECLARED}` is implemented exactly, with its "
        f"message letter for letter. Rules `{CODE}` are implemented too: the real application "
        f"has them although the recording never set them off. Rules `{LOOSE}` are implemented "
        "where the brief places them, and your choice is written in BUILD-NOTES.md. The "
        "same goes for the behaviour listed there: masks, fields shown, hidden or enabled by "
        "another, drop-downs filled from the API, a submit button disabled until the form is "
        "valid, the state on load and the session timers."
    )
