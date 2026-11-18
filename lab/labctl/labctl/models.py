"""Contracts shared by every part of Twin Lab. See docs/03-contracts.md.

Each model has an example under specs/ that the tests load. Change a model, change its example.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*$")]


class Contract(BaseModel):
    """Base for all contracts: unknown keys are errors, not silently ignored."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --- automation.yaml ---------------------------------------------------------------------------


class AutomationType(str, Enum):
    uipath = "uipath"
    agentic = "agentic"
    python = "python"
    other = "other"


class Surface(str, Enum):
    frontend = "frontend"
    backend = "backend"
    combo = "combo"


class Source(Contract):
    path: str | None = None
    entry: str | None = None
    repo: str | None = None
    ref: str | None = None

    @model_validator(mode="after")
    def _path_or_repo(self) -> Source:
        if not self.path and not self.repo:
            raise ValueError("source needs either 'path' or 'repo'")
        return self


class ElementUse(Contract):
    action: Literal["type", "click", "read", "select", "wait", "hover"]
    selector: str = Field(min_length=1)  # CSS, or "uipath:<original>" when CSS cannot express it
    field: str | None = None  # the variable carried, never a typed value
    raw: str | None = None  # the selector as the automation wrote it


class Screen(Contract):
    name: str
    path: str | None = None
    uses: list[ElementUse] = []


class WebTouch(Contract):
    app: Slug
    via: Literal["web"]
    screens: list[Screen] = Field(min_length=1)


class Route(Contract):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(pattern=r"^/")
    # Field names the automation's code uses, never values. Dotted, `[]` for list items:
    # `items[].review_no`. The demo app must accept `sends` and return every field in `reads`.
    sends: list[str] = []
    reads: list[str] = []


class ApiTouch(Contract):
    app: Slug
    via: Literal["api"]
    routes: list[Route] = Field(min_length=1)


class TableUse(Contract):
    table: str
    columns: list[str] = []
    access: Literal["read", "write", "read_write"]


class DbTouch(Contract):
    app: Slug
    via: Literal["db"]
    tables: list[TableUse] = Field(min_length=1)


class FileTouch(Contract):
    app: Slug
    via: Literal["file"]
    paths: list[str] = Field(min_length=1)


Touch = Annotated[WebTouch | ApiTouch | DbTouch | FileTouch, Field(discriminator="via")]


class Gap(Contract):
    """Something an analyser saw and could not interpret. Never dropped; a person resolves it."""

    where: str
    what: str


class Analysis(Contract):
    by: str | None = None
    gaps: list[Gap] = []  # block readiness until a person resolves each one
    notes: list[Gap] = []  # worth a look, do not block; the conformance check settles them
    reviewed_by: str | None = None


ChangeOp = Literal["insert", "update", "delete"]


class ExpectedChange(Contract):
    """A row change an automation is allowed to make. A session passes when every change it
    recorded matches one of these; anything else is an unexpected change and fails it."""

    table: str = Field(min_length=1)
    ops: list[ChangeOp] = Field(min_length=1)
    columns: list[str] = []  # for update: the only columns that may change; empty means any
    max_rows: int | None = Field(default=None, ge=1)  # per op; None means any number


OutcomeKind = Literal["changes", "read_only", "rejected", "failed_run"]


class OutcomeRequire(Contract):
    """Changes a run must make: at least `min_rows` rows of `table` changed by `op`."""

    table: str = Field(min_length=1)
    op: ChangeOp
    min_rows: int = Field(default=1, ge=1)
    max_rows: int | None = Field(default=None, ge=1)
    columns: list[str] = []  # update: columns that must be among those changed
    set: dict[str, str] = {}  # column -> expected final value, compared as strings

    @field_validator("set", mode="before")
    @classmethod
    def _values_as_text(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: v if isinstance(v, str) else str(v) for k, v in value.items()}
        return value

    @model_validator(mode="after")
    def _consistent(self) -> OutcomeRequire:
        if self.max_rows is not None and self.max_rows < self.min_rows:
            raise ValueError("max_rows is below min_rows")
        if self.columns and self.op != "update":
            raise ValueError("columns applies to update only")
        if self.set and self.op == "delete":
            raise ValueError("set cannot be checked on a delete")
        return self


class OutcomeCase(Contract):
    """One acceptance case: what business success means for a run. Kept apart from `expects:`
    (what a run may change); it never alters that verdict."""

    name: Slug
    kind: OutcomeKind
    when: str | None = None  # the harness condition or input label this case applies to
    description: str | None = None
    requires: list[OutcomeRequire] = []  # kind `changes` only

    @model_validator(mode="after")
    def _requires_match_kind(self) -> OutcomeCase:
        if self.kind == "changes" and not self.requires:
            raise ValueError("a `changes` case needs `requires:`")
        if self.kind != "changes" and self.requires:
            raise ValueError(f"a `{self.kind}` case takes no `requires:`")
        return self


