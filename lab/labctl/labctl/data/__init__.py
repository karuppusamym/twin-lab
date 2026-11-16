"""Data packs: read table definitions from DDL, Excel templates and validation, synthetic rows.

Rows are `dict[str, list[dict]]` (table name -> rows) with JSON-safe values: dates and times as
ISO strings, decimals as strings. Everything here works on synthetic data only.
"""

from __future__ import annotations

from typing import Any

Rows = dict[str, list[dict[str, Any]]]
