"""Masked real rows into a demo app's lab: map real names onto the demo schema, then load.

The real database's names (`dbo.QC_REVIEWS`, `ReviewNo`) rarely match the demo schema's
(`qc_reviews`, `review_no`). Tables and columns match by name, ignoring case, a schema prefix
and underscores; a mapping file names the rest:

    tables:  {QC_REVIEW_HDR: qc_reviews}
    columns: {QC_REVIEW_HDR: {RVW_NBR: review_no}}
    skip:    [AUDIT_ARCHIVE]

The lab agent then checks every value against the demo schema (types, NOT NULL, keys) and loads
all or nothing. Only rows masked by `labctl realdata mask` (or `extract --manifest`) are taken:
masking writes a receipt beside its output, and `load` needs it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from labctl.source.sqlalchemy_models import Table

RECEIPT_SUFFIX = ".mask-receipt.json"


class LoadError(Exception):
    pass


def _key(name: str) -> str:
    return name.rsplit(".", 1)[-1].strip('[]"`').replace("_", "").lower()


@dataclass
class Mapped:
    rows: dict[str, list[dict]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def map_rows(rows: dict, tables: list[Table], mapping: dict | None = None) -> Mapped:
    mapping = mapping or {}
    by_key = {_key(t.name): t for t in tables}
    by_name = {t.name: t for t in tables}
    table_map = {str(k): str(v) for k, v in (mapping.get("tables") or {}).items()}
    col_map = {str(k): dict(v or {}) for k, v in (mapping.get("columns") or {}).items()}
    skip = {_key(str(s)) for s in mapping.get("skip") or []}
    out = Mapped()
    for real, items in rows.items():
        if not isinstance(items, list):
            out.errors.append(f"{real}: not a list of rows")
            continue
        if _key(real) in skip:
            out.notes.append(f"{real}: skipped ({len(items)} rows), as the mapping says")
            continue
        target = by_name.get(table_map[real]) if real in table_map else by_key.get(_key(real))
        if target is None:
            out.errors.append(
                f"{real}: no table of that name in the demo schema; map it under `tables:` "
                "or list it under `skip:`"
            )
            continue
        cols = {_key(c.name): c.name for c in target.columns}
        names = {c.name for c in target.columns}
        rename = col_map.get(real, {})
        dropped: set[str] = set()
        mapped_rows = []
        for row in items:
            new = {}
            for col, value in row.items():
                demo = rename.get(col) if col in rename else cols.get(_key(col))
                if demo is None or demo not in names:
                    dropped.add(col)
                    continue
                new[demo] = value
            mapped_rows.append(new)
        if dropped:
            out.notes.append(
                f"{real} -> {target.name}: column(s) not in the demo schema left out: "
                + ", ".join(sorted(dropped))
            )
        out.rows.setdefault(target.name, []).extend(mapped_rows)
    return out


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def write_receipt(out: Path, manifest: Path) -> Path:
    """Beside masked output: what masked it, so `load` can tell masked rows from raw ones."""
    receipt = out.with_name(out.name + RECEIPT_SUFFIX)
    receipt.write_text(
        json.dumps({
            "file": out.name, "rows_digest": digest(out), "manifest": str(manifest),
            "manifest_digest": digest(manifest), "masked_at": datetime.now(UTC).isoformat(),
        }, indent=1),
        encoding="utf-8",
    )  # fmt: skip
    return receipt


def check_receipt(path: Path) -> dict:
    receipt = path.with_name(path.name + RECEIPT_SUFFIX)
    if not receipt.exists():
        raise LoadError(
            f"{path.name} has no mask receipt ({receipt.name}): only rows written by "
            "`labctl realdata mask` or `extract --manifest` are loaded. For synthetic rows use "
            "`--not-real-data`."
        )
    data = json.loads(receipt.read_text(encoding="utf-8"))
    if data.get("rows_digest") != digest(path):
        raise LoadError(f"{path.name} changed after it was masked; mask it again")
    return data