class Automation(Contract):
    id: Slug
    name: str
    type: AutomationType
    surface: Surface
    trigger: Literal["manual"] = "manual"
    owner: str | None = None
    source: Source
    replaces: Slug | None = None
    replaced_by: Slug | None = None
    touches: list[Touch] = []
    bindings: dict[str, str] = {}
    test_inputs: dict[str, str] = {}  # values for the conformance check, e.g. in_CaseNo: C-1003
    # The row changes a run may make. None: not declared yet (no verdict). []: changes nothing.
    expects: list[ExpectedChange] | None = None
    # What business success means: acceptance cases judged apart from `expects`. None: not declared.
    outcome: list[OutcomeCase] | None = None
    analysis: Analysis = Analysis()

    @model_validator(mode="after")
    def _surface_matches_touches(self) -> Automation:
        vias = {t.via for t in self.touches}
        if self.surface is Surface.backend and "web" in vias:
            raise ValueError("surface is 'backend' but touches include a web screen")
        if self.surface is Surface.frontend and vias - {"web"}:
            raise ValueError("surface is 'frontend' but touches include non-web interfaces")
        return self

    @model_validator(mode="after")
    def _outcome_names_unique(self) -> Automation:
        names = [c.name for c in self.outcome or []]
        if len(names) != len(set(names)):
            raise ValueError("outcome case names must be unique")
        return self

    def apps(self) -> list[str]:
        return sorted({t.app for t in self.touches})


# --- app.yaml ----------------------------------------------------------------------------------


class Verification(str, Enum):
    hypothesis = "hypothesis"
    owner_approved = "owner_approved"
    reference_verified = "reference_verified"


class Copies(Contract):
    system: str
    source: str | None = None
    ref: str | None = None
    analysed: date | None = None


class FrontendStack(Contract):
    framework: str
    version: str | None = None  # unknown for a server-rendered app
    ui_library: str | None = None
    marker: str | None = None  # what identified the framework in a recording


class BackendStack(Contract):
    framework: str
    version: str
    language: str | None = None


class DbStack(Contract):
    engine: str
    version: str | None = None


class DataStack(Contract):
    """Where the application's business data lives, and what shows it (R07).

    `browser`: the page changes state with no state-changing request to its own origin, so the data
    is in browser storage. `server`: state-changing requests (or responses carrying the records)
    were seen. `unknown`: neither can be shown; nothing is invented. Storage KEYS and the shape of
    stored records may be kept; stored values are data and never are.
    """

    kind: Literal["browser", "server", "unknown"]
    storage: list[str] = []  # kinds observed holding data: localStorage, sessionStorage, ...
    referenced: list[str] = []  # kinds a script mentions (not proof that data is written)
    keys: list[str] = []  # storage key names seen in the recorded storage
    script_keys: list[str] = []  # key names written as literals in the page's scripts (not seen)
    shape: dict = {}  # key -> shape of the stored record, values masked
    evidence: list[str] = []
    # The five findings: server | storage | navigation | transient | unknown (trial P1). `kind` is
    # only what decides on a database; this says what the recording shows about persistence.
    persistence: Literal["server", "storage", "navigation", "transient", "unknown"] | None = None
    label: str | None = (
        None  # the finding in words, e.g. "browser (transient or unknown persistence)"
    )
    overridden: bool = False  # a person named the kind (`labctl brief --data`)


class Stack(Contract):
    frontend: FrontendStack | None = None
    backend: BackendStack | None = None
    db: DbStack | None = None
    data: DataStack | None = None  # absent: a database-backed app, as before R07


class WebRun(Contract):
    port: int = Field(gt=0, lt=65536)


class ApiRun(Contract):
    port: int = Field(gt=0, lt=65536)
    health: str = Field(pattern=r"^/")


class RunEnv(Contract):
    database_url: str = "DATABASE_URL"


class Run(Contract):
    web: WebRun | None = None
    api: ApiRun | None = None
    env: RunEnv = RunEnv()


class AppDb(Contract):
    schema_path: str = Field(alias="schema")
    derived_from: list[str] = []


class Rule(Contract):
    id: str
    where: str
    rule: str
    verification: Verification = Verification.hypothesis


class Link(Contract):
    entity: str
    column: str
    to: str
    via: Literal["equal", "crosswalk"]
    table: str | None = None


class Lab(Contract):
    """How the lab runs this app: host ports and the test sign-in the data provides."""

    port: int = Field(gt=0, lt=65536)  # the lab agent: the app's front door
    # The database, through the slowdown proxy. Needed for every database but sqlite (no port).
    db_port: int | None = Field(default=None, gt=0, lt=65536)
    credentials: dict[str, str] = {}  # test values only, e.g. username/password from the seed
    # Where the database lives: "postgres" (Twin Lab's container, the default), "sqlserver"
    # (Twin Lab's SQL Server container), "sqlite" (a file on a volume the app and the lab agent
    # share), or the name of a server in twinlab.local.yaml.
    database: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][\w.-]*$")


