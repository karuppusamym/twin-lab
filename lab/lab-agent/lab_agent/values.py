"""JSON-safe values, both ways.

Out of the database (`to_json`):

| Postgres | JSON |
|---|---|
| integer, bigint, smallint, real, double | number (NaN and infinity as strings) |
| numeric | **string**, always (keeps precision; `"12.50"`) |
| boolean | true / false |
| text, varchar, char | string |
| date, time, timestamp | ISO 8601 string (`timestamptz` with offset, read in UTC) |
| interval | `"[-]<d> days <s>.<micro> seconds"`, exact (Postgres reads it back) |
| bytea | base64 string |
| uuid, inet and anything else | string |
| json, jsonb | the JSON value itself |
| arrays | lists of the above |

Into the database (`from_json`): the same forms, plus numbers or numeric strings for numeric
columns, numbers for text columns, `"true"`/`"false"`/`1`/`0` for booleans. A naive timestamp for
a `timestamptz` column is taken as UTC.
"""

from __future__ import annotations

import base64
import binascii
import math
import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from lab_agent.schema import Column

INT_RANGES = {
    "int2": (-(2**15), 2**15 - 1),
    "int4": (-(2**31), 2**31 - 1),
    "int8": (-(2**63), 2**63 - 1),
}
TEXT_TYPES = {"varchar", "bpchar", "text", "name", "citext", "char"}
TRUE = {"true", "t", "yes", "y", "1", "on"}
FALSE = {"false", "f", "no", "n", "0", "off"}
INTERVAL_TEXT = re.compile(
    r"^(?:(?P<days>-?\d+) days)?(?: ?(?P<secs>-?\d+)(?:\.(?P<frac>\d{1,6}))? seconds)?$"
)


class ValueProblem(ValueError):
    pass


class JsonValue:
    """A value for a json/jsonb column (the database layer wraps it for the driver)."""

    __slots__ = ("obj",)

    def __init__(self, obj: Any) -> None:
        self.obj = obj

    def __eq__(self, other: object) -> bool:
        return isinstance(other, JsonValue) and other.obj == self.obj

    def __repr__(self) -> str:
        return f"JsonValue({self.obj!r})"


