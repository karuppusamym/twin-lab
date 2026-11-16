"""`labctl realdata ...`: inventory, extract, trace diff, mask, draft a manifest, sanitize.

Database URLs are passed by the NAME of an environment variable, never on the command line,
and never printed (a message shows the URL with its password masked). The masking key comes
from the environment variable the manifest names.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import typer
import yaml
from pydantic import ValidationError

from labctl.realdata import closure, inventory
from labctl.realdata import mask as mask_mod
from labctl.realdata._db import RealDataError

app = typer.Typer(no_args_is_help=True, help="A masked slice of a real database (read-only).")


def _fail(message: str, code: int = 1) -> None:
    typer.echo(message, err=True)
    raise typer.Exit(code)


def _url(env_name: str) -> str:
    if "://" in env_name or ":" in env_name:
        _fail("pass the NAME of an environment variable holding the URL, not the URL", 2)
    url = os.environ.get(env_name)
    if not url:
        _fail(f"environment variable {env_name} is not set", 2)
    return url


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail(f"cannot read {path}: {exc}", 2)


def _write(path: Path | None, text: str) -> None:
    if path is None:
        typer.echo(text)
    else:
        path.write_text(text, encoding="utf-8")


def _spec(path: Path) -> closure.SubsetSpec:
    try:
        return closure.SubsetSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError) as exc:
        _fail(f"cannot read {path}: {exc}", 2)
    except ValidationError as exc:
        _fail(f"{path}: {exc}", 2)


def _manifest(path: Path) -> mask_mod.MaskManifest:
    try:
        return mask_mod.load_manifest(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        _fail(f"cannot read {path}: {exc}", 2)
    except ValidationError as exc:
        _fail(f"{path}: {exc}", 2)


def _tables(url_env: str | None, schema: Path | None):
    if schema is not None:
        return inventory.from_json(_read_json(schema))
    if url_env:
        return inventory.pull(_url(url_env))
    _fail("give --url-env VAR or --schema inventory.json", 2)


def _warn_if_tracked(path: Path) -> None:
    """Unmasked rows must stay out of Git: warn when the file is not ignored."""
    try:
        res = subprocess.run(
            ["git", "check-ignore", "-q", str(path)],
            cwd=path.resolve().parent,
            capture_output=True,
            check=False,
        )
    except OSError:
        return
    if res.returncode == 1:
        typer.echo(
            f"warning: {path} holds UNMASKED rows and is not ignored by Git; keep it under "
            "evidence/ or mask in the same step with --manifest",
            err=True,
        )


@app.command("inventory")
def inventory_cmd(
    url_env: str = typer.Argument(..., help="NAME of the env var holding the SQLAlchemy URL."),
    counts: bool = typer.Option(False, "--counts", help="Also count rows (COUNT(*) per table)."),
    db_schema: str = typer.Option(None, "--db-schema", help="Database schema to read."),
    out: Path = typer.Option(None, "--out", help="Write JSON here instead of printing."),
) -> None:
    """Tables, columns, keys, foreign keys, unique constraints and audit columns."""
    try:
        tables = inventory.pull(_url(url_env), counts=counts, schema=db_schema)
    except RealDataError as exc:
        _fail(str(exc))
    _write(out, json.dumps(inventory.to_json(tables), indent=2))


@app.command("extract")
def extract_cmd(
    url_env: str = typer.Option(..., "--url-env", help="NAME of the env var holding the URL."),
    spec: Path = typer.Option(..., "--spec", help="subset.yaml"),
    out: Path = typer.Option(..., "--out", help="rows.json"),
    manifest: Path = typer.Option(
        None, "--manifest", help="Mask in the same step: unmasked rows never touch the disk."
    ),
    report: Path = typer.Option(None, "--report", help="Write counts and truncations here."),
) -> None:
    """The closure of the spec's seeds, as Rows JSON. Prints what was truncated."""
    subset = _spec(spec)
    try:
        result = closure.extract_with_report(_url(url_env), subset)
    except RealDataError as exc:
        _fail(str(exc))
    rows = result.rows
    if manifest is not None:
        m = _manifest(manifest)
        try:
            masked = mask_mod.mask(rows, m, mask_mod.key_from_env(m))
        except mask_mod.MaskError as exc:
            _fail(str(exc))
        problems = mask_mod.sanitize(rows, masked, m, inventory.pull(_url(url_env)))
        if problems:
            _fail("sanitize failed, nothing written:\n" + "\n".join(problems))
        rows = masked
    out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    if manifest is None:
        _warn_if_tracked(out)
    for t in result.truncated:
        typer.echo(f"truncated: {t}")
    if result.stopped_at_max_rows:
        typer.echo(f"stopped at closure.max_rows ({subset.closure.max_rows})")
    if report is not None:
        report.write_text(json.dumps(result.report(), indent=2), encoding="utf-8")
    counts = ", ".join(f"{t}={n}" for t, n in result.counts.items())
    typer.echo(f"{sum(result.counts.values())} rows: {counts}")