class App(Contract):
    id: Slug
    name: str
    mode: str | None = None  # copy | reconstruction | skeleton | backend | sample (06-evidence.md)
    copies: Copies
    stack: Stack
    run: Run
    db: AppDb | None = None
    built_for: list[Slug] = []
    rules: list[Rule] = []
    links: list[Link] = []
    lab: Lab | None = None

    @property
    def browser_only(self) -> bool:
        """The app keeps its business data in the browser: no database, no data controls."""
        return self.stack.data is not None and self.stack.data.kind == "browser"


# --- data pack ---------------------------------------------------------------------------------


class Generator(Contract):
    seed: int
    rows: dict[str, int] = {}


class DataPack(Contract):
    id: Slug
    apps: list[Slug] = Field(min_length=1)
    kind: Literal["synthetic", "byo"]
    generator: Generator | None = None
    workbook: str | None = None
    derived_from_real_data: bool = False
    masked: bool = False
    schema_ref: dict[str, str] = {}

    @model_validator(mode="after")
    def _coherent(self) -> DataPack:
        if self.kind == "synthetic" and self.generator is None:
            raise ValueError("a synthetic pack needs 'generator'")
        if self.kind == "byo" and not self.workbook:
            raise ValueError("a byo pack needs 'workbook'")
        if self.kind == "synthetic" and self.derived_from_real_data:
            raise ValueError("a synthetic pack cannot be derived_from_real_data")
        if self.derived_from_real_data and not self.masked:
            raise ValueError("derived_from_real_data requires masked: true")
        return self


# --- condition profile -------------------------------------------------------------------------


class Latency(Contract):
    p50: int = Field(ge=0)
    p95: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> Latency:
        if self.p95 < self.p50:
            raise ValueError("p95 must be at least p50")
        return self


class HttpRule(Contract):
    match: str = Field(min_length=1)
    latency_ms: Latency


class DbLink(Contract):
    latency_ms: int = Field(ge=0)
    jitter_ms: int = Field(default=0, ge=0)


class Fault(Contract):
    match: str = Field(min_length=1)
    status: int = Field(ge=400, le=599)
    rate: float = Field(gt=0, le=1)


class ConditionTarget(Contract):
    app: Slug
    http: list[HttpRule] = []
    db: DbLink | None = None
    faults: list[Fault] = []


class Condition(Contract):
    id: Slug
    description: str = ""
    calibration: Literal["robot_logs", "har", "estimate"]
    calibrated_from: str | None = None
    seed: int | None = None
    targets: list[ConditionTarget] = []


# --- session -----------------------------------------------------------------------------------


class SessionApp(Contract):
    app: Slug
    version: str
    data_pack: Slug | None = None
    condition: Slug | None = None


class RequestRecord(Contract):
    ts: datetime
    app: Slug
    method: str
    path: str
    status: int
    app_ms: int = Field(ge=0)
    injected_ms: int = Field(default=0, ge=0)


class Change(Contract):
    app: Slug
    table: str
    op: ChangeOp
    key: str
    columns: list[str] = []  # for an update: the columns that changed


class SessionSummary(Contract):
    requests: int = Field(ge=0)
    wall_s: float = Field(ge=0)
    waiting_on_app_s: float = Field(ge=0)
    errors: int = Field(ge=0)


class Session(Contract):
    session_id: str = Field(pattern=r"^ses_\d{8}T\d{6}Z_[0-9a-f]{6}$")
    automation: Slug | None = None
    label: str | None = None
    apps: list[SessionApp] = Field(min_length=1)
    started: datetime
    ended: datetime | None = None
    requests: list[RequestRecord] = []
    changes: list[Change] = []
    summary: SessionSummary | None = None


# --- bindings ----------------------------------------------------------------------------------


class AppBinding(Contract):
    web: str | None = None
    api: str | None = None
    db: str | None = None  # a database URL (Python bots: SQLAlchemy, psycopg ...)
    db_engine: str | None = None  # postgresql | mssql | oracle
    db_connection_string: str | None = None  # ADO.NET style, for UiPath and .NET bots
    credentials: dict[str, str] = {}
    # Application Twin Studio's names, so ATS automations (ats/sdk.py Twin.http) run unchanged:
    http: str | None = None  # = api
    auth: dict[str, str] | None = None  # {"bearer": ...} when the app takes a fixed token


class Bindings(Contract):
    apps: dict[Slug, AppBinding]
    inputs: dict[str, str] = {}
    output_path: str | None = None
    arguments: dict[str, str] = {}  # the automation's `bindings:` with every template resolved


KINDS: dict[str, type[Contract]] = {
    "automation": Automation,
    "app": App,
    "pack": DataPack,
    "condition": Condition,
    "session": Session,
    "bindings": Bindings,
}
