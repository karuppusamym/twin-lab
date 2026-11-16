"""Keyed deterministic masking (Prod Echo I-17, ADR-003, ADR-009).

`mask(rows, manifest, key)`: the same key and input give the same output in every app and every
run, with no mapping store. Each technique derives its own key, HMAC-SHA256(key, label). No
tweak depends on the table or column, so the same person masks to the same value everywhere.

The key comes from the environment variable the manifest names, never from a file.
"""

from __future__ import annotations

import base64
import copy
import datetime as dt
import hashlib
import hmac
import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Literal

import yaml
from pydantic import Field, field_validator, model_validator

from labctl.models import Contract
from labctl.realdata import inventory
from labctl.realdata.dictionaries import DICTIONARIES, FULL_NAMES, WITH_NUMBER
from labctl.realdata.ff1 import FF1
from labctl.source.sqlalchemy_models import Table

Rows = dict[str, list[dict[str, Any]]]
Technique = Literal[
    "dict_hmac",
    "ff1_digits",
    "ff1_ssn_valid",
    "luhn",
    "entity_shift",
    "ff1_keep_area",
    "hmac_local_part",
    "keep",
]
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_KEY_ENV = "TWINLAB_MASK_KEY"
MIN_KEY_BYTES = 16
MAX_TRIES = 1000


class MaskError(Exception):
    pass


# -- the manifest (Prod Echo docs/04) ------------------------------------------------------------


class ColumnRule(Contract):
    class_: str = Field(alias="class", description="ssn, first_name, date, ... or none.")
    technique: Technique
    dictionary: str | None = Field(None, alias="dict", description="Dictionary for dict_hmac.")
    entity: str | None = Field(None, description="entity_shift: entity name, e.g. customer.")
    entity_column: str | None = Field(
        None, description="entity_shift: column of this row holding the entity key."
    )

    @model_validator(mode="after")
    def _complete(self) -> ColumnRule:
        if self.technique == "dict_hmac" and self.dictionary not in (*DICTIONARIES, FULL_NAMES):
            known = ", ".join([*DICTIONARIES, FULL_NAMES])
            raise ValueError(f"dict_hmac needs `dict`, one of: {known}")
        if self.technique == "entity_shift" and not self.entity_column:
            raise ValueError("entity_shift needs `entity_column` (and usually `entity`)")
        return self


class Review(Contract):
    approved_by: str | None = None
    date: dt.date | None = None
    unclassified_policy: Literal["block", "keep"] = "block"


class MaskManifest(Contract):
    app: str | None = None
    key_env: str = DEFAULT_KEY_ENV
    columns: dict[str, ColumnRule] = Field(default_factory=dict)
    review: Review = Field(default_factory=Review)

    @field_validator("key_env")
    @classmethod
    def _env_name(cls, value: str) -> str:
        if not ENV_NAME.match(value):
            raise ValueError(
                "`key_env` names an environment variable; the key never goes in a file"
            )
        return value

    @field_validator("columns")
    @classmethod
    def _dotted(cls, value: dict[str, ColumnRule]) -> dict[str, ColumnRule]:
        bad = [k for k in value if k.count(".") < 1]
        if bad:
            raise ValueError(f"column keys are TABLE.COLUMN: {', '.join(bad)}")
        return value

    def rule(self, table: str, column: str) -> ColumnRule | None:
        found = self.columns.get(f"{table}.{column}")
        if found is None:
            want = f"{table}.{column}".lower()
            found = next((r for k, r in self.columns.items() if k.lower() == want), None)
        return found


def is_approved(manifest: MaskManifest) -> bool:
    who = (manifest.review.approved_by or "").strip()
    return bool(who) and not who.startswith("<")


def key_from_env(manifest: MaskManifest) -> bytes:
    """The masking key from the manifest's environment variable (`hex:` prefix for hex)."""
    raw = os.environ.get(manifest.key_env)
    if not raw:
        raise MaskError(f"environment variable {manifest.key_env} is not set")
    key = bytes.fromhex(raw[4:]) if raw.startswith("hex:") else raw.encode("utf-8")
    if len(key) < MIN_KEY_BYTES:
        raise MaskError(f"the key in {manifest.key_env} is shorter than {MIN_KEY_BYTES} bytes")
    return key


