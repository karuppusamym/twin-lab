"""Read-only connections to a replica. The URL is never logged or written; messages mask it."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

READ_VERBS = {"SELECT", "WITH", "PRAGMA", "SHOW", "VALUES", "EXPLAIN", "DESCRIBE", "SET"}
_LEADING = re.compile(r"^(\s+|--[^\n]*\n|/\*.*?\*/)+", re.S)


class RealDataError(Exception):
    """A problem reading the replica. The message never holds a password."""


class WriteRefused(RealDataError):
    pass


def safe_url(url: str) -> str:
    """The URL with its password replaced by ***, for messages."""
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:  # noqa: BLE001  (an unparseable URL is shown as nothing at all)
        return "<unparseable database URL>"


def scrub(message: str, url: str) -> str:
    """Remove the password (and the raw URL) from a driver message."""
    try:
        password = make_url(url).password
    except Exception:  # noqa: BLE001
        password = None
    message = message.replace(url, safe_url(url))
    if password:
        message = message.replace(str(password), "***")
    return message


_QUOTED = re.compile(r"'(?:[^']|'')*'")
_CHANGES = re.compile(
    # Verbs that change data or schema. Function and column names such as replace() or
    # comment or delete_rule (catalog queries use them) are not in the list.
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE|CALL|"
    r"INTO)\b",
    re.I,
)


def check_read_only(statement: str) -> None:
    """Refuse anything but one reading statement. The first word alone is not enough: SQL Server
    runs `SELECT ...; DELETE ...` as one batch, `WITH c AS (...) DELETE FROM c` starts like a read,
    and `SELECT ... INTO` creates a table."""
    head = _LEADING.sub("", statement)
    verb = head.split(None, 1)[0].upper() if head.strip() else ""
    if verb not in READ_VERBS or (verb == "PRAGMA" and "=" in head):
        raise WriteRefused(f"refused a non-read statement on a replica: {verb or '(empty)'}")
    bare = _QUOTED.sub("''", head)  # words inside string literals do not count
    if ";" in bare.rstrip().rstrip(";"):
        raise WriteRefused("refused more than one statement in one call on a replica")
    if "--" in bare or "/*" in bare:
        raise WriteRefused("refused a statement with comments inside it on a replica")
    if verb in ("SELECT", "WITH", "VALUES") and (found := _CHANGES.search(bare)):
        raise WriteRefused(f"refused a statement that contains {found.group(1).upper()}")


def _guard(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
    check_read_only(statement)


def _sqlite_readonly(url: URL) -> Engine:
    database = url.database or ""
    if database in ("", ":memory:"):
        raise RealDataError("an in-memory SQLite database cannot be opened read-only")
    uri = Path(database).resolve().as_uri() + "?mode=ro"

    def creator() -> sqlite3.Connection:
        return sqlite3.connect(uri, uri=True)

    return create_engine("sqlite://", creator=creator, poolclass=NullPool)


def open_readonly(url: str) -> Engine:
    """An engine that cannot change the database.

    Read-only at the session level where the dialect allows it (SQLite `mode=ro`, Postgres
    `default_transaction_read_only`, MySQL `SET SESSION TRANSACTION READ ONLY`), and on every
    dialect a statement guard that refuses anything but SELECT/WITH/PRAGMA/SHOW/SET.
    """
    try:
        parsed = make_url(url)
    except Exception as exc:  # noqa: BLE001
        raise RealDataError("not a SQLAlchemy database URL") from exc
    backend = parsed.get_backend_name()
    try:
        if backend == "sqlite":
            engine = _sqlite_readonly(parsed)
        else:
            connect_args: dict = {}
            if backend == "postgresql":
                connect_args["options"] = "-c default_transaction_read_only=on"
            elif backend in ("mysql", "mariadb"):
                connect_args["init_command"] = "SET SESSION TRANSACTION READ ONLY"
            elif backend == "mssql" and "ApplicationIntent" not in parsed.query:
                # Routes to a readable secondary where there is one; it is not enforcement, so a
                # read-only login (db_datareader) is still required: see docs/realdata.md.
                parsed = parsed.update_query_dict({"ApplicationIntent": "ReadOnly"})
            engine = create_engine(parsed, connect_args=connect_args, poolclass=NullPool)
            if backend == "oracle":
                # Every transaction read only, from its first statement.
                event.listen(
                    engine, "begin", lambda c: c.exec_driver_sql("SET TRANSACTION READ ONLY")
                )
    except (SQLAlchemyError, ImportError, ValueError) as exc:
        raise RealDataError(scrub(f"cannot open {safe_url(url)}: {exc}", url)) from None
    event.listen(engine, "before_cursor_execute", _guard)
    return engine


@contextmanager
def connection(url: str):
    """A read-only connection; driver errors come back as RealDataError without the password."""
    engine = open_readonly(url)
    try:
        with engine.connect() as conn:
            yield conn
    except WriteRefused:
        raise
    except SQLAlchemyError as exc:
        detail = str(getattr(exc, "orig", None) or exc).splitlines()[0]
        raise RealDataError(scrub(f"{safe_url(url)}: {detail}", url)) from None
    finally:
        engine.dispose()
