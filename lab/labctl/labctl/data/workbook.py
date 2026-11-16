"""Excel data packs: a template workbook per schema, and reading a filled one back with checks.

Layout of every table sheet: row 1 holds the column names exactly as in the database, row 2 a
note per column (type, required or optional, keys), data from row 3. A first sheet, README,
explains how to fill it. Reading reports every problem by sheet, Excel row and column.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import math
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException

from labctl.data import Rows
from labctl.source.sqlalchemy_models import Column, Table, ordered

__all__ = ["Problem", "template", "read", "sheet_names", "column_note", "convert"]

README = "README"
HEADER_ROW, NOTES_ROW, FIRST_DATA_ROW = 1, 2, 3
INT_RANGE = {
    "SMALLINT": (-(2**15), 2**15 - 1),
    "INTEGER": (-(2**31), 2**31 - 1),
    "BIGINT": (-(2**63), 2**63 - 1),
}
TRUE_WORDS = {"yes", "y", "true", "t", "1", "on"}
FALSE_WORDS = {"no", "n", "false", "f", "0", "off"}
_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_NOTES_FILL = PatternFill("solid", fgColor="EDEDED")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_NOTES_FONT = Font(italic=True, color="595959", size=9)
_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


@dataclass
class Problem:
    sheet: str  # "" for a problem with the whole file
    row: int | None  # Excel row number, 1 = header row
    column: str | None  # "C (email)": letter and name, or the name alone when it is missing
    message: str

    def __str__(self) -> str:
        where = [f"sheet '{self.sheet}'"] if self.sheet else []
        if self.row is not None:
            where.append(f"row {self.row}")
        if self.column:
            where.append(f"column {self.column}")
        if not where:
            return self.message
        text = ", ".join(where)
        return f"{text[0].upper()}{text[1:]}: {self.message}"


# --- names and notes ---------------------------------------------------------------------------


def sheet_names(tables: list[Table]) -> dict[str, str]:
    """Table name -> sheet title. Excel allows 31 characters and no []:*?/\\ in a title."""
    out: dict[str, str] = {}
    used: set[str] = {README.lower()}
    for table in tables:
        title = _BAD_SHEET_CHARS.sub("_", table.name)[:31] or "table"
        n = 2
        while title.lower() in used:
            suffix = f"~{n}"
            title = _BAD_SHEET_CHARS.sub("_", table.name)[: 31 - len(suffix)] + suffix
            n += 1
        used.add(title.lower())
        out[table.name] = title
    return out


_FORMAT_HINT = {
    "DATE": "YYYY-MM-DD",
    "TIMESTAMP": "YYYY-MM-DD HH:MM:SS",
    "TIMESTAMP WITH TIME ZONE": "YYYY-MM-DD HH:MM:SS, UTC",
    "TIME": "HH:MM:SS",
    "BOOLEAN": "yes or no",
    "BLOB": "base64 text",
    "JSON": "JSON text",
    "UUID": "like 123e4567-e89b-42d3-a456-426614174000",
}


def required(col: Column) -> bool:
    return not col.nullable and not col.autoincrement


def column_note(col: Column) -> str:
    """The text in row 2 under a column's name."""
    kind = col.type
    hint = _FORMAT_HINT.get(kind)
    if kind.startswith("VARCHAR("):
        hint = f"text, at most {_length(kind)} characters"
    elif kind in ("TEXT", "VARCHAR"):
        hint = "text"
    elif kind in INT_RANGE:
        hint = "whole number"
    elif kind.startswith("NUMERIC"):
        scale = _numeric(kind)[1]
        hint = f"number, {scale} decimal places" if scale is not None else "number"
    elif kind == "DOUBLE PRECISION":
        hint = "number"
    parts = [f"{kind} ({hint})" if hint else kind]
    if col.autoincrement:
        parts.append("optional: numbered automatically when left empty")
    else:
        parts.append("optional" if col.nullable else "required")
    if col.primary_key:
        parts.append("primary key")
    if col.unique:
        parts.append("unique")
    if col.references:
        parts.append(f"refers to {col.references[0]}.{col.references[1]}")
    return "; ".join(parts)


def _length(kind: str) -> int | None:
    m = re.fullmatch(r"VARCHAR\((\d+)\)", kind)
    return int(m.group(1)) if m else None


