"""Reading a DOM snapshot (outerHTML) into the element states the catalog and fidelity compare.

Snapshots written by the harness bot carry the live state as attributes (`value` on inputs,
`selected` on options, `checked` on boxes), set just before `outerHTML` is read. Snapshots
without them still parse; values are then unknown (None), not empty.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
    "track", "wbr",
}  # fmt: skip
SKIP_TEXT = {"script", "style", "template", "noscript"}
INTERACTIVE = {"input", "select", "textarea", "button"}
FORM_TAGS = {"input", "select", "textarea"}
TEXT_CAP = 200

# JavaScript run in the page before reading outerHTML, so values and selections are in the markup.
# Password values are never written.
SYNC_JS = """() => {
  for (const el of document.querySelectorAll('input, textarea, select')) {
    if (el.tagName === 'SELECT') {
      for (const o of el.options) {
        if (o.selected) o.setAttribute('selected', ''); else o.removeAttribute('selected');
      }
    } else if (el.type === 'checkbox' || el.type === 'radio') {
      if (el.checked) el.setAttribute('checked', ''); else el.removeAttribute('checked');
    } else if (el.type === 'password') {
      el.removeAttribute('value');
    } else {
      el.setAttribute('value', el.value);
    }
  }
  return '<!DOCTYPE html>\\n' + document.documentElement.outerHTML;
}"""


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    parent: Node | None = None
    children: list[Node] = field(default_factory=list)
    text: list[str] = field(default_factory=list)  # direct and descendant text, in order

    @property
    def id(self) -> str | None:
        return self.attrs.get("id") or None

    @property
    def classes(self) -> list[str]:
        return sorted((self.attrs.get("class") or "").split())


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", {})
        self.stack: list[Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        from labctl.sanitize import mask_attributes

        attrs = mask_attributes(attrs)  # record numbers in ids never reach behaviours.json
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if any(n.tag in SKIP_TEXT for n in self.stack):
            return
        for node in self.stack[1:]:
            node.text.append(data)


def parse_html(html: str) -> Node:
    builder = _Builder()
    builder.feed(html or "")
    builder.close()
    return builder.root


def _walk(node: Node):
    for child in node.children:
        yield child
        yield from _walk(child)


def _text(node: Node) -> str:
    return re.sub(r"\s+", " ", "".join(node.text)).strip()


def _hidden_self(node: Node) -> bool:
    if "hidden" in node.attrs:
        return True
    style = (node.attrs.get("style") or "").replace(" ", "").lower()
    if "display:none" in style or "visibility:hidden" in style:
        return True
    return node.tag == "input" and (node.attrs.get("type") or "").lower() == "hidden"


def _visible(node: Node) -> bool:
    cur: Node | None = node
    while cur is not None and cur.tag != "#document":
        if _hidden_self(cur):
            return False
        cur = cur.parent
    return True


def _enabled(node: Node) -> bool:
    cur: Node | None = node
    while cur is not None and cur.tag != "#document":
        if "disabled" in cur.attrs and cur.tag in INTERACTIVE | {"fieldset", "option", "optgroup"}:
            return False
        cur = cur.parent
    return True


@dataclass
class ElementState:
    key: str  # "#id", or "tag[name='x']"
    tag: str
    id: str | None
    classes: list[str]
    visible: bool
    enabled: bool
    interactive: bool
    value: str | None = None  # for form elements; select: the selected option's value
    selected: str | None = None  # select: the selected option's text
    options: list[str] | None = None  # select: option texts in order
    text: str | None = None  # non-form elements: their text content (capped)
    container: bool = False  # holds elements with an id or interactive elements

    def signature(self) -> str:
        return (
            self.tag + (f"#{self.id}" if self.id else "") + "".join(f".{c}" for c in self.classes)
        )


@dataclass
class Snapshot:
    elements: dict[str, ElementState]  # keyed elements
    signatures: list[str]  # every interactive element, keyed or not, as tag#id.classes


def _is_interactive(node: Node) -> bool:
    if node.tag in INTERACTIVE:
        return not (node.tag == "input" and (node.attrs.get("type") or "").lower() == "hidden")
    return node.tag == "a" and "href" in node.attrs


def _state(node: Node, key: str) -> ElementState:
    st = ElementState(
        key=key,
        tag=node.tag,
        id=node.id,
        classes=node.classes,
        visible=_visible(node),
        enabled=_enabled(node),
        interactive=_is_interactive(node),
    )
    if node.tag == "select":
        opts = [d for d in _walk(node) if d.tag == "option"]
        st.options = [_text(o) for o in opts]
        chosen = [o for o in opts if "selected" in o.attrs]
        if chosen:
            st.value = chosen[-1].attrs.get("value", _text(chosen[-1]))
            st.selected = _text(chosen[-1])
    elif node.tag == "input":
        kind = (node.attrs.get("type") or "text").lower()
        if kind in ("checkbox", "radio"):
            st.value = "checked" if "checked" in node.attrs else "unchecked"
        elif "value" in node.attrs:
            st.value = node.attrs["value"]
    elif node.tag == "textarea":
        st.value = node.attrs["value"] if "value" in node.attrs else _text(node) or None
    elif node.tag != "button":
        st.text = _text(node)[:TEXT_CAP]
    if node.tag in ("button", "a"):
        st.text = _text(node)[:TEXT_CAP]
    if node.tag not in FORM_TAGS:
        st.container = any(d.id or _is_interactive(d) for d in _walk(node))
    return st


def snapshot(html: str) -> Snapshot:
    root = parse_html(html)
    elements: dict[str, ElementState] = {}
    signatures: list[str] = []
    for node in _walk(root):
        key = None
        if node.id:
            key = f"#{node.id}"
        elif node.attrs.get("name") and node.tag in INTERACTIVE:
            key = f"{node.tag}[name='{node.attrs['name']}']"
        interactive = _is_interactive(node)
        if key and key not in elements:
            elements[key] = _state(node, key)
        if interactive:
            sig = node.tag + (f"#{node.id}" if node.id else "")
            sig += "".join(f".{c}" for c in node.classes)
            signatures.append(sig)
    return Snapshot(elements=elements, signatures=signatures)


def key_of(selector: str | None) -> str | None:
    """The element key a CSS selector names: its last #id, or tag[name=...]."""
    if not selector:
        return None
    ids = re.findall(r"#([A-Za-z_][\w-]*)", selector)
    if ids:
        return f"#{ids[-1]}"
    m = re.search(r"(\w+)\[name=['\"]?([\w-]+)['\"]?\]", selector)
    if m:
        return f"{m.group(1)}[name='{m.group(2)}']"
    return None


def shape(value: str | None) -> str | None:
    """A value's format with the data taken out: letters -> A, digits -> 9, runs collapsed."""
    if value is None:
        return None
    out = []
    for ch in value:
        c = "A" if ch.isalpha() else "9" if ch.isdigit() else ch
        if not out or out[-1] != c or c not in "A9":
            out.append(c)
    return "".join(out)
