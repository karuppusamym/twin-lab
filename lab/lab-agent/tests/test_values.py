import base64
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from lab_agent.schema import Column
from lab_agent.values import JsonValue, ValueProblem, from_json, to_json


def col(udt, data_type="", max_length=None):
    return Column("c", data_type or udt, udt, True, None, None, max_length, 1)


def test_to_json():
    assert to_json(Decimal("12.50")) == "12.50"
    assert to_json(datetime(2026, 11, 1, 9, 0, tzinfo=UTC)) == "2026-11-01T09:00:00+00:00"
    assert to_json(date(2026, 11, 1)) == "2026-11-01"
    assert to_json(time(9, 30)) == "09:30:00"
    assert to_json(b"\x00\x01") == base64.b64encode(b"\x00\x01").decode()
    assert to_json(memoryview(b"ab")) == "YWI="
    assert to_json(float("nan")) == "nan"
    assert to_json(timedelta(hours=1)) == "3600 seconds"
    u = uuid.uuid4()
    assert to_json(u) == str(u)
    assert to_json({"a": [Decimal("1"), None]}) == {"a": ["1", None]}


@pytest.mark.parametrize(
    ("udt", "value", "expected"),
    [
        ("int4", 5, 5),
        ("int4", "7", 7),
        ("int4", 3.0, 3),
        ("int8", 2**40, 2**40),
        ("numeric", "12.50", Decimal("12.50")),
        ("numeric", 3, Decimal("3")),
        ("float8", "1.5", 1.5),
        ("bool", "true", True),
        ("bool", 0, False),
        ("varchar", 1001, "1001"),
        ("date", "2026-11-01", date(2026, 11, 1)),
        ("timestamptz", "2026-11-01T09:00:00Z", datetime(2026, 11, 1, 9, tzinfo=UTC)),
        ("timestamptz", "2026-11-01T09:00:00", datetime(2026, 11, 1, 9, tzinfo=UTC)),
        ("timestamp", "2026-11-01T10:00:00+01:00", datetime(2026, 11, 1, 9)),
        ("time", "09:30", time(9, 30)),
        ("bytea", "YWI=", b"ab"),
        ("jsonb", {"a": 1}, JsonValue({"a": 1})),
        ("interval", 60, timedelta(seconds=60)),
    ],
)
def test_from_json(udt, value, expected):
    assert from_json(value, col(udt)) == expected


@pytest.mark.parametrize(
    ("udt", "value"),
    [
        ("int4", "abc"),
        ("int4", True),
        ("int4", 1.5),
        ("int2", 40000),
        ("numeric", "x"),
        ("bool", "maybe"),
        ("date", "01/11/2026"),
        ("timestamptz", "yesterday"),
        ("bytea", "not base64!"),
        ("uuid", "nope"),
        ("varchar", {"a": 1}),
    ],
)
def test_from_json_rejects(udt, value):
    with pytest.raises(ValueProblem):
        from_json(value, col(udt))


def test_text_length_and_arrays():
    with pytest.raises(ValueProblem, match="maximum 3"):
        from_json("abcd", col("varchar", max_length=3))
    assert from_json(["1", 2], col("_int4", "ARRAY")) == [1, 2]
    with pytest.raises(ValueProblem):
        from_json("1,2", col("_int4", "ARRAY"))


def test_round_trip():
    values = {
        "numeric": Decimal("10.25"),
        "timestamptz": datetime(2026, 11, 1, 9, 0, 0, 123000, tzinfo=UTC),
        "date": date(2026, 1, 2),
        "bytea": b"\xff\x00",
        "uuid": uuid.UUID("12345678-1234-5678-1234-567812345678"),
        "bool": True,
    }
    for udt, v in values.items():
        assert from_json(to_json(v), col(udt)) == v
