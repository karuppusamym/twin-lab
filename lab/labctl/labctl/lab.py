"""Run demo apps in the lab: one Docker Compose project per app (database, lab agent, app).

browser / bot -> lab agent :<lab.port> -> app -> lab agent DB proxy -> Postgres
                                \\-> admin API /_lab/*         (bots' SQL also via :<lab.db_port>)
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

from labctl.bindings import BindingError, load_app

AGENT_IMAGE = os.environ.get("TWINLAB_AGENT_IMAGE", "twinlab/lab-agent:dev")
POSTGRES_IMAGE = "postgres:17-alpine"


class LabError(Exception):
    pass


def project(app_id: str) -> str:
    return f"twinlab-{app_id}"


def state_dir(root: Path) -> Path:
    path = Path(os.environ.get("TWINLAB_STATE", root / ".lab-state"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _secret(root: Path, name: str, env: str | None = None) -> str:
    """A value kept in the state folder (Git-ignored), generated once. An environment variable,
    when given and set, wins and is written back, so every process on the machine agrees."""
    import secrets

    path = state_dir(root) / name
    given = os.environ.get(env, "") if env else ""
    if given:
        if not path.exists() or path.read_text(encoding="utf-8").strip() != given:
            path.write_text(given, encoding="utf-8")
        return given
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    value = secrets.token_urlsafe(24)
    path.write_text(value, encoding="utf-8")
    return value


def lab_token(root: Path) -> str:
    """The admin token for every lab agent: TWINLAB_LAB_TOKEN, or one generated once and kept in
    the state folder. Bots never need it; only the portal and labctl do."""
    return _secret(root, "lab-token", "TWINLAB_LAB_TOKEN")


def db_password(root: Path) -> str:
    """The lab database's owner password (the app and the agent use it; bots never see it)."""
    return _secret(root, "lab-db-password")


def bot_password(root: Path) -> str:
    """The password of `bot`, the database login handed to automations: rows only, no DDL."""
    return _secret(root, "lab-bot-password")


SETTINGS = {"bind": ("TWINLAB_LAB_BIND", "127.0.0.1"), "host": ("TWINLAB_HOST", "127.0.0.1")}