def load_manifest(text: str) -> MaskManifest:
    return MaskManifest.model_validate(yaml.safe_load(text))


def manifest_yaml(manifest: MaskManifest) -> str:
    columns = {
        k: r.model_dump(by_alias=True, exclude_none=True) for k, r in manifest.columns.items()
    }
    data = {
        "app": manifest.app,
        "key_env": manifest.key_env,
        "columns": columns,
        "review": manifest.review.model_dump(mode="json"),
    }
    if data["app"] is None:
        del data["app"]
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=None, width=100)


# -- validity checks -----------------------------------------------------------------------------


def ssn_valid(digits: str) -> bool:
    if len(digits) != 9 or not digits.isdigit():
        return False
    area, group, serial = int(digits[:3]), digits[3:5], digits[5:]
    return area != 0 and area != 666 and area < 900 and group != "00" and serial != "0000"


def luhn_check_digit(body: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return str((10 - total % 10) % 10)


def luhn_valid(digits: str) -> bool:
    return len(digits) >= 2 and digits.isdigit() and luhn_check_digit(digits[:-1]) == digits[-1]


def _digits_of(value: Any) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())


# -- the masker ----------------------------------------------------------------------------------


class Masker:
    """One key, every technique. Pure: no state but cached derived keys."""

    def __init__(self, key: bytes) -> None:
        if len(key) < MIN_KEY_BYTES:
            raise MaskError(f"the masking key must be at least {MIN_KEY_BYTES} bytes")
        self._key = key
        self._sub: dict[str, bytes] = {}
        self._ff1: dict[str, FF1] = {}

    def subkey(self, label: str) -> bytes:
        if label not in self._sub:
            self._sub[label] = hmac.new(self._key, label.encode(), hashlib.sha256).digest()
        return self._sub[label]

    def _h(self, label: str, value: str) -> bytes:
        return hmac.new(self.subkey(label), value.encode("utf-8"), hashlib.sha256).digest()

    def _int(self, label: str, value: str) -> int:
        return int.from_bytes(self._h(label, value), "big")

    def _fpe(self, label: str) -> FF1:
        if label not in self._ff1:
            self._ff1[label] = FF1(self.subkey(label), 10)
        return self._ff1[label]

    # digits ------------------------------------------------------------------------------------

    def _cycle(self, label: str, digits: str, accept=lambda d: True) -> str:
        """FF1 (or, under 6 digits, HMAC) with an incremented tweak until accepted and changed."""
        for attempt in range(MAX_TRIES):
            tweak = b"" if attempt == 0 else attempt.to_bytes(4, "big")
            if len(digits) >= 6:
                out = self._fpe(label).encrypt(digits, tweak)
            else:
                n = self._int(label + ":short", f"{attempt}:{digits}") % 10 ** len(digits)
                out = str(n).zfill(len(digits))
            if out != digits and accept(out):
                return out
        raise MaskError(f"{label}: no acceptable value after {MAX_TRIES} tries")

    @staticmethod
    def _put_digits(template: str, digits: str) -> str:
        it = iter(digits)
        return "".join(next(it) if ch.isdigit() else ch for ch in template)

    def _digit_technique(self, value: Any, label: str, transform) -> Any:
        text = str(value)
        digits = _digits_of(text)
        if not digits:
            raise MaskError(f"{label}: a value has no digits to mask")
        out = self._put_digits(text, transform(digits))
        return int(out) if isinstance(value, int) and not isinstance(value, bool) else out

    def ff1_digits(self, value: Any) -> Any:
        return self._digit_technique(value, "ff1_digits", lambda d: self._cycle("ff1_digits", d))

    def ff1_ssn_valid(self, value: Any) -> Any:
        def transform(d: str) -> str:
            if len(d) != 9:  # still masked; sanitize reports it as not a valid SSN
                return self._cycle("ff1_ssn_valid", d)
            return self._cycle("ff1_ssn_valid", d, ssn_valid)

        if isinstance(value, int) and not isinstance(value, bool):
            return int(transform(str(value).zfill(9)))
        return self._digit_technique(value, "ff1_ssn_valid", transform)

    def luhn(self, value: Any) -> Any:
        def transform(d: str) -> str:
            if len(d) < 2:
                return self._cycle("luhn", d)
            body = self._cycle("luhn", d[:-1])
            return body + luhn_check_digit(body)

        return self._digit_technique(value, "luhn", transform)

    def ff1_keep_area(self, value: Any) -> Any:
        def transform(d: str) -> str:
            if len(d) < 10:
                return self._cycle("ff1_keep_area", d)
            return d[:-7] + self._cycle("ff1_keep_area", d[-7:])

        return self._digit_technique(value, "ff1_keep_area", transform)

    # text --------------------------------------------------------------------------------------

    def hmac_local_part(self, value: Any) -> str:
        local = str(value).rsplit("@", 1)[0].strip().casefold()
        code = base64.b32encode(self._h("hmac_local_part", local)).decode("ascii")
        return f"{code[:12].lower()}@test.invalid"

    def dict_hmac(self, value: Any, dictionary: str) -> str:
        norm = " ".join(str(value).split()).casefold()
        if dictionary == FULL_NAMES:
            first = self._pick("first_names_v1", norm, "full:first")
            last = self._pick("last_names_v1", norm, "full:last")
            return f"{first} {last}"
        word = self._pick(dictionary, norm, f"dict:{dictionary}")
        if dictionary in WITH_NUMBER:
            word = f"{self._int(f'dict:{dictionary}:no', norm) % 9899 + 1} {word}"
        return word

    def _pick(self, dictionary: str, norm: str, label: str) -> str:
        entries = DICTIONARIES[dictionary]
        idx = self._int(label, norm) % len(entries)
        for step in range(len(entries)):
            word = entries[(idx + step) % len(entries)]
            if word.casefold() != norm:
                return word
        raise MaskError(f"dictionary {dictionary} cannot replace {norm!r}")

    # dates -------------------------------------------------------------------------------------

    def shift_days(self, entity: str, entity_key: Any) -> int:
        days = self._int("entity_shift", f"{entity}:{entity_key}") % 731 - 365
        return days or 365  # never zero, so the masked date always differs

    def entity_shift(self, value: Any, entity: str, entity_key: Any) -> Any:
        delta = timedelta(days=self.shift_days(entity, entity_key))
        if isinstance(value, datetime | date):
            return value + delta
        text = str(value).strip()
        try:
            if len(text) == 10:
                return (date.fromisoformat(text) + delta).isoformat()
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise MaskError(f"entity_shift: not an ISO date: {text[:4]}...") from exc
        sep = text[10] if len(text) > 10 and text[10] in " T" else "T"
        return (moment + delta).isoformat(sep=sep)

    # one value ---------------------------------------------------------------------------------

    def value(self, rule: ColumnRule, value: Any, row: dict[str, Any], table: str) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return value
        t = rule.technique
        if t == "keep":
            return value
        if t == "dict_hmac":
            return self.dict_hmac(value, rule.dictionary or "")
        if t == "entity_shift":
            col = rule.entity_column or ""
            if col not in row:
                raise MaskError(f"{table}: entity_column {col} is not in the row")
            return self.entity_shift(value, rule.entity or table, row[col])
        return getattr(self, t)(value)


