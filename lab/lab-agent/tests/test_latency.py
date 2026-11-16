import random
import statistics

import pytest

from lab_agent.conditions import AgentCondition, ConditionEngine, parse_condition
from lab_agent.latency import LogNormal


def test_lognormal_fits_p50_and_p95():
    dist = LogNormal(4000, 15000)
    rng = random.Random(1)
    draws = sorted(dist.sample(rng) for _ in range(40000))
    p50 = draws[len(draws) // 2]
    p95 = draws[int(len(draws) * 0.95)]
    assert p50 == pytest.approx(4000, rel=0.05)
    assert p95 == pytest.approx(15000, rel=0.07)
    assert max(draws) <= 10 * 15000  # capped


def test_quantiles_are_exact_for_z():
    dist = LogNormal(400, 1200)
    assert dist.from_normal(0) == pytest.approx(400)
    assert dist.from_normal(1.6448536269514722) == pytest.approx(1200)


def test_degenerate_distributions():
    assert LogNormal(800, 800).from_normal(2.5) == pytest.approx(800)
    assert LogNormal(0, 0).from_normal(3) == 0
    assert LogNormal(0, 100).from_normal(-1) == 0
    with pytest.raises(ValueError):
        LogNormal(10, 5)


def _condition(seed):
    return AgentCondition.model_validate(
        {
            "id": "peak",
            "seed": seed,
            "http": [
                {"match": "GET /api/cases/search*", "latency_ms": {"p50": 4000, "p95": 15000}},
                {"match": "*", "latency_ms": {"p50": 400, "p95": 1200}},
            ],
            "faults": [{"match": "POST /api/cases/*/notes", "status": 503, "rate": 0.5}],
        }
    )


SEQUENCE = [("GET", "/api/cases/search"), ("POST", "/api/cases/C-1/notes")] * 20 + [
    ("GET", "/api/categories")
] * 5


def _run(engine, sequence=SEQUENCE):
    return [
        (round(d.delay_ms, 6), d.fault.status if d.fault else None, d.rule)
        for d in (engine.decide(m, p) for m, p in sequence)
    ]


def test_seed_repeats_delays_and_faults():
    a = _run(ConditionEngine(_condition(7)))
    b = _run(ConditionEngine(_condition(7)))
    assert a == b
    faults = [f for _, f, _ in a[1:40:2]]
    assert any(faults) and not all(faults)
    assert a != _run(ConditionEngine(_condition(8)))


def test_rewind_repeats_and_interleaving_does_not_matter():
    engine = ConditionEngine(_condition(7))
    first = _run(engine)
    engine.rewind()
    assert _run(engine) == first
    # Same order per route, different interleaving between routes: the same draws per route.
    engine.rewind()
    shuffled = [s for s in SEQUENCE if s[0] == "POST"] + [s for s in SEQUENCE if s[0] == "GET"]
    by_route: dict = {}
    for key, d in zip(shuffled, _run(engine, shuffled), strict=True):
        by_route.setdefault(key, []).append(d)
    expected: dict = {}
    for key, d in zip(SEQUENCE, first, strict=True):
        expected.setdefault(key, []).append(d)
    assert by_route == expected


def test_first_rule_wins_and_unseeded_varies():
    engine = ConditionEngine(_condition(None))
    rules = {engine.decide("GET", "/api/cases/search").rule for _ in range(3)}
    assert rules == {"GET /api/cases/search*"}
    assert engine.decide("GET", "/x").rule == "*"
    delays = [engine.decide("GET", "/api/cases/search").delay_ms for _ in range(20)]
    assert statistics.pstdev(delays) > 0


def test_fault_rate_is_close():
    engine = ConditionEngine(_condition(3))
    n = 4000
    faults = sum(engine.decide("POST", "/api/cases/C-1/notes").fault is not None for _ in range(n))
    assert faults / n == pytest.approx(0.5, abs=0.03)


def test_profile_reduces_to_this_app():
    profile = {
        "id": "peak-hour",
        "calibration": "estimate",
        "seed": 7,
        "targets": [
            {"app": "other", "db": {"latency_ms": 5}},
            {"app": "showcase", "db": {"latency_ms": 150, "jitter_ms": 80}, "http": []},
        ],
    }
    cond = parse_condition(profile, "showcase")
    assert cond.id == "peak-hour" and cond.seed == 7 and cond.db.latency_ms == 150
    assert parse_condition(profile, "nobody").db is None


def test_invalid_condition_rejected():
    with pytest.raises(ValueError):
        parse_condition({"http": [{"match": "GET nope", "latency_ms": {"p50": 1, "p95": 2}}]}, "a")
    with pytest.raises(ValueError):
        parse_condition({"faults": [{"match": "*", "status": 200, "rate": 0.5}]}, "a")
    with pytest.raises(ValueError):
        parse_condition({"bogus": 1}, "a")
