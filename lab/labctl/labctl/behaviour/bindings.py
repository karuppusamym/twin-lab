"""Synthetic input bindings: the values a replay types, one per recorded step.

A recording keeps no typed value. To replay it, a person (or a generator) writes the values in a
bindings file beside the recording, `bindings.yaml`, keyed by step:

```yaml
version: 1
recording: 20261117-101500
steps:
  4:
    action: type
    field: input.new-todo
    shape: A A          # what was typed, as its shape (letters A, digits 9): for reference
    value: Water the plants
  17:
    action: check
    record: Pay the bills   # a step on a record the recording did not create: its title
```

`value` is what a `type` or `select` step enters. `record` names the record (list item, table
row) a step acts on when the recording cannot: the record was already there when recording
started, or its id is masked and several records share its shape. A step on a record created in
the recording needs nothing: its title is the value bound to the step that typed it.

`labctl behaviour bindings <recording>` writes the template, listing every step that needs one.
The older `--value selector=value` and `--value 6:selector=value` still work: they are turned into
bindings for the steps they match. A step that needs a value with no binding blocks the replay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from labctl.behaviour import dom as domlib
from labctl.botlog.recording import Recording, RecStep

FILE = "bindings.yaml"
VALUED = ("type", "select")


@dataclass
class Bindings:
    values: dict[int, str] = field(default_factory=dict)  # seq -> value typed or selected
    records: dict[int, str] = field(default_factory=dict)  # seq -> title of the record acted on

    def merged(self, other: Bindings) -> Bindings:
        """These bindings, with `other` filling the steps these do not bind."""
        return Bindings({**other.values, **self.values}, {**other.records, **self.records})


@dataclass
class Need:
    seq: int
    kind: str  # "value" | "record"
    action: str
    field: str | None
    shape: str | None = None
    note: str | None = None


def record_candidate(step: RecStep) -> dict | None:
    """The step's per-record candidate when it is the primary one (its first locator)."""
    locs = [c for c in step.locators if c.get("kind") not in ("id", "testid")]
    if locs and locs[0].get("kind") == "record":
        return locs[0]
    return None


def needs_value(step: RecStep) -> bool:
    return step.action in VALUED and step.blank is None and step.value is None


def needs(rec: Recording) -> list[Need]:
    """Every step that needs a binding, in order."""
    out: list[Need] = []
    for step in rec.steps:
        if needs_value(step) and recorded_value(rec, step) is None:
            out.append(
                Need(
                    step.seq,
                    "value",
                    step.action,
                    step.selector,
                    step.typed_shape,
                    None if step.typed_shape else "shape not known",
                )  # fmt: skip
            )
        cand = record_candidate(step)
        if cand is not None and cand.get("text_from") is None:
            out.append(
                Need(
                    step.seq,
                    "record",
                    step.action,
                    step.selector,
                    cand.get("title_shape"),
                    "acts on a record the recording did not create: give its synthetic title",
                )  # fmt: skip
            )
    return out


def template(rec: Recording, existing: Bindings | None = None) -> str:
    """The bindings file for a recording: every step that needs a value, with its shape."""
    existing = existing or Bindings()
    steps: dict[int, dict] = {}
    for n in needs(rec):
        entry = steps.setdefault(n.seq, {"action": n.action, "field": n.field})
        if n.kind == "value":
            entry["shape"] = n.shape
            entry["value"] = existing.values.get(n.seq)
        else:
            entry["record_shape"] = n.shape
            entry["record"] = existing.records.get(n.seq)
        if n.note:
            entry["note"] = n.note
    head = (
        "# Synthetic values for replaying this recording, one per step. Never a real value.\n"
        "# value: what the step types or selects. record: the title of the record the step\n"
        "# acts on. shape is what was recorded (letters A, digits 9), for reference only.\n"
    )
    body = {"version": 1, "recording": rec.id, "steps": steps}
    return head + yaml.safe_dump(body, sort_keys=False, allow_unicode=True)


def write_template(rec: Recording, out: Path | None = None) -> Path:
    """Write (or complete) the bindings file; values already given are kept."""
    out = Path(out) if out else rec.path / FILE
    existing = load(out) if out.exists() else None
    out.write_text(template(rec, existing), encoding="utf-8")
    return out


def load(path: Path | None) -> Bindings:
    """Read a bindings file. `steps: {4: "text"}` is short for `{4: {value: "text"}}`."""
    if path is None or not Path(path).exists():
        return Bindings()
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a bindings file")
    out = Bindings()
    for key, entry in (data.get("steps") or {}).items():
        try:
            seq = int(key)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path}: step {key!r} is not a step number") from exc
        if isinstance(entry, (str, int, float)):
            out.values[seq] = str(entry)
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("value") is not None:
            out.values[seq] = str(entry["value"])
        if entry.get("record") is not None:
            out.records[seq] = str(entry["record"])
    return out


def selector_of(name: str) -> str:
    """The selector of a `values` key: `"6:#loanQuery"` -> `"#loanQuery"`."""
    head, sep, rest = name.partition(":")
    return rest if sep and head.isdigit() else name


def from_values(rec: Recording, values: dict[str, str] | None) -> Bindings:
    """The older per-selector values as bindings: `6:sel` binds step 6, `sel` every step that
    types into that field and has no other value."""
    out = Bindings()
    for step in rec.steps if values else []:
        if step.action not in VALUED or not step.selector:
            continue
        for key in (f"{step.seq}:{step.selector}", step.selector):
            if key in values:
                out.values[step.seq] = values[key]
                break
    return out


def recorded_value(rec: Recording, step: RecStep) -> str | None:
    """A value the recording itself holds: written by the harness bot (`value`, never for a
    Private field), or, for older recordings, the field's value in the after-snapshot."""
    if step.value is not None:
        return step.value
    if step.blank is not None:
        return step.blank
    if new_format(rec):  # this recorder takes typed values out of its snapshots
        return None
    html = rec.dom(step)
    key = domlib.key_of(step.selector)
    if html is None or key is None:
        return None
    el = domlib.snapshot(html).elements.get(key)
    if el is None:
        return None
    return el.selected if el.tag == "select" else el.value


def new_format(rec: Recording) -> bool:
    """Written by the recorder that keeps locators (and takes typed values out of snapshots)."""
    return any(s.locators or s.nav for s in rec.steps)


def supplied_keys(rec: Recording, bindings: Bindings) -> set[str]:
    """Element keys of fields given a value for the replay: their value format is not compared."""
    out = set()
    for step in rec.steps:
        if step.seq in bindings.values:
            key = domlib.key_of(step.selector)
            if key:
                out.add(key)
    return out