def _plan(rows: Rows, manifest: MaskManifest) -> dict[str, dict[str, ColumnRule]]:
    plan: dict[str, dict[str, ColumnRule]] = {}
    unclassified = []
    for table, items in rows.items():
        columns = list(dict.fromkeys(c for r in items for c in r))
        plan[table] = {}
        for col in columns:
            rule = manifest.rule(table, col)
            if rule is None:
                unclassified.append(f"{table}.{col}")
            elif rule.technique != "keep":
                plan[table][col] = rule
    if unclassified and manifest.review.unclassified_policy == "block":
        shown = ", ".join(unclassified[:20]) + (" ..." if len(unclassified) > 20 else "")
        raise MaskError(f"unclassified columns (policy block): {shown}")
    return plan


def mask(rows: Rows, manifest: MaskManifest, key: bytes, allow_unreviewed: bool = False) -> Rows:
    """Masked copy of `rows`. Refuses a manifest no data owner approved, unless told otherwise."""
    if not is_approved(manifest) and not allow_unreviewed:
        raise MaskError("the mask manifest is not approved (review.approved_by is empty)")
    masker = Masker(key)
    plan = _plan(rows, manifest)
    out: Rows = {}
    for table, items in rows.items():
        rules = plan[table]
        masked_items = []
        for i, row in enumerate(items):
            new = copy.deepcopy(row)
            for col, rule in rules.items():
                if col in row:
                    try:
                        new[col] = masker.value(rule, row[col], row, table)
                    except MaskError as exc:
                        raise MaskError(f"{table}.{col} row {i}: {exc}") from None
            masked_items.append(new)
        out[table] = masked_items
    return out


