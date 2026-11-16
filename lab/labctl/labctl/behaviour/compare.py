"""Does the application behave the same in two places? (Prod Echo I-22b)

Build a behaviour catalog from each set of recordings (for example the flow recorded in UAT and
the same flow recorded in production) and compare them rule by rule:

- `only_a` / `only_b`: a behaviour seen in one place and never in the other (a field that shows,
  a button that enables, a mask). If the other set never even exercised the trigger, it is listed
  as `not_exercised` instead, because absence then proves nothing;
- `differs`: the same behaviour with a different outcome (other options in a cascade, another
  format, another value);
- delays are compared only when they differ by more than a factor of two, since timings vary.

A demo app built from UAT evidence is trustworthy for production only where the two agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from labctl.behaviour import catalog as catalog_mod
from labctl.behaviour.catalog import Catalog, Rule

DELAY_FACTOR = 2.0


@dataclass
class Difference:
    kind: str  # only_a | only_b | differs | not_exercised
    rule: str  # the rule as a sentence
    detail: str = ""


@dataclass
class Comparison:
    a: str
    b: str
    same: int = 0
    differences: list[Difference] = field(default_factory=list)

    @property
    def status(self) -> str:
        real = [d for d in self.differences if d.kind != "not_exercised"]
        return "same" if not real else "different"

    def markdown(self) -> str:
        lines = [f"# Behaviour: {self.a} against {self.b}", "",
                 f"**{self.status.upper()}**: {self.same} behaviour(s) agree, "
                 f"{len([d for d in self.differences if d.kind != 'not_exercised'])} differ.", ""]  # fmt: skip
        heads = {
            "only_a": f"Seen only in {self.a}",
            "only_b": f"Seen only in {self.b}",
            "differs": "Same behaviour, different outcome",
            "not_exercised": "Not comparable: the other side never did this",
        }
        for kind, head in heads.items():
            items = [d for d in self.differences if d.kind == kind]
            if items:
                lines += [f"## {head}", ""]
                lines += [f"- {d.rule}" + (f" ({d.detail})" if d.detail else "") for d in items]
                lines.append("")
        return "\n".join(lines)


def _key(rule: Rule) -> tuple:
    return (rule.kind, rule.screen, rule.trigger.element, rule.trigger.event, rule.target)


def _sentence(rule: Rule) -> str:
    text = catalog_mod.describe(rule)
    return text.split(" (seen ", 1)[0]  # counts belong to one side only


def _triggers(cat: Catalog) -> set[tuple]:
    return {(r.screen, r.trigger.element, r.trigger.event) for r in cat.rules}


def _detail_differs(ra: Rule, rb: Rule) -> str | None:
    if ra.kind == "delay":

        def typical(rule: Rule) -> int:  # a catalog keeps p50_ms and the list of ms seen
            d = rule.detail
            value = d.get("p50_ms", d.get("ms") or 0)
            return int(value[len(value) // 2] if isinstance(value, list) and value else value or 0)

        ma, mb = typical(ra), typical(rb)
        low, high = sorted((max(ma, 1), max(mb, 1)))
        return f"{ma} ms against {mb} ms" if high / low > DELAY_FACTOR else None
    da = {k: v for k, v in ra.detail.items() if k != "requests"}
    db = {k: v for k, v in rb.detail.items() if k != "requests"}
    return f"{da} against {db}" if da != db else None


def compare(a: Catalog, b: Catalog, name_a: str = "A", name_b: str = "B") -> Comparison:
    out = Comparison(name_a, name_b)
    rules_a = {_key(r): r for r in a.rules}
    rules_b = {_key(r): r for r in b.rules}
    trig_a, trig_b = _triggers(a), _triggers(b)
    for key in sorted(rules_a.keys() | rules_b.keys(), key=str):
        ra, rb = rules_a.get(key), rules_b.get(key)
        if ra and rb:
            why = _detail_differs(ra, rb)
            if why:
                out.differences.append(Difference("differs", _sentence(ra), why))
            else:
                out.same += 1
            continue
        rule = ra or rb
        trigger = (rule.screen, rule.trigger.element, rule.trigger.event)
        other_did = trigger in (trig_b if ra else trig_a)
        if not other_did:
            where = name_b if ra else name_a
            out.differences.append(Difference("not_exercised", _sentence(rule),
                                              f"{where} never did this"))  # fmt: skip
        else:
            out.differences.append(Difference("only_a" if ra else "only_b", _sentence(rule)))
    return out


def compare_recordings(a: list[Path], b: list[Path], name_a: str, name_b: str) -> Comparison:
    return compare(catalog_mod.build(a), catalog_mod.build(b), name_a, name_b)
