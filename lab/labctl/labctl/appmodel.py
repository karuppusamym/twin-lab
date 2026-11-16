"""The app model: everything known about one application, whatever the evidence.

Every item records the evidence it came from (`from`) and how sure we are (`basis`). The brief,
the schema and the conformance check are generated from this model only. See docs/06-evidence.md.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from labctl.models import KINDS, Contract, Slug

Basis = Literal["observed", "declared", "inferred", "described", "assumed"]
STRENGTH: dict[str, int] = {
    "observed": 5,
    "declared": 4,
    "inferred": 3,
    "described": 2,
    "assumed": 1,
}
EvidenceKind = Literal[
    "source", "automation", "flow", "har", "ddl", "openapi", "postman", "screenshots", "robot_log"
]
Area = Literal["screens", "routes", "tables"]
# When evidence of different kinds disagrees, the earlier kind wins (docs/06-evidence.md).
PRIORITY: tuple[str, ...] = (
    "source",
    "har",
    "ddl",
    "openapi",
    "postman",
    "automation",
    "screenshots",
    "flow",
)


def stronger(a: str, b: str) -> str:
    return a if STRENGTH[a] >= STRENGTH[b] else b


class Item(Contract):
    basis: Basis
    from_: list[str] = Field(default_factory=list, alias="from")


class Where(Contract):
    file: str
    line: int | None = None


class Evidence(Contract):
    id: str
    kind: EvidenceKind
    ref: str | None = None  # a digest or a location; never the content
    complete: list[Area] = []  # areas this evidence lists exhaustively (source: all three)


class Element(Item):
    selector: str | None = None  # None for a field a person described but nobody located
    label: str | None = None
    actions: list[str] = []
    # Seen in a page snapshot (recordings): what the element is, never what it held.
    tag: str | None = None
    name: str | None = None
    type: str | None = None
    classes: list[str] = []
    options: list[str] = []  # option texts of a select


class Call(Contract):
    method: str
    path: str
    at: Where | None = None


class Screen(Item):
    name: str
    aliases: list[str] = []  # other names the same screen goes by in other evidence
    path: str | None = None
    component: str | None = None
    at: Where | None = None  # where the screen's route is declared
    files: list[str] = []  # files that render it, when known
    ids: list[str] = []  # every element id seen on the screen
    classes: list[str] = []  # every class seen on the screen
    elements: list[Element] = []  # elements somebody needs
    calls: list[Call] = []  # API calls the screen makes
    snapshots: int | None = None  # recordings: page snapshots seen; 0 means address only


class Timing(Contract):
    count: int
    p50_ms: int
    p95_ms: int
    max_ms: int


class Route(Item):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str
    handler: str | None = None
    at: Where | None = None
    doc: str | None = None
    query: list[str] = []
    body: list[str] = []  # request body fields with their constraints
    tables: list[str] = []
    responses: dict[str, dict] = {}  # status -> response shape (field -> type, "[]" for items)
    timings: Timing | None = None  # recordings: for slowness calibration

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


class Column(Contract):
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False
    unique: bool = False
    index: bool = False
    references: list[str] | None = None  # [table, column]
    autoincrement: bool = False


class Table(Item):
    name: str
    model: str | None = None
    at: Where | None = None
    columns: list[Column] = []


class Rule(Item):
    text: str
    where: list[str] = []  # route keys or screen names
    status: int | None = None
    code: str | None = None


class Conflict(Contract):
    what: str
    evidence: list[str] = []


class Stack(Contract):
    frontend: dict | None = None
    backend: dict | None = None
    orm: dict | None = None
    db: dict | None = None
    pinned: dict[str, str] = {}
    env: list[str] = []
    from_: list[str] = Field(default_factory=list, alias="from")


class AppModel(Contract):
    app: Slug
    evidence: list[Evidence] = []
    stack: Stack = Stack()
    screens: list[Screen] = []
    routes: list[Route] = []
    tables: list[Table] = []
    rules: list[Rule] = []
    flows: list[str] = []
    codes: dict[str, list[str]] = {}  # "entity.field" -> code values seen (low cardinality only)
    conflicts: list[Conflict] = []
    problems: list[str] = []

    def complete(self, area: str) -> list[str]:
        """Ids of evidence that lists this area exhaustively."""
        return [e.id for e in self.evidence if area in e.complete]

    def route(self, key: str) -> Route:
        return next(r for r in self.routes if r.key == key)

    def table(self, name: str) -> Table:
        return next(t for t in self.tables if t.name == name)


# --- flows a person asks for -------------------------------------------------------------------


class FlowStep(Contract):
    screen: str = Field(min_length=1)  # a screen's name as people call it
    path: str | None = None  # its address, when known
    do: str = ""
    fields: list[str] = []
    rules: list[str] = []


class Flow(Contract):
    id: Slug
    app: Slug
    requested_by: str
    purpose: str = ""
    steps: list[FlowStep] = Field(min_length=1)
    evidence: list[str] = []


KINDS.update({"model": AppModel, "flow": Flow})