# -- sanitize ------------------------------------------------------------------------------------


def _fk_hits(rows: Rows, child: str, cols: list[str], parent: str, pcols: list[str]) -> int:
    keys = {tuple(r.get(c) for c in pcols) for r in rows.get(parent, [])}
    return sum(
        1
        for r in rows.get(child, [])
        if None not in (k := tuple(r.get(c) for c in cols)) and k in keys
    )


def sanitize(
    source_rows: Rows,
    masked_rows: Rows,
    manifest: MaskManifest,
    tables: list[Table],
    key: bytes | None = None,
    sample_rate: float = 0.01,
) -> list[str]:
    """Problems found comparing masked rows with their source. Empty means safe to load.

    Every masked value differs from its source; foreign-key joins hold as often as in the
    source; masked SSNs and Luhn numbers are valid; a sample re-masked gives identical output.
    No regex scan: a masked SSN looks like a real one, so shape proves nothing.
    """
    problems: list[str] = []
    if set(source_rows) != set(masked_rows):
        problems.append(f"tables differ: source {sorted(source_rows)} masked {sorted(masked_rows)}")
    for table, src in source_rows.items():
        dst = masked_rows.get(table, [])
        if len(src) != len(dst):
            problems.append(f"{table}: {len(src)} source rows but {len(dst)} masked rows")
            continue
        columns = list(dict.fromkeys(c for r in src for c in r))
        for col in columns:
            rule = manifest.rule(table, col)
            if rule is None:
                if manifest.review.unclassified_policy == "block":
                    problems.append(f"{table}.{col}: not in the manifest (policy block)")
                continue
            if rule.technique == "keep":
                continue
            pairs = [
                (s.get(col), d.get(col))
                for s, d in zip(src, dst, strict=True)
                if s.get(col) is not None and str(s.get(col)).strip()
            ]
            same = sum(1 for s, d in pairs if s == d or str(s) == str(d))
            if same:
                problems.append(f"{table}.{col}: {same} of {len(pairs)} values left unmasked")
            if rule.technique == "ff1_ssn_valid":
                bad = sum(1 for _, d in pairs if not ssn_valid(_digits_of(d)))
                if bad:
                    problems.append(f"{table}.{col}: {bad} masked values are not valid SSNs")
            if rule.technique == "luhn":
                bad = sum(1 for _, d in pairs if not luhn_valid(_digits_of(d)))
                if bad:
                    problems.append(f"{table}.{col}: {bad} masked values fail the Luhn check")
            if rule.technique == "hmac_local_part":
                bad = sum(1 for _, d in pairs if not str(d).endswith("@test.invalid"))
                if bad:
                    problems.append(f"{table}.{col}: {bad} masked emails are deliverable")
    for t in tables:
        for fk in inventory.foreign_keys(t):
            if t.name not in source_rows or fk.parent not in source_rows:
                continue
            before = _fk_hits(source_rows, t.name, fk.columns, fk.parent, fk.parent_columns)
            after = _fk_hits(masked_rows, t.name, fk.columns, fk.parent, fk.parent_columns)
            if before != after:
                problems.append(
                    f"{t.name}.{','.join(fk.columns)} -> {fk.parent}: {before} rows joined in "
                    f"the source, {after} after masking"
                )
    problems += _remask(source_rows, masked_rows, manifest, key, sample_rate)
    return problems