def to_json(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return interval_text(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, dict):
        return {str(k): to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json(v) for v in value]
    return str(value)


def interval_text(value: timedelta) -> str:
    """Exact interval text: days, seconds and microseconds, every unit with the same sign.

    `timedelta(days=30, seconds=1)` gives `"30 days 1 seconds"`, `timedelta(microseconds=-1)`
    gives `"-0.000001 seconds"`; Postgres parses either back to the same interval.
    """
    sign = "-" if value < timedelta(0) else ""
    value = abs(value)
    parts = []
    if value.days:
        parts.append(f"{sign}{value.days} days")
    if value.seconds or value.microseconds or not parts:
        secs = str(value.seconds)
        if value.microseconds:
            secs += f".{value.microseconds:06d}".rstrip("0")
        parts.append(f"{sign}{secs} seconds")
    return " ".join(parts)


def row_to_json(row: dict[str, Any]) -> dict[str, Any]:
    return {k: to_json(v) for k, v in row.items()}


def _kind(udt: str) -> str:
    return udt.lstrip("_") if udt.startswith("_") else udt


def from_json(value: Any, column: Column) -> Any:
    """Convert one JSON value for `column`, or raise ValueProblem with a readable reason."""
    if value is None:
        return None
    if column.data_type == "ARRAY":
        if not isinstance(value, list):
            raise ValueProblem(f"expected a list for {column.udt_name.lstrip('_')}[]")
        element = Column(
            column.name, "", _kind(column.udt_name), True, None, None, None, column.position
        )
        return [from_json(v, element) for v in value]
    return convert(value, _kind(column.udt_name), column.max_length)


def convert(value: Any, udt: str, max_length: int | None = None) -> Any:
    if udt in INT_RANGES:
        if isinstance(value, bool):
            raise ValueProblem("expected an integer, got true/false")
        if isinstance(value, float):
            if not value.is_integer():
                raise ValueProblem(f"expected an integer, got {value!r}")
            value = int(value)
        elif isinstance(value, str):
            try:
                value = int(value.strip())
            except ValueError:
                raise ValueProblem(f"expected an integer, got {value!r}") from None
        elif not isinstance(value, int):
            raise ValueProblem(f"expected an integer, got {type(value).__name__}")
        lo, hi = INT_RANGES[udt]
        if not lo <= value <= hi:
            raise ValueProblem(f"{value} is out of range for {udt}")
        return value
    if udt == "numeric":
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueProblem(f"expected a number, got {type(value).__name__}")
        try:
            d = Decimal(str(value).strip())
        except InvalidOperation:
            raise ValueProblem(f"expected a number, got {value!r}") from None
        if not d.is_finite():
            raise ValueProblem(f"expected a finite number, got {value!r}")
        return d
    if udt in ("float4", "float8"):
        if isinstance(value, bool):
            raise ValueProblem("expected a number, got true/false")
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueProblem(f"expected a number, got {value!r}") from None
    if udt == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.strip().lower() in TRUE | FALSE:
            return value.strip().lower() in TRUE
        raise ValueProblem(f"expected true or false, got {value!r}")
    if udt in TEXT_TYPES:
        if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
            raise ValueProblem(f"expected text, got {type(value).__name__}")
        text = value if isinstance(value, str) else str(value)
        if "\x00" in text:
            raise ValueProblem("text cannot contain a NUL character")
        if max_length is not None and len(text) > max_length:
            raise ValueProblem(f"{len(text)} characters, longer than the maximum {max_length}")
        return text
    if udt == "date":
        if not isinstance(value, str):
            raise ValueProblem(f"expected a date as YYYY-MM-DD, got {type(value).__name__}")
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            raise ValueProblem(f"expected a date as YYYY-MM-DD, got {value!r}") from None
    if udt in ("timestamp", "timestamptz"):
        if not isinstance(value, str):
            raise ValueProblem(f"expected an ISO date-time, got {type(value).__name__}")
        try:
            dt = datetime.fromisoformat(value.strip())
        except ValueError:
            raise ValueProblem(f"expected an ISO date-time, got {value!r}") from None
        if udt == "timestamptz" and dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        if udt == "timestamp" and dt.tzinfo is not None:
            dt = dt.astimezone(UTC).replace(tzinfo=None)
        return dt
    if udt in ("time", "timetz"):
        if not isinstance(value, str):
            raise ValueProblem(f"expected a time as HH:MM[:SS], got {type(value).__name__}")
        try:
            return time.fromisoformat(value.strip())
        except ValueError:
            raise ValueProblem(f"expected a time as HH:MM[:SS], got {value!r}") from None
    if udt == "interval":
        if isinstance(value, bool):
            raise ValueProblem("expected an interval, got true/false")
        if isinstance(value, (int, float)):
            return timedelta(seconds=value)
        if isinstance(value, str):
            exact = INTERVAL_TEXT.match(value.strip())
            if exact and value.strip():  # the form to_json writes: the identical timedelta
                days, secs, frac = exact.group("days", "secs", "frac")
                micro = int((frac or "0").ljust(6, "0"))
                if secs and secs.startswith("-"):
                    micro = -micro
                return timedelta(days=int(days or 0), seconds=int(secs or 0), microseconds=micro)
            return value  # any other interval text: Postgres parses it
        raise ValueProblem(f"expected an interval, got {type(value).__name__}")
    if udt == "uuid":
        try:
            return uuid.UUID(str(value))
        except ValueError:
            raise ValueProblem(f"expected a UUID, got {value!r}") from None
    if udt == "bytea":
        if not isinstance(value, str):
            raise ValueProblem(f"expected base64 text, got {type(value).__name__}")
        try:
            return base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            raise ValueProblem("expected base64 text") from None
    if udt in ("json", "jsonb"):
        return JsonValue(value)
    # Anything else (inet, enums, money, ranges ...): text, left for Postgres to parse.
    if isinstance(value, (dict, list)):
        raise ValueProblem(f"expected a scalar value for {udt}")
    return str(value) if not isinstance(value, str) else value
