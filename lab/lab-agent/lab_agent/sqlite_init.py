"""Make a lab's SQLite database ready before the demo app starts: the file, WAL mode, and the
app's tables from its schema file (the same translation every engine gets: labctl.dbsetup).

    python -m lab_agent.sqlite_init        # reads ADMIN_DATABASE_URL and SCHEMA_PATH

`labctl lab up` runs this once in the agent's image (`docker compose run --rm agent ...`) before
it starts the app, so an app that expects its tables finds them. It is safe to run again: tables
that exist are left alone. The agent also runs it at startup.

The file lives on a volume both containers mount. The app's container may run as another user
than the agent, so the file is made writable by both (0666); SQLite gives its -wal and -shm files
the same permissions as the database file.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import sqlalchemy as sa


def database_path(url: str) -> Path | None:
    """The file a `sqlite:///...` URL names (None for an in-memory database)."""
    name = sa.engine.make_url(url).database
    return Path(name) if name and name != ":memory:" else None


def prepare(url: str, schema_file: Path | None) -> list[str]:
    """Create the file (WAL) and the missing tables. Returns the tables it created."""
    from labctl import dbsetup

    path = database_path(url)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)  # creates the file
        try:
            conn.execute("PRAGMA journal_mode = WAL")  # kept in the file: the app gets it too
        finally:
            conn.close()
        for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
            try:
                if p.exists():
                    os.chmod(p, 0o666)
            except OSError:  # not ours (an app that created it first): leave it
                pass
    if schema_file is None or not Path(schema_file).is_file():
        return []
    return dbsetup.create_tables(url, Path(schema_file), "sqlite")


def main() -> int:
    url = os.environ.get("ADMIN_DATABASE_URL", "")
    if not url.startswith("sqlite:"):
        print("sqlite_init: ADMIN_DATABASE_URL is not a sqlite:/// URL", file=sys.stderr)
        return 2
    schema = os.environ.get("SCHEMA_PATH")
    made = prepare(url, Path(schema) if schema else None)
    print("sqlite: created " + (", ".join(made) if made else "nothing (all tables exist)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