def _remask(source: Rows, masked: Rows, manifest: MaskManifest, key, rate: float) -> list[str]:
    try:
        key = key if key is not None else key_from_env(manifest)
    except MaskError as exc:
        return [f"re-mask check not run: {exc}"]
    step = max(1, round(1 / rate)) if rate > 0 else 1
    problems = []
    for table, src in source.items():
        dst = masked.get(table, [])
        for i in range(0, min(len(src), len(dst)), step):
            try:
                again = mask({table: [src[i]]}, manifest, key, allow_unreviewed=True)[table][0]
            except MaskError as exc:
                problems.append(f"{table} row {i}: cannot re-mask: {exc}")
                break
            if again != dst[i]:
                problems.append(f"{table} row {i}: re-masking gives a different result")
    return problems


# -- draft manifest ------------------------------------------------------------------------------

GUESSES: list[tuple[str, str, Technique, str | None]] = [
    # (name pattern, class, technique, dictionary); first match wins
    (r"(^|_)(ssn|social_?sec\w*)($|_)|^ssn", "ssn", "ff1_ssn_valid", None),
    (r"(^|_)(tin|tax_?id|ein|itin)($|_)", "tin", "ff1_digits", None),
    (r"birth|(^|_)dob($|_)", "date", "entity_shift", None),
    (r"e_?mail", "email", "hmac_local_part", None),
    (r"phone|mobile|(^|_)(tel|fax|cell)($|_)", "phone", "ff1_keep_area", None),
    (r"first_?(name|nm)|given_?name|(^|_)fname($|_)|forename", "first_name", "dict_hmac",
     "first_names_v1"),
    (r"last_?(name|nm)|surname|family_?name|(^|_)lname($|_)", "last_name", "dict_hmac",
     "last_names_v1"),
    (r"full_?name|cust(omer)?_?name|holder_?name|contact_?name", "full_name", "dict_hmac",
     FULL_NAMES),
    (r"addr|street|line_?1", "address", "dict_hmac", "streets_v1"),
    (r"(^|_)(city|town)($|_)", "city", "dict_hmac", "cities_v1"),
    (r"card|(^|_)pan($|_)", "card", "luhn", None),
    (r"acc(oun)?t|acct|iban", "account", "ff1_digits", None),
]  # fmt: skip
INT_TYPES = ("INT", "SERIAL")


def _is_surrogate(col) -> bool:
    is_int = any(t in col.type.upper() for t in INT_TYPES)
    named_id = col.name.lower() == "id" or col.name.lower().endswith("_id")
    return (col.primary_key and col.autoincrement) or (is_int and named_id)


def guess(table: Table, column) -> ColumnRule:
    """The class a column's name suggests, or `keep`."""
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", column.name).lower()
    if not _is_surrogate(column):
        for pattern, cls, technique, dictionary in GUESSES:
            if re.search(pattern, name):
                rule = {"class": cls, "technique": technique, "dict": dictionary}
                if technique == "entity_shift":
                    pk = [c.name for c in table.columns if c.primary_key]
                    if len(pk) != 1:
                        continue
                    rule.update(entity=table.name, entity_column=pk[0])
                return ColumnRule.model_validate(rule)
    return ColumnRule.model_validate({"class": "none", "technique": "keep"})


def draft_manifest(
    tables: list[Table], app: str | None = None, key_env: str = DEFAULT_KEY_ENV
) -> MaskManifest:
    """A manifest guessed from column names, every column listed, for a data owner to tick.

    Masked columns referenced by a foreign key pass their rule to the referencing column, so
    joins survive. `review.approved_by` is null: `mask()` refuses it until someone signs.
    """
    columns: dict[str, ColumnRule] = {}
    for t in tables:
        for c in t.columns:
            columns[f"{t.name}.{c.name}"] = guess(t, c)
    changed = True
    while changed:
        changed = False
        for t in tables:
            for fk in inventory.foreign_keys(t):
                for col, pcol in zip(fk.columns, fk.parent_columns, strict=True):
                    parent = columns.get(f"{fk.parent}.{pcol}")
                    child_key = f"{t.name}.{col}"
                    if (
                        parent
                        and parent.technique not in ("keep", "entity_shift")
                        and columns.get(child_key, parent).technique == "keep"
                    ):
                        columns[child_key] = parent.model_copy()
                        changed = True
    return MaskManifest(
        app=app,
        key_env=key_env,
        columns=columns,
        review=Review(approved_by=None, date=None, unclassified_policy="block"),
    )