def _numeric(kind: str) -> tuple[int | None, int | None]:
    m = re.fullmatch(r"NUMERIC\((\d+),(\d+)\)", kind)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


# --- template ----------------------------------------------------------------------------------


def template(tables: list[Table], rows: Rows | None = None) -> bytes:
    """An .xlsx workbook to fill in: README, then a sheet per table, parents before children.

    `rows` (optional) are written below the notes row, e.g. the app's current data.
    """
    tables = ordered(tables)
    names = sheet_names(tables)
    wb = Workbook()
    readme = wb.active
    readme.title = README
    _write_readme(readme, tables, names)
    for table in tables:
        ws = wb.create_sheet(names[table.name])
        for i, col in enumerate(table.columns, start=1):
            head = ws.cell(row=HEADER_ROW, column=i, value=col.name)
            head.font, head.fill = _HEADER_FONT, _HEADER_FILL
            _as_text(head)
            note = ws.cell(row=NOTES_ROW, column=i, value=column_note(col))
            note.font, note.fill = _NOTES_FONT, _NOTES_FILL
            note.alignment = Alignment(wrap_text=True, vertical="top")
            width = max(len(col.name) + 2, 14, min(len(column_note(col)) // 2, 40))
            ws.column_dimensions[get_column_letter(i)].width = width
        ws.row_dimensions[NOTES_ROW].height = 45
        ws.freeze_panes = ws.cell(row=FIRST_DATA_ROW, column=1)
        for r, row in enumerate((rows or {}).get(table.name, []), start=FIRST_DATA_ROW):
            for i, col in enumerate(table.columns, start=1):
                _write_value(ws.cell(row=r, column=i), col, row.get(col.name))
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _write_readme(ws, tables: list[Table], names: dict[str, str]) -> None:
    lines = [
        ("How to fill this workbook", True),
        ("", False),
        ("Each sheet after this one is a table of the demo app, in the order to fill them: a "
         "table comes after the tables it refers to.", False),
        ("Row 1 holds the column names. Do not rename, reorder or delete them.", False),
        ("Row 2 (grey) describes each column: its type, whether it is required, and keys. It "
         "is not data and is ignored when the workbook is read.", False),
        ("Enter data from row 3 down, one record per row. An empty cell means no value.", False),
        ("Dates as YYYY-MM-DD, date and time as YYYY-MM-DD HH:MM:SS (times with a time zone "
         "are read as UTC). Yes/no columns take yes, no, true, false, 1 or 0.", False),
        ("'refers to table.column' means the value must exist in that column of the other "
         "sheet, or in the app's current data when that sheet is left out.", False),
        ("Columns numbered automatically may be left empty, but fill them when other rows "
         "refer to them.", False),
        ("Leave a sheet empty, or delete it, to keep that table as it is. Loading replaces the "
         "rows of each table that has rows here; nothing is loaded if any problem is found.",
         False),
        ("Use made-up (synthetic) data only. Never paste real customer records here.", True),
        ("", False),
        ("Sheet", True),
    ]  # fmt: skip
    for r, (text, bold) in enumerate(lines, start=1):
        cell = ws.cell(row=r, column=1, value=text)
        cell.font = Font(bold=bold, size=13 if r == 1 else 11)
    start = len(lines)
    ws.cell(row=start, column=2, value="Table").font = Font(bold=True)
    ws.cell(row=start, column=3, value="Columns").font = Font(bold=True)
    ws.cell(row=start, column=4, value="Refers to").font = Font(bold=True)
    for r, table in enumerate(tables, start=start + 1):
        ws.cell(row=r, column=1, value=names[table.name])
        ws.cell(row=r, column=2, value=table.name)
        ws.cell(row=r, column=3, value=len(table.columns))
        ws.cell(row=r, column=4, value=", ".join(sorted(set(table.parents))) or "-")
    ws.column_dimensions["A"].width = 32
    for letter in "BCD":
        ws.column_dimensions[letter].width = 20


def _as_text(cell) -> None:
    """A string starting with = stays a string, not a formula."""
    if isinstance(cell.value, str):
        cell.data_type = "s"


def _write_value(cell, col: Column, value: Any) -> None:
    if value is None:
        return
    kind = col.type
    out: Any = value
    fmt = None
    try:
        if kind == "DATE" and isinstance(value, str):
            out, fmt = date.fromisoformat(value[:10]), "yyyy-mm-dd"
        elif kind in ("TIMESTAMP", "TIMESTAMP WITH TIME ZONE") and isinstance(value, str):
            moment = datetime.fromisoformat(value)
            if moment.tzinfo is not None:
                moment = moment.astimezone(UTC).replace(tzinfo=None)
            if moment.microsecond % 1000 == 0:  # Excel keeps milliseconds only
                out, fmt = moment, "yyyy-mm-dd hh:mm:ss"
            else:
                out = moment.isoformat(sep=" ")
        elif kind == "TIME" and isinstance(value, str):
            out, fmt = time.fromisoformat(value), "hh:mm:ss"
        elif kind.startswith("NUMERIC") and isinstance(value, str | int | float):
            number = Decimal(str(value))
            if len(number.as_tuple().digits) <= 15:
                out = number
                scale = _numeric(kind)[1]
                fmt = "0." + "0" * scale if scale else "0"
            else:
                out = str(value)  # more digits than Excel keeps
        elif kind == "JSON":
            out = json.dumps(value, ensure_ascii=False)
    except (ValueError, InvalidOperation, TypeError):
        out = value if isinstance(value, str | int | float | bool) else str(value)
    if isinstance(out, dict | list):
        out = json.dumps(out, ensure_ascii=False)
    if isinstance(out, str):
        out = ILLEGAL_CHARACTERS_RE.sub("", out)
    cell.value = out
    if isinstance(out, str):
        _as_text(cell)
        cell.number_format = "@"
    elif fmt:
        cell.number_format = fmt


# --- converting one value ----------------------------------------------------------------------


class _Bad(Exception):
    pass


def _shown(value: Any) -> str:
    if isinstance(value, datetime | date | time):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    text = str(value)
    return f"'{text[:40]}...'" if len(text) > 40 else f"'{text}'"


def convert(col: Column, value: Any, from_excel: bool = True) -> Any:
    """One cell (or JSON value) -> its JSON-safe value for this column. Raises ValueError."""
    try:
        return _convert(col, value, from_excel)
    except _Bad as exc:
        raise ValueError(str(exc)) from None


def _convert(col: Column, value: Any, from_excel: bool) -> Any:
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
    if value is None:
        return None
    kind = col.type
    if kind in INT_RANGE:
        return _integer(kind, value)
    if kind.startswith("NUMERIC"):
        return _decimal(kind, value, from_excel)
    if kind == "DOUBLE PRECISION":
        if isinstance(value, bool) or isinstance(value, datetime | date | time):
            raise _Bad(f"{_shown(value)} is not a number")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise _Bad(f"{_shown(value)} is not a number") from None
        if not math.isfinite(number):
            raise _Bad(f"{_shown(value)} is not a number")
        return number
    if kind == "BOOLEAN":
        return _boolean(value)
    if kind == "DATE":
        return _date(value)
    if kind in ("TIMESTAMP", "TIMESTAMP WITH TIME ZONE"):
        return _datetime(value, kind == "TIMESTAMP WITH TIME ZONE")
    if kind == "TIME":
        return _time(value)
    if kind == "UUID":
        try:
            return str(uuid.UUID(str(value)))
        except ValueError:
            raise _Bad(f"{_shown(value)} is not a UUID") from None
    if kind == "JSON":
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except ValueError:
            raise _Bad(f"{_shown(value)} is not valid JSON") from None
    if kind == "BLOB":
        text = str(value)
        try:
            base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            raise _Bad(f"{_shown(value)} is not base64 text") from None
        return text
    return _text(kind, value)


def _integer(kind: str, value: Any) -> int:
    if isinstance(value, bool) or isinstance(value, datetime | date | time):
        raise _Bad(f"{_shown(value)} is not a whole number")
    if isinstance(value, float):
        if not value.is_integer():
            raise _Bad(f"{_shown(value)} is not a whole number")
        number = int(value)
    elif isinstance(value, int):
        number = value
    else:
        text = str(value).strip()
        if not re.fullmatch(r"[+-]?\d+(\.0*)?", text):
            raise _Bad(f"{_shown(value)} is not a whole number")
        number = int(text.split(".")[0])
    low, high = INT_RANGE[kind]
    if not low <= number <= high:
        raise _Bad(f"{number} is too large for {kind} (from {low} to {high})")
    return number


def _decimal(kind: str, value: Any, from_excel: bool) -> str:
    if isinstance(value, bool) or isinstance(value, datetime | date | time):
        raise _Bad(f"{_shown(value)} is not a number")
    try:
        number = Decimal(repr(value) if isinstance(value, float) else str(value).strip())
    except InvalidOperation:
        raise _Bad(f"{_shown(value)} is not a number") from None
    if not number.is_finite():
        raise _Bad(f"{_shown(value)} is not a number")
    precision, scale = _numeric(kind)
    if scale is None:
        return format(number, "f")
    exponent = number.normalize().as_tuple().exponent
    float_noise = from_excel and isinstance(value, float)  # Excel arithmetic, e.g. 0.1 + 0.2
    if isinstance(exponent, int) and -exponent > scale and not float_noise:
        raise _Bad(f"{_shown(value)} has more than {scale} decimal places")
    try:
        number = number.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_EVEN)
    except InvalidOperation:
        raise _Bad(f"{_shown(value)} is too large for {kind}") from None
    whole = abs(int(number))
    if precision is not None and whole and len(str(whole)) > precision - scale:
        raise _Bad(
            f"{_shown(value)} is too large for {kind}: at most {precision - scale} digits "
            "before the decimal point"
        )
    return format(number, "f")


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    raise _Bad(f"{_shown(value)} is not yes or no (use yes, no, true, false, 1 or 0)")


def _date(value: Any) -> str:
    if isinstance(value, datetime):
        if value.time() != time(0):
            raise _Bad(f"{_shown(value)} has a time of day; this column holds dates only")
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            raise _Bad(f"{_shown(value)} is not a date (use YYYY-MM-DD)") from None
        return _date(moment.replace(tzinfo=None))
    raise _Bad(f"{_shown(value)} is not a date (use YYYY-MM-DD)")


def _datetime(value: Any, with_zone: bool) -> str:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            raise _Bad(
                f"{_shown(value)} is not a date and time (use YYYY-MM-DD HH:MM:SS)"
            ) from None
    else:
        raise _Bad(f"{_shown(value)} is not a date and time (use YYYY-MM-DD HH:MM:SS)")
    if with_zone:
        moment = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    elif moment.tzinfo is not None:
        raise _Bad(f"{_shown(value)} has a time zone; this column holds local times without one")
    return moment.isoformat()


def _time(value: Any) -> str:
    if isinstance(value, time):
        return value.replace(tzinfo=None).isoformat()
    if isinstance(value, datetime):
        return value.time().isoformat()
    if isinstance(value, str):
        try:
            return time.fromisoformat(value).replace(tzinfo=None).isoformat()
        except ValueError:
            pass
    raise _Bad(f"{_shown(value)} is not a time (use HH:MM:SS)")


def _text(kind: str, value: Any) -> str:
    if isinstance(value, bool):
        text = "TRUE" if value else "FALSE"
    elif isinstance(value, float) and value.is_integer():
        text = str(int(value))
    elif isinstance(value, datetime):
        text = value.isoformat(sep=" ")
    elif isinstance(value, date | time):
        text = value.isoformat()
    else:
        text = str(value).strip()
    limit = _length(kind)
    if limit is not None and len(text) > limit:
        raise _Bad(f"is {len(text)} characters long; at most {limit} allowed")
    return text


# --- reading -----------------------------------------------------------------------------------


@dataclass
class _Sheet:
    table: Table
    title: str
    rows: list[dict[str, Any]]
    excel_rows: list[int]  # Excel row number of each row
    letters: dict[str, str]  # column name -> letter, for columns present


def _where(sheet: _Sheet, col: str) -> str:
    letter = sheet.letters.get(col)
    return f"{letter} ({col})" if letter else col


def read(
    data: bytes, tables: list[Table], existing: Rows | None = None
) -> tuple[Rows, list[Problem]]:
    """Rows from a filled workbook, and every problem found. Load only when there are none.

    Sheets that are absent give no rows (a partial pack). `existing` is the app's current data,
    used to check foreign keys into tables the workbook leaves out.
    """
    problems: list[Problem] = []
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (zipfile.BadZipFile, InvalidFileException, KeyError, OSError, ValueError) as exc:
        return {}, [Problem("", None, None, f"the file is not an Excel workbook (.xlsx): {exc}")]
    tables = ordered(tables)
    names = sheet_names(tables)
    by_title = {names[t.name].lower(): t for t in tables}
    by_name = {t.name.lower(): t for t in tables}
    sheets: dict[str, _Sheet] = {}
    try:
        for ws in wb.worksheets:
            title = ws.title
            if title.strip().lower() == README.lower():
                continue
            table = by_title.get(title.lower()) or by_name.get(title.strip().lower())
            if table is None:
                problems.append(
                    Problem(
                        title,
                        None,
                        None,
                        "there is no table with this name in the schema; the sheet was not read",
                    )  # fmt: skip
                )
                continue
            if table.name in sheets:
                problems.append(Problem(title, None, None, f"a second sheet for table "
                                        f"{table.name}; only '{sheets[table.name].title}' was read"))  # fmt: skip
                continue
            found: list[Problem] = []
            sheet = _read_sheet(ws, table, found)
            if sheet.rows or any(p.row and p.row > HEADER_ROW for p in found):
                sheets[table.name] = sheet  # a sheet without rows counts as left out
                problems.extend(found)
    finally:
        wb.close()
    _check_keys(sheets, problems)
    _check_references(sheets, tables, existing, problems)
    rows: Rows = {t.name: sheets[t.name].rows for t in tables if t.name in sheets}
    return rows, problems


def _read_sheet(ws, table: Table, problems: list[Problem]) -> _Sheet:
    title = ws.title
    ws.reset_dimensions()  # some writers record a wrong used range
    raw = list(ws.iter_rows(values_only=True))
    sheet = _Sheet(table, title, [], [], {})
    if not raw or not any(v not in (None, "") for v in raw[0]):
        problems.append(Problem(title, HEADER_ROW, None, "row 1 must hold the column names"))
        return sheet
    columns = {c.name: c for c in table.columns}
    lower = {c.name.lower(): c for c in table.columns}
    positions: dict[int, Column] = {}
    for i, head in enumerate(raw[0]):
        letter = get_column_letter(i + 1)
        if head is None or str(head).strip() == "":
            continue
        name = str(head).strip()
        col = columns.get(name) or lower.get(name.lower())
        if col is None:
            problems.append(Problem(title, HEADER_ROW, f"{letter} ({name})",
                                    f"there is no column {name} in table {table.name}; the "
                                    "column was not read"))  # fmt: skip
            continue
        if col.name in sheet.letters:
            problems.append(Problem(title, HEADER_ROW, f"{letter} ({name})",
                                    f"column {col.name} appears twice; only column "
                                    f"{sheet.letters[col.name]} was read"))  # fmt: skip
            continue
        positions[i] = col
        sheet.letters[col.name] = letter
    for col in table.columns:
        if col.name not in sheet.letters and required(col):
            problems.append(Problem(title, HEADER_ROW, col.name,
                                    "required column is missing from row 1"))  # fmt: skip
    start = 1
    if len(raw) > 1 and _is_notes(raw[1], positions):
        start = 2
    unnamed_reported: set[int] = set()
    for offset, values in enumerate(raw[start:], start=start + 1):
        if not any(v is not None and (not isinstance(v, str) or v.strip()) for v in values):
            continue
        row: dict[str, Any] = {}
        for i, value in enumerate(values):
            col = positions.get(i)
            if col is None:
                empty = value is None or (isinstance(value, str) and not value.strip())
                header = raw[0][i] if i < len(raw[0]) else None
                if (
                    not empty
                    and (header is None or str(header).strip() == "")
                    and (i not in unnamed_reported)
                ):
                    unnamed_reported.add(i)
                    problems.append(Problem(title, offset, get_column_letter(i + 1),
                                            "has a value but the column has no name in row 1; "
                                            "the column was not read"))  # fmt: skip
                continue
            try:
                row[col.name] = convert(col, value)
            except ValueError as exc:
                problems.append(Problem(title, offset, _where(sheet, col.name), str(exc)))
                row[col.name] = _INVALID
        out: dict[str, Any] = {}
        for col in table.columns:
            value = row.get(col.name)
            if value is None and col.name in sheet.letters and required(col):
                problems.append(Problem(title, offset, _where(sheet, col.name),
                                        "is required but empty"))  # fmt: skip
            if value is None and col.autoincrement:
                continue  # left to the database to number
            out[col.name] = value
        sheet.rows.append(out)
        sheet.excel_rows.append(offset)
    return sheet


class _Invalid:
    """Placeholder for a cell that failed to convert; replaced by None before returning."""

    def __repr__(self) -> str:
        return "invalid"


_INVALID = _Invalid()


def _is_notes(values: tuple, positions: dict[int, Column]) -> bool:
    texts = [(i, v) for i, v in enumerate(values) if v is not None and str(v).strip()]
    if not texts:
        return False
    same = sum(
        1 for i, v in texts if i in positions and str(v).strip() == column_note(positions[i])
    )
    return same > 0 and same * 2 >= len(texts)


def _key(value: Any) -> Any:
    return json.dumps(value, sort_keys=True) if isinstance(value, dict | list) else value


def _check_keys(sheets: dict[str, _Sheet], problems: list[Problem]) -> None:
    for sheet in sheets.values():
        table = sheet.table
        pk = [c for c in table.columns if c.primary_key]
        seen: dict[Any, int] = {}
        uniques = {c.name: {} for c in table.columns if c.unique}
        for row, excel_row in zip(sheet.rows, sheet.excel_rows, strict=True):
            values = tuple(row.get(c.name) for c in pk)
            if pk and all(v is not None and v is not _INVALID for v in values):
                key = tuple(_key(v) for v in values)
                if key in seen:
                    shown = ", ".join(f"{c.name}={v}" for c, v in zip(pk, values, strict=True))
                    where = _where(sheet, pk[0].name) if len(pk) == 1 else None
                    problems.append(Problem(sheet.title, excel_row, where,
                                            f"duplicate primary key {shown}: already used in "
                                            f"row {seen[key]}"))  # fmt: skip
                else:
                    seen[key] = excel_row
            for name, used in uniques.items():
                value = row.get(name)
                if value is None or value is _INVALID:
                    continue
                key = _key(value)
                if key in used:
                    problems.append(Problem(sheet.title, excel_row, _where(sheet, name),
                                            f"duplicate value {_shown(value)}: must be unique, "
                                            f"already used in row {used[key]}"))  # fmt: skip
                else:
                    used[key] = excel_row


def _check_references(
    sheets: dict[str, _Sheet], tables: list[Table], existing: Rows | None, problems: list[Problem]
) -> None:
    by_name = {t.name: t for t in tables}
    cache: dict[tuple[str, str], set | None] = {}

    def targets(parent: Table, column: str) -> set | None:
        key = (parent.name, column)
        if key not in cache:
            target_col = next((c for c in parent.columns if c.name == column), None)
            if target_col is None:
                cache[key] = None
            elif parent.name in sheets:
                cache[key] = {
                    _key(r.get(column))
                    for r in sheets[parent.name].rows
                    if r.get(column) is not None and r.get(column) is not _INVALID
                }
            elif existing is not None and parent.name in existing:
                found = set()
                for r in existing[parent.name]:
                    try:
                        value = convert(target_col, r.get(column), from_excel=False)
                    except ValueError:
                        continue
                    if value is not None:
                        found.add(_key(value))
                cache[key] = found
            else:
                cache[key] = None
        return cache[key]

    for sheet in sheets.values():
        for col in sheet.table.columns:
            if not col.references or col.references[0] not in by_name:
                continue
            parent = by_name[col.references[0]]
            pcol = col.references[1]
            if not any(c.name == pcol for c in parent.columns):
                continue
            allowed = targets(parent, pcol)
            pairs = [
                (r.get(col.name), n)
                for r, n in zip(sheet.rows, sheet.excel_rows, strict=True)
                if r.get(col.name) is not None and r.get(col.name) is not _INVALID
            ]
            if not pairs:
                continue
            if allowed is None:
                problems.append(Problem(sheet.title, pairs[0][1], _where(sheet, col.name),
                                        f"refers to {parent.name}.{pcol}, but the workbook has "
                                        f"no {parent.name} sheet and there is no current "
                                        f"{parent.name} data to check against ({len(pairs)} "
                                        "row(s) affected)"))  # fmt: skip
                continue
            where = "sheet " + sheet_names(tables)[parent.name]
            if parent.name not in sheets:
                where = f"the current {parent.name} data"
            for value, excel_row in pairs:
                if _key(value) not in allowed:
                    problems.append(Problem(sheet.title, excel_row, _where(sheet, col.name),
                                            f"{_shown(value)} not found in {parent.name}.{pcol} "
                                            f"({where})"))  # fmt: skip
    for sheet in sheets.values():
        for row in sheet.rows:
            for name, value in row.items():
                if value is _INVALID:
                    row[name] = None
