"""Where a demo app's database lives: Twin Lab's own container, or a server the office provides.

Each app's `app.yaml` names it in `lab.database`:

- not set, or `postgres`: a Postgres container Twin Lab starts and owns (the default);
- `sqlserver`: a SQL Server 2022 container Twin Lab starts and owns (Developer edition: for
  testing only, under Microsoft's licence; nothing to configure);
- `sqlite`: one SQLite file on a Docker volume the app and the lab agent share; no database
  container and no database port (so no slow-database condition, and bots get no database
  address). For small demo apps;
- any other name: a server the DBA set up, configured once per machine in `twinlab.local.yaml`
  (Git-ignored) or in environment variables:

```yaml
databases:
  qtt-sqlserver:                 # the name an app's lab.database uses
    engine: mssql                # mssql | oracle | postgresql
    host: sqldemo01.office.local
    port: 1433
    database: TWINLAB_QTT        # SQL Server and Postgres: the database
    # service: FREEPDB1          # Oracle: the service name instead
    owner_user: twinlab_owner    # may create tables and load data
    owner_password_env: TL_QTT_OWNER_PASSWORD
    bot_user: twinlab_bot        # what bots get (rows only); optional, else the owner
    bot_password_env: TL_QTT_BOT_PASSWORD
    # schema: dbo                # optional: the schema holding the app's tables
```

A password can also be written as `owner_password:` / `bot_password:` in that local file; an
environment variable is better, and wins. Passwords are never shown or written anywhere else.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import yaml

LOCAL_FILE = "twinlab.local.yaml"
BUILT_IN = {"postgres": "postgresql", "sqlserver": "mssql", "sqlite": "sqlite"}
ENGINES = {"mssql", "oracle", "postgresql"}  # what a DBA's server may be
ENGINE_NAMES = {"mssql": "mssql", "sqlserver": "mssql", "sql server": "mssql", "oracle": "oracle",
                "postgresql": "postgresql", "postgres": "postgresql", "pg": "postgresql"}  # fmt: skip
DEFAULT_PORT = {"mssql": 1433, "oracle": 1521, "postgresql": 5432}
DRIVER = {"mssql": "mssql+pymssql", "oracle": "oracle+oracledb", "postgresql": "postgresql"}
LABEL = {"mssql": "SQL Server", "oracle": "Oracle", "postgresql": "PostgreSQL", "sqlite": "SQLite"}


class DbServerError(Exception):
    pass


@dataclass
class DbServer:
    name: str
    engine: str
    host: str
    port: int
    database: str | None = None
    service: str | None = None
    owner_user: str = ""
    owner_password: str = ""
    bot_user: str = ""
    bot_password: str = ""
    schema: str | None = None

    @property
    def label(self) -> str:
        return LABEL[self.engine]

    def problems(self) -> list[str]:
        out = []
        if self.engine not in ENGINES:
            out.append(f"engine must be one of {', '.join(sorted(ENGINES))}")
        if not self.host:
            out.append("host is missing")
        if self.engine == "oracle" and not (self.service or self.database):
            out.append("an Oracle server needs `service`")
        if self.engine != "oracle" and not self.database:
            out.append("`database` is missing")
        if not self.owner_user:
            out.append("owner_user is missing")
        if not self.owner_password:
            out.append("the owner's password is not set (owner_password_env, or owner_password)")
        return out

    def url(self, bot: bool = False, host: str | None = None, port: int | None = None) -> str:
        """A SQLAlchemy URL: the owner's (Twin Lab's own work) or the bot's (what apps and bots
        get), optionally through another address (the lab agent's database link)."""
        user = (self.bot_user or self.owner_user) if bot else self.owner_user
        password = (self.bot_password or self.owner_password) if bot and self.bot_user else (
            self.owner_password)  # fmt: skip
        h, p = host or self.host, port or self.port
        auth = f"{quote(user, safe='')}:{quote(password, safe='')}@"
        if self.engine == "oracle":
            return f"{DRIVER['oracle']}://{auth}{h}:{p}/?service_name={quote(self.service or self.database or '')}"
        return f"{DRIVER[self.engine]}://{auth}{h}:{p}/{quote(self.database or '', safe='')}"

    def connection_string(
        self, bot: bool = True, host: str | None = None, port: int | None = None
    ) -> str:
        """The ADO.NET-style string UiPath's database activities and .NET apps take."""
        user = (self.bot_user or self.owner_user) if bot else self.owner_user
        password = (self.bot_password or self.owner_password) if bot and self.bot_user else (
            self.owner_password)  # fmt: skip
        h, p = host or self.host, port or self.port
        if self.engine == "mssql":
            return (f"Server={h},{p};Database={self.database};User Id={user};Password={password};"
                    "TrustServerCertificate=True")  # fmt: skip
        if self.engine == "oracle":
            return f"Data Source={h}:{p}/{self.service or self.database};User Id={user};Password={password}"
        return f"Host={h};Port={p};Database={self.database};Username={user};Password={password}"

    def described(self) -> dict:
        """For pages and logs: everything except passwords."""
        return {"name": self.name, "engine": self.label, "host": self.host, "port": self.port,
                "database": self.database, "service": self.service, "owner_user": self.owner_user,
                "bot_user": self.bot_user or "(the owner)", "schema": self.schema,
                "owner_password": "set" if self.owner_password else "missing",
                "bot_password": ("set" if self.bot_password else "missing") if self.bot_user else "-"}  # fmt: skip


def _password(entry: dict, key: str, env: dict) -> str:
    var = entry.get(f"{key}_env")
    if var and env.get(str(var)):
        return env[str(var)]
    return str(entry.get(key) or "")


def load(root: Path, env: dict | None = None) -> dict[str, DbServer]:
    """The servers configured on this machine, by name."""
    env = dict(os.environ if env is None else env)
    path = Path(root) / LOCAL_FILE
    entries: dict = {}
    if path.is_file():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise DbServerError(f"{path}: cannot read: {exc}") from exc
        entries = data.get("databases") or {} if isinstance(data, dict) else {}
        if not isinstance(entries, dict):
            raise DbServerError(f"{path}: `databases` must map names to settings")
    if env.get("TWINLAB_DATABASES"):  # the same mapping as YAML text: for CI, from a secret
        try:
            extra = yaml.safe_load(env["TWINLAB_DATABASES"]) or {}
        except yaml.YAMLError as exc:
            raise DbServerError(f"TWINLAB_DATABASES is not valid YAML: {exc}") from exc
        if not isinstance(extra, dict):
            raise DbServerError("TWINLAB_DATABASES must map names to settings")
        entries = {**entries, **extra.get("databases", extra)}
    out = {}
    for name, entry in entries.items():
        if not isinstance(entry, dict):
            raise DbServerError(f"databases.{name}: expected settings")
        engine = ENGINE_NAMES.get(str(entry.get("engine") or "").strip().lower(), "")
        out[str(name)] = DbServer(
            name=str(name), engine=engine, host=str(entry.get("host") or ""),
            port=int(entry.get("port") or DEFAULT_PORT.get(engine, 0)),
            database=entry.get("database"), service=entry.get("service"),
            owner_user=str(entry.get("owner_user") or ""),
            owner_password=_password(entry, "owner_password", env),
            bot_user=str(entry.get("bot_user") or ""),
            bot_password=_password(entry, "bot_password", env),
            schema=entry.get("schema"),
        )  # fmt: skip
    return out


def choice(app_database: str | None) -> str:
    """'postgres', 'sqlserver', 'sqlite' or a configured server's name."""
    return (app_database or "postgres").strip() or "postgres"


def engine_for(root: Path, app_database: str | None) -> str:
    name = choice(app_database)
    if name in BUILT_IN:
        return BUILT_IN[name]
    server = load(root).get(name)
    if server is None:
        raise DbServerError(f"database {name!r} is not configured in {LOCAL_FILE} (databases:)")
    return server.engine


def server(root: Path, name: str, need_passwords: bool = True) -> DbServer:
    found = load(root).get(name)
    if found is None:
        raise DbServerError(f"database {name!r} is not configured in {LOCAL_FILE} (databases:)")
    problems = [p for p in found.problems() if need_passwords or "password" not in p]
    if problems:
        raise DbServerError(f"database {name}: " + "; ".join(problems))
    return found


def check(srv: DbServer, timeout: int = 10) -> str:
    """Connect as the owner and the bot. Returns a one-line result; raises DbServerError."""
    import sqlalchemy as sa

    probe = "SELECT 1 FROM DUAL" if srv.engine == "oracle" else "SELECT 1"
    args = {"connect_args": {"login_timeout": timeout}} if srv.engine == "mssql" else {}
    said = []
    for who, bot in (("owner", False), ("bot", True)):
        if bot and not srv.bot_user:
            continue
        try:
            engine = sa.create_engine(srv.url(bot=bot), **args)
            with engine.connect() as c:
                c.execute(sa.text(probe))
            engine.dispose()
            said.append(f"{who} ok")
        except Exception as exc:  # noqa: BLE001 - drivers raise their own types
            text = str(getattr(exc, "orig", exc) or exc).splitlines()[0][:200]
            for secret in (srv.owner_password, srv.bot_password):
                if secret:
                    text = text.replace(secret, "***")
            raise DbServerError(f"{srv.name}: the {who} cannot connect: {text}") from exc
    return f"{srv.name} ({srv.label} at {srv.host}:{srv.port}): " + ", ".join(said)