def setting(root: Path, key: str) -> str:
    """A lab setting: the environment variable if set (and then remembered in
    `.lab-state/lab.yaml`), else the remembered value, else the default. So `labctl lab up` in one
    shell and the portal started from another use the same bind address and host name."""
    var, default = SETTINGS[key]
    path = state_dir(root) / "lab.yaml"
    try:
        saved = yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}
    except (OSError, yaml.YAMLError):
        saved = {}
    # Every lab variable set in this process is remembered, whichever one is being read.
    seen = {k: os.environ.get(v, "").strip() for k, (v, _) in SETTINGS.items()}
    changed = {k: v for k, v in seen.items() if v and saved.get(k) != v}
    if changed:
        saved.update(changed)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(yaml.safe_dump(saved, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    return seen[key] or str(saved.get(key) or default)


def bind_address(root: Path) -> str:
    """Where the lab's ports listen: this machine only, unless TWINLAB_LAB_BIND says otherwise
    (0.0.0.0 when bots on other machines must reach the apps; then set a firewall rule)."""
    return setting(root, "bind")


def compose(root: Path, app_id: str) -> dict:
    """The Compose document for one app. Ports come from `lab:` in app.yaml."""
    app = load_app(root, app_id)
    if app.lab is None:
        raise LabError(f"apps/{app_id}/app.yaml has no `lab:` section (port, db_port)")
    if app.run.web is None and app.run.api is None:
        raise LabError(f"apps/{app_id}/app.yaml has no `run:` port")
    app_port = (app.run.web or app.run.api).port
    db_env = app.run.env.database_url
    schema = (root / "apps" / app_id / "db" / "schema.sql").resolve()
    token = lab_token(root)
    bind = bind_address(root)
    pw = db_password(root)  # not "lab": the DB port can be reachable from the network
    return {
        "name": project(app_id),
        "services": {
            "db": {
                "image": POSTGRES_IMAGE,
                "environment": {
                    "POSTGRES_USER": "lab",
                    "POSTGRES_PASSWORD": pw,
                    "POSTGRES_DB": app_id,
                },
                "volumes": ["db:/var/lib/postgresql/data"],
                "healthcheck": {
                    "test": ["CMD-SHELL", f"pg_isready -h 127.0.0.1 -U lab -d {app_id}"],
                    "interval": "3s",
                    "timeout": "3s",
                    "retries": 40,
                },
                "restart": "unless-stopped",
            },
            "agent": {
                "image": AGENT_IMAGE,
                "environment": {
                    "APP_ID": app_id,
                    "APP_UPSTREAM": f"http://app:{app_port}",
                    "DB_UPSTREAM": "db:5432",
                    "DB_PROXY_PORT": "15432",
                    "ADMIN_DATABASE_URL": f"postgresql://lab:{pw}@db:5432/{app_id}",
                    "SCHEMA_PATH": "/lab/schema.sql",
                    "STATE_DIR": "/var/lib/twinlab",
                    "LAB_PORT": "9000",
                    "LAB_TOKEN": token,
                },
                "volumes": [f"{schema.as_posix()}:/lab/schema.sql:ro", "state:/var/lib/twinlab"],
                "ports": [f"{bind}:{app.lab.port}:9000", f"{bind}:{app.lab.db_port}:15432"],
                "depends_on": {"db": {"condition": "service_healthy"}},
                "restart": "unless-stopped",
            },
            "app": {
                "image": f"twinlab/{app_id}:dev",
                "build": {"context": (root / "apps" / app_id).resolve().as_posix()},
                "environment": {db_env: f"postgresql://lab:{pw}@agent:15432/{app_id}"},
                "depends_on": ["agent"],
                "restart": "unless-stopped",
            },
        },
        "volumes": {"db": {}, "state": {}},
    }


def compose_file(root: Path, app_id: str) -> Path:
    """Write the Compose file only when it changed, atomically (a running `up` may be reading it)."""
    path = state_dir(root) / "compose" / f"{app_id}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(compose(root, app_id), sort_keys=False)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    return path


def _docker(args: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    try:
        # UTF-8 with replacement: on Windows the default (cp1252) cannot decode some bytes Docker
        # prints, the reader thread dies and stdout comes back None instead of the real error.
        out = subprocess.run(
            ["docker", *args], capture_output=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False,
        )  # fmt: skip
        out.stdout, out.stderr = out.stdout or "", out.stderr or ""
        return out
    except FileNotFoundError as exc:
        raise LabError("Docker is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise LabError(
            f"Docker did not answer within {timeout} s (is Docker Desktop still starting?)"
        ) from exc


def build_agent_image(root: Path) -> str:
    root = Path(root).resolve()
    dockerfile = root / "lab" / "lab-agent" / "Dockerfile"
    out = _docker(["build", "-t", AGENT_IMAGE, "-f", str(dockerfile), str(root)])
    if out.returncode != 0:
        raise LabError(f"lab agent image build failed:\n{out.stderr[-3000:]}")
    return AGENT_IMAGE


def _roles_sql(app_id: str, owner_pw: str, bot_pw: str) -> str:
    """The owner gets this machine's password; `bot` (for automations) may read and change rows
    but is no superuser, so it cannot run programs, read files or drop tables."""
    q = lambda s: "'" + s.replace("'", "''") + "'"  # noqa: E731
    return f"""
ALTER ROLE lab PASSWORD {q(owner_pw)};
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bot') THEN
    CREATE ROLE bot LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
END $$;
ALTER ROLE bot PASSWORD {q(bot_pw)};
GRANT CONNECT ON DATABASE "{app_id}" TO bot;
GRANT USAGE ON SCHEMA public TO bot;
ALTER DEFAULT PRIVILEGES FOR ROLE lab IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO bot;
ALTER DEFAULT PRIVILEGES FOR ROLE lab IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO bot;
""" + _grants_sql()


def _grants_sql() -> str:
    return (
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO bot;\n"
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO bot;\n"
    )


def _psql(file: Path, app_id: str, sql: str) -> None:
    """Run SQL as the owner inside the db container (local socket: no password needed)."""
    out = _docker(
        ["compose", "-f", str(file), "exec", "-T", "db", "psql", "-v", "ON_ERROR_STOP=1",
         "-U", "lab", "-d", app_id, "-q", "-c", sql],
        timeout=120,
    )  # fmt: skip
    if out.returncode != 0:
        raise LabError(f"setting up the database logins of {app_id} failed:\n{out.stderr[-2000:]}")


def up(root: Path, app_id: str, build: bool = True) -> str:
    """Start (or update) the app's lab. Returns Docker's output."""
    # Rebuilt every time (Docker's cache makes an unchanged build quick), so after a `git pull`
    # the lab runs the agent from this checkout, not an old image.
    if build and "TWINLAB_AGENT_IMAGE" not in os.environ:
        build_agent_image(root)
    file = compose_file(root, app_id)
    # 1. The database alone, so its logins match this machine's passwords before the app and the
    #    agent connect with them (a volume made earlier keeps the password it was created with).
    first = _docker(
        ["compose", "-f", str(file), "up", "-d", "--wait", "--wait-timeout", "240", "db"]
    )
    if first.returncode != 0:
        raise LabError(f"the database of {app_id} did not start:\n{first.stderr[-3000:]}")
    _psql(file, app_id, _roles_sql(app_id, db_password(root), bot_password(root)))
    # 2. Everything.
    args = ["compose", "-f", str(file), "up", "-d", "--wait", "--wait-timeout", "240"]
    if build:
        args.append("--build")
    out = _docker(args)
    if out.returncode != 0:
        raise LabError(
            f"`docker compose up` failed for {app_id}:\n{(out.stderr or out.stdout)[-3000:]}"
        )
    _psql(file, app_id, _grants_sql())  # tables the app created on start
    # The first time, the app's own starting data becomes the baseline, so Reset works at once.
    try:
        lab_agent = agent(root, app_id)
        if not (lab_agent.info() or {}).get("baseline"):
            lab_agent.baseline()
    except LabError:
        pass  # the app runs; the baseline can be set from the portal
    return out.stdout + out.stderr


def down(root: Path, app_id: str, wipe: bool = False) -> str:
    file = compose_file(root, app_id)
    out = _docker(["compose", "-f", str(file), "down", *(["-v"] if wipe else [])])
    if out.returncode != 0:
        raise LabError(f"`docker compose down` failed for {app_id}:\n{out.stderr[-2000:]}")
    return out.stdout + out.stderr


@dataclass
class Status:
    app: str
    running: bool
    services: dict[str, str]  # service -> state
    health: dict | None = None
    error: str | None = None


def status(root: Path, app_id: str) -> Status:
    try:
        file = compose_file(root, app_id)
    except (LabError, BindingError) as exc:
        return Status(app_id, False, {}, error=str(exc))
    try:
        out = _docker(["compose", "-f", str(file), "ps", "--all", "--format", "json"], timeout=20)
    except LabError as exc:  # no Docker, or Docker not answering: the page still renders
        return Status(app_id, False, {}, error=str(exc))
    services: dict[str, str] = {}
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items = json.loads(line)
        except ValueError:
            continue
        for item in items if isinstance(items, list) else [items]:
            services[item.get("Service", "?")] = item.get("Health") or item.get("State", "?")
    running = bool(services) and all(s in ("running", "healthy") for s in services.values())
    health = None
    if running:
        try:
            health = agent(root, app_id).health()
        except LabError as exc:
            return Status(app_id, running, services, error=str(exc))
    return Status(app_id, running, services, health)


class Agent:
    """Client for one app's lab agent admin API (/_lab/*)."""

    def __init__(self, base_url: str, token: str = "", timeout: float = 60):
        headers = {"X-Lab-Token": token} if token else {}
        self.base = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base, headers=headers, timeout=timeout)

    def _call(self, method: str, path: str, **kwargs):
        try:
            r = self.http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise LabError(
                f"lab agent at {self.base} is not reachable ({exc.__class__.__name__})"
            ) from exc
        if r.status_code >= 400 and not (path == "/_lab/health" and r.status_code == 503):
            try:
                detail = r.json()
            except ValueError:
                detail = r.text[:500]
            raise LabError(f"{method} {path} -> {r.status_code}: {detail}")
        return r.json()

    def health(self):
        return self._call("GET", "/_lab/health")

    def info(self):
        return self._call("GET", "/_lab/info")

    def data(self):
        return self._call("GET", "/_lab/data")

    def load(self, rows: dict, label: str, pack: str | None = None):
        body = {"rows": rows, "label": label, **({"pack": pack} if pack else {})}
        return self._call("POST", "/_lab/data/load", json=body)

    def baseline(self, label: str | None = None, pack: str | None = None):
        body = {k: v for k, v in (("label", label), ("pack", pack)) if v}
        return self._call("POST", "/_lab/data/baseline", json=body)

    def baselines(self):
        return self._call("GET", "/_lab/baselines")

    def restore_baseline(self, baseline_id: str):
        return self._call("POST", f"/_lab/baselines/{baseline_id}/restore")

    def pin_baseline(self, baseline_id: str, pinned: bool = True):
        return self._call("POST", f"/_lab/baselines/{baseline_id}/pin", json={"pinned": pinned})

    def reset(self):
        return self._call("POST", "/_lab/reset")

    def condition(self):
        return self._call("GET", "/_lab/condition")

    def set_condition(self, condition: dict | None):
        return self._call("PUT", "/_lab/condition", json=condition or {})

    def start_session(self, automation: str | None = None, label: str | None = None):
        return self._call(
            "POST", "/_lab/session/start", json={"automation": automation, "label": label}
        )

    def stop_session(self):
        return self._call("POST", "/_lab/session/stop")

    def sessions(self):
        return self._call("GET", "/_lab/sessions")

    def session(self, session_id: str):
        return self._call("GET", f"/_lab/sessions/{session_id}")

    def session_detail(self, session_id: str):
        return self._call("GET", f"/_lab/sessions/{session_id}/detail")


def agent(root: Path, app_id: str, host: str | None = None) -> Agent:
    app = load_app(root, app_id)
    if app.lab is None:
        raise LabError(f"apps/{app_id}/app.yaml has no `lab:` section")
    # Admin calls come from this machine, so they go to 127.0.0.1 whatever TWINLAB_HOST says (that
    # name is for the addresses handed to bots elsewhere, and need not be bound or resolve to IPv4).
    host = host or "127.0.0.1"
    return Agent(f"http://{host}:{app.lab.port}", lab_token(root))
