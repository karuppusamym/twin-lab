"""`labctl data template|validate|generate`: Excel data packs from a schema file."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from labctl.data import Rows, ddl, synthetic, workbook

app = typer.Typer(no_args_is_help=True, help="Data packs: Excel templates, checks, synthetic data.")
MAX_SHOWN = 200


def _tables(schema: Path):
    try:
        text = schema.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        typer.echo(f"cannot read {schema}: {exc}", err=True)
        raise typer.Exit(2) from exc
    tables, problems = ddl.parse_with_problems(text)
    for problem in problems:
        typer.echo(f"{schema.name}: {problem}", err=True)
    if not tables:
        typer.echo(f"{schema}: no CREATE TABLE statements found", err=True)
        raise typer.Exit(2)
    return tables


def _rows(path: Path | None) -> Rows | None:
    """Rows from JSON: `{table: [rows]}`, or the body of a load request `{rows: ..., label}`."""
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        typer.echo(f"cannot read rows from {path}: {exc}", err=True)
        raise typer.Exit(2) from exc
    if (
        isinstance(data, dict)
        and isinstance(data.get("rows"), dict)
        and set(data)
        <= {
            "rows",
            "label",
        }
    ):
        data = data["rows"]
    if not isinstance(data, dict) or not all(isinstance(v, list) for v in data.values()):
        typer.echo(f"{path}: expected an object of table name -> list of rows", err=True)
        raise typer.Exit(2)
    return data


def _counts(pairs: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for pair in pairs:
        name, sep, number = pair.partition("=")
        if not sep or not name.strip() or not number.strip().isdigit():
            typer.echo(f"--count {pair!r}: use table=number, e.g. --count customer=50", err=True)
            raise typer.Exit(2)
        counts[name.strip()] = int(number)
    return counts


@app.command()
def template(
    schema: Path = typer.Argument(..., help="Schema file (DDL), e.g. apps/<app>/db/schema.sql."),
    out: Path = typer.Option(..., "--out", help="Workbook to write (.xlsx)."),
    rows: Path = typer.Option(None, "--rows", help="JSON rows to write below the notes."),
) -> None:
    """Write an Excel workbook to fill in: one sheet per table."""
    tables = _tables(schema)
    out.write_bytes(workbook.template(tables, _rows(rows)))
    typer.echo(f"wrote {out} ({len(tables)} tables)")


@app.command()
def validate(
    schema: Path = typer.Argument(..., help="Schema file (DDL)."),
    book: Path = typer.Argument(..., metavar="WORKBOOK", help="Filled workbook (.xlsx)."),
    existing: Path = typer.Option(
        None, "--existing", help="JSON rows of the app's current data, for foreign keys."
    ),
) -> None:
    """Check a filled workbook. Prints every problem; exit code 1 if there are any."""
    tables = _tables(schema)
    try:
        data = book.read_bytes()
    except OSError as exc:
        typer.echo(f"cannot read {book}: {exc}", err=True)
        raise typer.Exit(2) from exc
    rows, problems = workbook.read(data, tables, _rows(existing))
    if problems:
        for problem in problems[:MAX_SHOWN]:
            typer.echo(str(problem))
        if len(problems) > MAX_SHOWN:
            typer.echo(f"... and {len(problems) - MAX_SHOWN} more")
        typer.echo(f"{len(problems)} problem(s); nothing may be loaded until they are fixed")
        raise typer.Exit(1)
    summary = ", ".join(f"{name} {len(r)}" for name, r in rows.items()) or "no table sheets"
    typer.echo(f"OK: {summary}")


@app.command()
def generate(
    schema: Path = typer.Argument(..., help="Schema file (DDL)."),
    seed: int = typer.Option(..., "--seed", help="Same seed, same rows."),
    count: list[str] = typer.Option(..., "--count", help="table=n; repeat per table."),
    samples: Path = typer.Option(
        None, "--samples", help="JSON rows whose short code lists (status values) are reused."
    ),
    out: Path = typer.Option(..., "--out", help="Write .xlsx (a filled template) or .json rows."),
) -> None:
    """Generate synthetic rows, reproducible from the seed."""
    tables = _tables(schema)
    suffix = out.suffix.lower()
    if suffix not in (".xlsx", ".json"):
        typer.echo("--out must end in .xlsx or .json", err=True)
        raise typer.Exit(2)
    try:
        rows = synthetic.generate(tables, seed, _counts(count), _rows(samples))
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if suffix == ".xlsx":
        out.write_bytes(workbook.template(tables, rows))
    else:
        out.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    typer.echo(f"wrote {out}: " + ", ".join(f"{t} {len(r)}" for t, r in rows.items()))


if __name__ == "__main__":
    app()
