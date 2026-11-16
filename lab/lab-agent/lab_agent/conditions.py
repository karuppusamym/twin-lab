"""The active condition for this app, and the decisions it makes per request.

Stored form (`GET/PUT /_lab/condition`):

    {"id": "peak-hour", "seed": 7, "description": "...",
     "http":   [{"match": "GET /api/cases/search*", "latency_ms": {"p50": 4000, "p95": 15000}}],
     "db":     {"latency_ms": 150, "jitter_ms": 80},
     "faults": [{"match": "POST /api/cases/*/notes", "status": 503, "rate": 0.05}]}

`PUT` also takes a whole condition profile (`conditions/<id>.yaml` as JSON, with `targets`); the
target for this app is used.

Repeatability: each request draws from its own generator, seeded from the condition seed, the
request's method and path, and how many times that method and path have been seen since the
condition was applied (or the counters were rewound). So the same request sequence gives the same
delays and faults, even when requests to different routes interleave differently.
"""

from __future__ import annotations

import random
import secrets
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lab_agent.latency import LogNormal
from lab_agent.matching import Matcher, PatternError, compile_match
from labctl.models import Condition, DbLink, Fault, HttpRule

Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*$")]


class AgentCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Slug | None = None
    description: str = ""
    seed: int | None = None
    http: list[HttpRule] = []
    db: DbLink | None = None
    faults: list[Fault] = []

    @field_validator("http", "faults")
    @classmethod
    def _patterns_compile(cls, rules: list[Any]) -> list[Any]:
        for rule in rules:
            try:
                compile_match(rule.match)
            except PatternError as exc:
                raise ValueError(str(exc)) from exc
        return rules

    @property
    def is_empty(self) -> bool:
        return not (
            self.http or self.faults or (self.db and (self.db.latency_ms or self.db.jitter_ms))
        )


NONE = AgentCondition()


def parse_condition(body: Any, app_id: str) -> AgentCondition:
    """A stored-form condition, or a whole profile reduced to this app's target."""
    if body is None:
        return NONE
    if not isinstance(body, dict):
        raise ValueError("condition must be a JSON object")
    if "targets" in body or "calibration" in body:
        profile = Condition.model_validate(body)
        target = next((t for t in profile.targets if t.app == app_id), None)
        return AgentCondition(
            id=profile.id,
            description=profile.description,
            seed=profile.seed,
            http=target.http if target else [],
            db=target.db if target else None,
            faults=target.faults if target else [],
        )
    return AgentCondition.model_validate(body)


@dataclass(frozen=True)
class Decision:
    delay_ms: float
    fault: Fault | None
    rule: str | None  # the http rule that matched


class ConditionEngine:
    def __init__(self, condition: AgentCondition = NONE) -> None:
        self.apply(condition)

    def apply(self, condition: AgentCondition) -> None:
        self.condition = condition
        self._http: list[tuple[Matcher, LogNormal]] = [
            (compile_match(r.match), LogNormal(r.latency_ms.p50, r.latency_ms.p95))
            for r in condition.http
        ]
        self._faults: list[tuple[Matcher, Fault]] = [
            (compile_match(f.match), f) for f in condition.faults
        ]
        self.rewind()

    def rewind(self) -> None:
        """Start the request sequence again: the next requests repeat the same draws."""
        seed = self.condition.seed
        self._base = str(seed) if seed is not None else "random:" + secrets.token_hex(8)
        self._counts: dict[str, int] = {}
        self._db_connections = 0

    def rng_for(self, method: str, path: str) -> random.Random:
        key = f"{method.upper()} {path}"
        n = self._counts.get(key, 0)
        self._counts[key] = n + 1
        return random.Random(f"{self._base}|{key}|{n}")

    def decide(self, method: str, path: str, query: str = "") -> Decision:
        if not self._http and not self._faults:
            return Decision(0.0, None, None)
        rng = self.rng_for(method, path)
        u_fault = rng.random()  # both draws always happen, so one rule never shifts the other
        z = rng.gauss(0.0, 1.0)
        delay, rule = 0.0, None
        for matcher, dist in self._http:
            if matcher.matches(method, path, query):
                delay, rule = dist.from_normal(z), matcher.source
                break
        fault = None
        for matcher, f in self._faults:
            if matcher.matches(method, path, query):
                fault = f if u_fault < f.rate else None
                break
        return Decision(delay, fault, rule)

    # --- database link ----------------------------------------------------------------------

    @property
    def db_link(self) -> DbLink | None:
        db = self.condition.db
        if db is None or (db.latency_ms == 0 and db.jitter_ms == 0):
            return None
        return db

    def db_rng(self) -> random.Random:
        n = self._db_connections
        self._db_connections += 1
        return random.Random(f"{self._base}|db|{n}")
