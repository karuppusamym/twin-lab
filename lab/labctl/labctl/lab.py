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
    token = os.environ.get("TWINLAB_LAB_TOKEN", "")
    return {
        "name": project(app_id),
        "services": {
            "db": {
                "image": POSTGRES_IMAGE,
                "environment": {
                    "POSTGRES_USER": "lab",
                    "POSTGRES_PASSWORD": "lab",
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
                    "ADMIN_DATABASE_URL": f"postgresql://lab:lab@db:5432/{app_id}",
                    "SCHEMA_PATH": "/lab/schema.sql",
                    "STATE_DIR": "/var/lib/twinlab",
                    "LAB_PORT": "9000",
                    "LAB_TOKEN": token,
                },
                "volumes": [f"{schema.as_posix()}:/lab/schema.sql:ro", "state:/var/lib/twinlab"],
                "ports": [f"{app.lab.port}:9000", f"{app.lab.db_port}:15432"],
                "depends_on": {"db": {"condition": "service_healthy"}},
                "restart": "unless-stopped",
            },
            "app": {
                "image": f"twinlab/{app_id}:dev",
                "build": {"context": (root / "apps" / app_id).resolve().as_posix()},
                "environment": {db_env: f"postgresql://lab:lab@agent:15432/{app_id}"},
                "depends_on": ["agent"],
                "restart": "unless-stopped",
            },
        },
        "volumes": {"db": {}, "state": {}},
    }


def compose_file(root: Path, app_id: str) -> Path:
    path = state_dir(root) / "compose" / f"{app_id}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(compose(root, app_id), sort_keys=False), encoding="utf-8")
    return path


def _docker(args: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise LabError("Docker is not installed or not on PATH") from exc


def build_agent_image(root: Path) -> str:
    out = _docker(["build", "-t", AGENT_IMAGE, "-f", "lab/lab-agent/Dockerfile", str(root)])
    if out.returncode != 0:
        raise LabError(f"lab agent image build failed:\n{out.stderr[-3000:]}")
    return AGENT_IMAGE


def up(root: Path, app_id: str, build: bool = True) -> str:
    """Start (or update) the app's lab. Returns Docker's output."""
    if build and _docker(["image", "inspect", AGENT_IMAGE]).returncode != 0:
        build_agent_image(root)
    file = compose_file(root, app_id)
    args = ["compose", "-f", str(file), "up", "-d", "--wait", "--wait-timeout", "240"]
    if build:
        args.append("--build")
    out = _docker(args)
    if out.returncode != 0:
        raise LabError(
            f"`docker compose up` failed for {app_id}:\n{(out.stderr or out.stdout)[-3000:]}"
        )
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
    out = _docker(["compose", "-f", str(file), "ps", "--all", "--format", "json"], timeout=60)
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

    def load(self, rows: dict, label: str):
        return self._call("POST", "/_lab/data/load", json={"rows": rows, "label": label})

    def baseline(self):
        return self._call("POST", "/_lab/data/baseline")

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


def agent(root: Path, app_id: str, host: str | None = None) -> Agent:
    app = load_app(root, app_id)
    if app.lab is None:
        raise LabError(f"apps/{app_id}/app.yaml has no `lab:` section")
    host = host or os.environ.get("TWINLAB_HOST", "localhost")
    return Agent(f"http://{host}:{app.lab.port}", os.environ.get("TWINLAB_LAB_TOKEN", ""))