@app.command("trace-diff")
def trace_diff_cmd(
    url_env: str = typer.Option(..., "--url-env", help="NAME of the env var holding the URL."),
    spec: Path = typer.Option(..., "--spec", help="subset.yaml"),
    observed: list[str] = typer.Option(..., "--observed", help="A table the screen reads."),
) -> None:
    """Tables a screen reads that the closure misses, with candidate associations."""
    try:
        missing = closure.missing_relations(_url(url_env), _spec(spec), observed)
    except RealDataError as exc:
        _fail(str(exc))
    for m in missing:
        typer.echo(f"read_but_not_in_closure: {m.table}")
        for a in m.candidates:
            typer.echo("  candidate: " + json.dumps(a.model_dump(exclude_none=True)))
    if missing:
        raise typer.Exit(1)
    typer.echo("closure covers every observed table")


@app.command("mask")
def mask_cmd(
    manifest: Path = typer.Option(..., "--manifest", help="mask.yaml"),
    in_: Path = typer.Option(..., "--in", help="rows.json"),
    out: Path = typer.Option(..., "--out", help="masked.json"),
    allow_unreviewed: bool = typer.Option(
        False, "--allow-unreviewed", help="Mask with a manifest nobody approved (tests only)."
    ),
) -> None:
    """Mask Rows JSON with the key from the env var the manifest names."""
    m = _manifest(manifest)
    try:
        masked = mask_mod.mask(
            _read_json(in_), m, mask_mod.key_from_env(m), allow_unreviewed=allow_unreviewed
        )
    except mask_mod.MaskError as exc:
        _fail(str(exc))
    out.write_text(json.dumps(masked, indent=1), encoding="utf-8")
    typer.echo(f"masked {sum(len(r) for r in masked.values())} rows into {out}")


@app.command("draft-manifest")
def draft_manifest_cmd(
    url_env: str = typer.Option(None, "--url-env", help="NAME of the env var holding the URL."),
    schema: Path = typer.Option(None, "--schema", help="Inventory JSON instead of a database."),
    app_id: str = typer.Option(None, "--app", help="App id to put in the manifest."),
    out: Path = typer.Option(None, "--out", help="Write YAML here instead of printing."),
) -> None:
    """A manifest guessed from column names, unapproved, for a data owner to review."""
    try:
        tables = _tables(url_env, schema)
    except RealDataError as exc:
        _fail(str(exc))
    _write(out, mask_mod.manifest_yaml(mask_mod.draft_manifest(tables, app=app_id)))


@app.command("sanitize")
def sanitize_cmd(
    manifest: Path = typer.Option(..., "--manifest", help="mask.yaml"),
    source: Path = typer.Option(..., "--source", help="rows.json (unmasked)"),
    masked: Path = typer.Option(..., "--masked", help="masked.json"),
    url_env: str = typer.Option(None, "--url-env", help="For foreign keys: env var NAME."),
    schema: Path = typer.Option(None, "--schema", help="For foreign keys: inventory JSON."),
) -> None:
    """Check masked rows against their source before loading. Exit 1 on any problem."""
    try:
        tables = _tables(url_env, schema)
    except RealDataError as exc:
        _fail(str(exc))
    problems = mask_mod.sanitize(
        _read_json(source), _read_json(masked), _manifest(manifest), tables
    )
    for p in problems:
        typer.echo(p)
    if problems:
        raise typer.Exit(1)
    typer.echo("sanitize: no problems")
