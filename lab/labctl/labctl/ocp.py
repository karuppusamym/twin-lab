"""Run an app's lab on OpenShift: the Compose lab of `labctl.lab`, as OpenShift manifests.

    Route (edge TLS) -> agent :9000 -> app -> agent DB proxy :15432 -> db :5432
                         \\-> admin API /_lab/* (X-Lab-Token)

Every secret value lives in one Secret per app (`twinlab-<app>-secrets`), created by
`labctl ocp deploy` when missing and never overwritten. The manifests only name its keys, so the
rendered YAML can be shown, committed or attached to a ticket.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import yaml

from labctl.bindings import load_app
from labctl.lab import _grants_sql, _roles_sql

POSTGRES_IMAGE = "quay.io/sclorg/postgresql-16-c9s"  # runs under the restricted SCC, any UID
SECRET_KEYS = ("lab-token", "db-password", "bot-password")
DNS1123 = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_OWNER_MARK, _BOT_MARK = "TWINLAB_OWNER_PASSWORD", "TWINLAB_BOT_PASSWORD"
AGENT_PORT, AGENT_DB_PORT, DB_PORT = 9000, 15432, 5432

# The agent's health needs the token, so it is checked from inside the container, as the
# Dockerfile HEALTHCHECK does. 200 (app and db up) and 503 (agent up, app or db not yet) both mean
# the agent itself answers with this token; anything else (401: wrong token) is a failure.
# Readiness must not wait for the app: the app reaches its database through the agent's Service.
_AGENT_PROBE = """\
import os, sys, urllib.error, urllib.request as u
r = u.Request("http://127.0.0.1:9000/_lab/health", headers={"X-Lab-Token": os.environ["LAB_TOKEN"]})
try:
    status = u.urlopen(r, timeout=4).status
except urllib.error.HTTPError as exc:
    status = exc.code
sys.exit(0 if status in (200, 503) else 1)
"""

# Waits for the database, then runs setup.sql as `postgres` with the passwords passed as psql
# variables (they come from the Secret through the environment; they are never in a manifest).
# PGPASSWORD is db-password: the admin and owner passwords are the same value (see the db env).
_SETUP_SCRIPT = """\
set -eu
for i in $(seq 1 90); do
  pg_isready -q -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" && break
  echo "waiting for the database ($i)"; sleep 2
done
psql -v ON_ERROR_STOP=1 -q -v owner_pw="$PGPASSWORD" -v bot_pw="$BOT_PASSWORD" -f /setup/setup.sql
echo "database logins set up"
"""


class OcpError(Exception):
    pass


def name(app_id: str, part: str) -> str:
    return f"twinlab-{app_id}-{part}"


def secret_name(app_id: str) -> str:
    return name(app_id, "secrets")


def image(registry: str, image_name: str, tag: str) -> str:
    return f"{registry.rstrip('/')}/{image_name}:{tag}"


def setup_sql(app_id: str) -> str:
    """`_roles_sql` then `_grants_sql`, with the passwords as psql variables `:'owner_pw'` and
    `:'bot_pw'` instead of literals. Safe to run again: every statement is idempotent."""
    sql = _roles_sql(app_id, _OWNER_MARK, _BOT_MARK)
    for mark, var in ((_OWNER_MARK, ":'owner_pw'"), (_BOT_MARK, ":'bot_pw'")):
        if sql.count(f"'{mark}'") != 1:
            raise OcpError("the roles SQL changed shape; update labctl.ocp.setup_sql")
        sql = sql.replace(f"'{mark}'", var)
    if _grants_sql() not in sql:  # the grants after the app created its tables, as `lab.up`
        sql += _grants_sql()
    return sql.lstrip("\n")


def _labels(app_id: str, component: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/part-of": "twinlab",
        "app.kubernetes.io/instance": app_id,
        "app.kubernetes.io/component": component,
        "app.kubernetes.io/name": name(app_id, component),
        "app.kubernetes.io/managed-by": "labctl",
    }


def _selector(app_id: str, component: str) -> dict[str, str]:
    return {"app.kubernetes.io/instance": app_id, "app.kubernetes.io/component": component}


def _meta(app_id: str, component: str, obj_name: str, namespace: str, **extra) -> dict:
    return {"name": obj_name, "namespace": namespace, "labels": _labels(app_id, component), **extra}


def _secret_env(env: str, app_id: str, key: str) -> dict:
    return {
        "name": env,
        "valueFrom": {"secretKeyRef": {"name": secret_name(app_id), "key": key}},
    }


def _restricted() -> dict:
    """What OpenShift's restricted-v2 SCC demands. No runAsUser: OpenShift picks the UID."""
    return {
        "runAsNonRoot": True,
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "seccompProfile": {"type": "RuntimeDefault"},
    }


def _pod_security() -> dict:
    return {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}}


def _resources(cpu: str, memory: str, cpu_limit: str, memory_limit: str) -> dict:
    return {
        "requests": {"cpu": cpu, "memory": memory},
        "limits": {"cpu": cpu_limit, "memory": memory_limit},
    }


def _pvc(app_id: str, component: str, obj_name: str, namespace: str, size: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": _meta(app_id, component, obj_name, namespace),
        "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": size}}},
    }


def _service(app_id: str, component: str, namespace: str, ports: list[tuple[str, int]]) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": _meta(app_id, component, name(app_id, component), namespace),
        "spec": {
            "selector": _selector(app_id, component),
            "ports": [{"name": n, "port": p, "targetPort": p, "protocol": "TCP"} for n, p in ports],
        },
    }


def _deployment(
    app_id: str, component: str, namespace: str, pod: dict, *, recreate: bool, annotations=None
) -> dict:
    template_meta: dict = {"labels": _labels(app_id, component)}
    if annotations:
        template_meta["annotations"] = annotations
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": _meta(app_id, component, name(app_id, component), namespace),
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate" if recreate else "RollingUpdate"},
            "selector": {"matchLabels": _selector(app_id, component)},
            "template": {"metadata": template_meta, "spec": pod},
        },
    }


def _pod(containers: list[dict], volumes: list[dict] | None = None, pull_secret=None) -> dict:
    pod: dict = {"securityContext": _pod_security(), "containers": containers}
    if volumes:
        pod["volumes"] = volumes
    if pull_secret:
        pod["imagePullSecrets"] = [{"name": pull_secret}]
    return pod


def _check_names(app_id: str) -> None:
    longest = name(app_id, "db-setup")
    if not DNS1123.match(app_id) or len(longest) > 63:
        raise OcpError(
            f"app id {app_id!r} does not make DNS-1123 names (a-z, 0-9, '-', and "
            f"{longest!r} must be at most 63 characters)"
        )


def render(
    root: Path,
    app_id: str,
    *,
    registry: str,
    tag: str,
    namespace: str,
    postgres_image: str = POSTGRES_IMAGE,
    pull_secret: str | None = None,
    expose_db: bool = False,
) -> list[dict]:
    """OpenShift manifests for one app's lab, equivalent to `labctl.lab.compose`. The Secret is not
    among them: `deploy` creates it once (see `ensure_secret`).

    The database is Twin Lab's own Postgres, or a server the DBA provides (app.yaml
    `lab.database`, configured in `databases:`): then no database is deployed, a Job creates the
    app's tables there, and its connection details come from the Secret `twinlab-<app>-dbconn`,
    which every deploy refreshes. `expose_db` adds a NodePort Service for the database link, so
    bots outside the cluster can query the database (through the agent, slowdowns included)."""
    _check_names(app_id)
    if not DNS1123.match(namespace):
        raise OcpError(f"namespace {namespace!r} is not a DNS-1123 name")
    app = load_app(root, app_id)
    if app.lab is None:
        raise OcpError(f"apps/{app_id}/app.yaml has no `lab:` section (port, db_port)")
    if app.run.web is None and app.run.api is None:
        raise OcpError(f"apps/{app_id}/app.yaml has no `run:` port")
    app_port = (app.run.web or app.run.api).port
    health_port, health_path = app_port, "/"
    if app.run.api is not None:
        health_port, health_path = app.run.api.port, app.run.api.health
    schema_file = root / "apps" / app_id / "db" / "schema.sql"
    if not schema_file.is_file():
        raise OcpError(f"apps/{app_id}/db/schema.sql is missing")
    schema = schema_file.read_text(encoding="utf-8")

    db_host, agent_host = name(app_id, "db"), name(app_id, "agent")
    db_url = f"postgresql://lab:$(DB_PASSWORD)@%s/{app_id}"  # $(VAR): expanded by Kubernetes
    docs: list[dict] = []
    ext = _external_server(root, app)

    # --- configuration -------------------------------------------------------------------------
    docs.append({
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": _meta(app_id, "agent", name(app_id, "schema"), namespace),
        "data": {"schema.sql": schema},
    })  # fmt: skip
    if ext is None:
        docs.append({
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": _meta(app_id, "db-setup", name(app_id, "db-setup"), namespace),
            "data": {"setup.sql": setup_sql(app_id), "setup.sh": _SETUP_SCRIPT},
        })  # fmt: skip

    if ext is None:
        # --- database ------------------------------------------------------------------------------
        docs.append(_pvc(app_id, "db", name(app_id, "db"), namespace, "5Gi"))
        docs.append(_service(app_id, "db", namespace, [("postgres", DB_PORT)]))
        ready = ["pg_isready", "-q", "-h", "127.0.0.1", "-U", "lab", "-d", app_id]
        db_container = {
            "name": "db",
            "image": postgres_image,
            "env": [
                {"name": "POSTGRESQL_USER", "value": "lab"},
                _secret_env("POSTGRESQL_PASSWORD", app_id, "db-password"),
                {"name": "POSTGRESQL_DATABASE", "value": app_id},
                # In this image `lab` owns the database but is no superuser, so it cannot create the
                # `bot` login; the setup Job signs in as `postgres`. Same password: in the Compose lab
                # `lab` itself is the superuser, so this gives nobody more than there.
                _secret_env("POSTGRESQL_ADMIN_PASSWORD", app_id, "db-password"),
            ],
            "ports": [{"name": "postgres", "containerPort": DB_PORT}],
            "volumeMounts": [{"name": "data", "mountPath": "/var/lib/pgsql/data"}],
            "readinessProbe": {"exec": {"command": ready}, "periodSeconds": 5, "timeoutSeconds": 3},
            "livenessProbe": {
                "exec": {"command": ready},
                "initialDelaySeconds": 60,
                "periodSeconds": 15,
                "timeoutSeconds": 5,
                "failureThreshold": 6,
            },
            "resources": _resources("100m", "256Mi", "1", "1Gi"),
            "securityContext": _restricted(),
        }
        db_volumes = [{"name": "data", "persistentVolumeClaim": {"claimName": name(app_id, "db")}}]
        docs.append(
            _deployment(app_id, "db", namespace, _pod([db_container], db_volumes), recreate=True)
        )

        # --- database logins (run on every deploy; idempotent) -------------------------------------
        setup_container = {
            "name": "db-setup",
            "image": postgres_image,
            "command": ["bash", "/setup/setup.sh"],
            "env": [
                {"name": "PGHOST", "value": db_host},
                {"name": "PGPORT", "value": str(DB_PORT)},
                {"name": "PGUSER", "value": "postgres"},
                {"name": "PGDATABASE", "value": app_id},
                _secret_env("PGPASSWORD", app_id, "db-password"),
                _secret_env("BOT_PASSWORD", app_id, "bot-password"),
            ],
            "volumeMounts": [{"name": "setup", "mountPath": "/setup", "readOnly": True}],
            "resources": _resources("50m", "64Mi", "500m", "256Mi"),
            "securityContext": _restricted(),
        }
        setup_pod = _pod(
            [setup_container],
            [{"name": "setup", "configMap": {"name": name(app_id, "db-setup")}}],
            pull_secret,
        )
        setup_pod["restartPolicy"] = "OnFailure"
        docs.append({
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": _meta(app_id, "db-setup", name(app_id, "db-setup"), namespace),
            "spec": {
                "backoffLimit": 6,
                "activeDeadlineSeconds": 600,
                "ttlSecondsAfterFinished": 86400,
                "template": {"metadata": {"labels": _labels(app_id, "db-setup")}, "spec": setup_pod},
            },
        })  # fmt: skip

    else:
        docs.append(_tables_job(app_id, namespace, ext, registry, tag, pull_secret))

    # --- lab agent: front door, admin API, DB link proxy ---------------------------------------
    docs.append(_pvc(app_id, "agent", name(app_id, "agent-state"), namespace, "1Gi"))
    docs.append(_service(app_id, "agent", namespace, [("http", AGENT_PORT), ("db", AGENT_DB_PORT)]))
    probe = {"exec": {"command": ["python", "-c", _AGENT_PROBE]}, "timeoutSeconds": 6}
    agent_container = {
        "name": "agent",
        "image": image(registry, "lab-agent", tag),
        "env": [
            _secret_env("LAB_TOKEN", app_id, "lab-token"),
            _secret_env("DB_PASSWORD", app_id, "db-password"),
            {"name": "APP_ID", "value": app_id},
            {"name": "APP_UPSTREAM", "value": f"http://{name(app_id, 'app')}:{app_port}"},
            {"name": "APP_HEALTH_PATH", "value": health_path},
            {"name": "APP_VERSION", "value": tag},
            {"name": "DB_PROXY_PORT", "value": str(AGENT_DB_PORT)},
            *_agent_db_env(app_id, ext, db_host, db_url),
            {"name": "SCHEMA_PATH", "value": "/lab/schema.sql"},
            {"name": "STATE_DIR", "value": "/var/lib/twinlab"},
            {"name": "LAB_PORT", "value": str(AGENT_PORT)},
        ],
        "ports": [
            {"name": "http", "containerPort": AGENT_PORT},
            {"name": "db", "containerPort": AGENT_DB_PORT},
        ],
        "volumeMounts": [
            {"name": "schema", "mountPath": "/lab", "readOnly": True},
            {"name": "state", "mountPath": "/var/lib/twinlab"},
        ],
        "startupProbe": {**probe, "periodSeconds": 5, "failureThreshold": 24},
        "readinessProbe": {**probe, "periodSeconds": 10},
        "livenessProbe": {**probe, "periodSeconds": 20, "failureThreshold": 3},
        "resources": _resources("100m", "128Mi", "1", "512Mi"),
        "securityContext": _restricted(),
    }
    agent_volumes = [
        {"name": "schema", "configMap": {"name": name(app_id, "schema")}},
        {"name": "state", "persistentVolumeClaim": {"claimName": name(app_id, "agent-state")}},
    ]
    schema_sum = hashlib.sha256(schema.encode("utf-8")).hexdigest()[:16]
    docs.append(
        _deployment(
            app_id,
            "agent",
            namespace,
            _pod([agent_container], agent_volumes, pull_secret),
            recreate=True,  # its state volume is ReadWriteOnce
            annotations={"twinlab.io/schema-sha256": schema_sum},
        )
    )

    # --- the app -------------------------------------------------------------------------------
    app_ports = [("http", app_port)] + ([("api", health_port)] if health_port != app_port else [])
    docs.append(_service(app_id, "app", namespace, app_ports))
    http_probe = {"httpGet": {"path": health_path, "port": health_port}, "timeoutSeconds": 3}
    app_container = {
        "name": "app",
        "image": image(registry, app_id, tag),
        "env": _app_db_env(app_id, ext, app.run.env.database_url, db_url, agent_host),
        "ports": [{"name": n, "containerPort": p} for n, p in app_ports],
        "startupProbe": {**http_probe, "periodSeconds": 5, "failureThreshold": 36},
        "readinessProbe": {**http_probe, "periodSeconds": 10},
        "livenessProbe": {**http_probe, "periodSeconds": 20, "failureThreshold": 3},
        "resources": _resources("100m", "256Mi", "1", "768Mi"),
        "securityContext": _restricted(),
    }
    docs.append(
        _deployment(
            app_id, "app", namespace, _pod([app_container], None, pull_secret), recreate=False
        )
    )

    if expose_db:  # bots outside the cluster: the database link on a port of every node
        docs.append({
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": _meta(app_id, "agent", name(app_id, "db-external"), namespace),
            "spec": {"type": "NodePort", "selector": _selector(app_id, "agent"),
                     "ports": [{"name": "db", "port": AGENT_DB_PORT, "targetPort": "db"}]},
        })  # fmt: skip

    # --- the front door: HTTPS to the agent's port 9000 only (never 15432) ---------------------
    docs.append({
        "apiVersion": "route.openshift.io/v1",
        "kind": "Route",
        "metadata": _meta(
            app_id, "agent", name(app_id, "web"), namespace,
            annotations={"haproxy.router.openshift.io/timeout": "300s"},
        ),
        "spec": {
            "to": {"kind": "Service", "name": agent_host, "weight": 100},
            "port": {"targetPort": "http"},
            "tls": {"termination": "edge", "insecureEdgeTerminationPolicy": "Redirect"},
            "wildcardPolicy": "None",
        },
    })  # fmt: skip
    return docs


def conn_secret_name(app_id: str) -> str:
    return f"twinlab-{app_id}-dbconn"


def _external_server(root: Path, app):
    """The DBA's server an app uses, or None for Twin Lab's own Postgres."""
    from labctl import dbservers

    choice = dbservers.choice(app.lab.database if app.lab else None)
    if choice == "postgres":
        return None
    if choice == "sqlserver":
        raise OcpError(
            "the SQL Server container Twin Lab runs is for the local lab only. On OpenShift, use a "
            "server your DBA provides: list it under `databases:` (twinlab.local.yaml, or the "
            "TWINLAB_DATABASES secret in CI) and choose it for this app"
        )
    try:
        return dbservers.server(root, choice, need_passwords=False)
    except dbservers.DbServerError as exc:
        raise OcpError(str(exc)) from exc


def _conn_env(env: str, app_id: str, key: str) -> dict:
    return {
        "name": env,
        "valueFrom": {"secretKeyRef": {"name": conn_secret_name(app_id), "key": key}},
    }


def _agent_db_env(app_id: str, ext, db_host: str, db_url: str) -> list[dict]:
    if ext is None:
        return [
            {"name": "DB_UPSTREAM", "value": f"{db_host}:{DB_PORT}"},
            {"name": "ADMIN_DATABASE_URL", "value": db_url % f"{db_host}:{DB_PORT}"},
        ]
    env = [{"name": "DB_UPSTREAM", "value": f"{ext.host}:{ext.port}"},
           _conn_env("ADMIN_DATABASE_URL", app_id, "admin-url")]  # fmt: skip
    if ext.schema:
        env.append({"name": "DB_SCHEMA", "value": ext.schema})
    return env


def _app_db_env(app_id: str, ext, var: str, db_url: str, agent_host: str) -> list[dict]:
    if ext is None:
        return [
            _secret_env("DB_PASSWORD", app_id, "db-password"),
            {"name": var, "value": db_url % f"{agent_host}:{AGENT_DB_PORT}"},
        ]
    return [
        _conn_env(var, app_id, "app-url"),
        {"name": "DB_ENGINE", "value": ext.engine},
        {"name": "DB_HOST", "value": agent_host},
        {"name": "DB_PORT", "value": str(AGENT_DB_PORT)},
        {"name": "DB_NAME", "value": ext.database or ""},
        {"name": "DB_SERVICE", "value": ext.service or ""},
        {"name": "DB_USER", "value": ext.bot_user or ext.owner_user},
        _conn_env("DB_PASSWORD", app_id, "app-password"),
        _conn_env("DB_CONNECTION_STRING", app_id, "app-connection-string"),
    ]


def _tables_job(app_id: str, namespace: str, ext, registry: str, tag: str, pull_secret) -> dict:
    """Creates the app's tables on the DBA's server from schema.sql (views become tables).
    Runs in the lab agent's image, which has the drivers; existing tables are left alone."""
    script = (
        "from pathlib import Path; import os\n"
        "from labctl import dbsetup\n"
        "made = dbsetup.create_tables(os.environ['ADMIN_DATABASE_URL'], Path('/lab/schema.sql'), "
        "os.environ['DB_ENGINE'], os.environ.get('DB_SCHEMA') or None)\n"
        "print('created:', ', '.join(made) or 'nothing (all tables exist)')\n"
    )
    container = {
        "name": "db-tables",
        "image": image(registry, "lab-agent", tag),
        "command": ["python", "-c", script],
        "env": [
            _conn_env("ADMIN_DATABASE_URL", app_id, "admin-url"),
            {"name": "DB_ENGINE", "value": ext.engine},
            *([{"name": "DB_SCHEMA", "value": ext.schema}] if ext.schema else []),
        ],  # fmt: skip
        "volumeMounts": [{"name": "schema", "mountPath": "/lab", "readOnly": True}],
        "resources": _resources("50m", "128Mi", "500m", "512Mi"),
        "securityContext": _restricted(),
    }
    pod = _pod([container], [{"name": "schema", "configMap": {"name": name(app_id, "schema")}}],
               pull_secret)  # fmt: skip
    pod["restartPolicy"] = "OnFailure"
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": _meta(app_id, "db-setup", name(app_id, "db-setup"), namespace),
        "spec": {"backoffLimit": 4, "activeDeadlineSeconds": 600, "ttlSecondsAfterFinished": 86400,
                 "template": {"metadata": {"labels": _labels(app_id, "db-setup")}, "spec": pod}},
    }  # fmt: skip


def conn_secret_manifest(app_id: str, namespace: str, ext) -> dict:
    """The DBA server's connection details for one app (owner for Twin Lab, bot for the app)."""
    agent = name(app_id, "agent")
    bot = bool(ext.bot_user)
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": _meta(app_id, "secrets", conn_secret_name(app_id), namespace),
        "stringData": {
            "admin-url": ext.url(),
            "app-url": ext.url(bot=bot, host=agent, port=AGENT_DB_PORT),
            "app-password": ext.bot_password if bot else ext.owner_password,
            "app-connection-string": ext.connection_string(
                bot=True, host=agent, port=AGENT_DB_PORT
            ),
        },
    }


class _Dumper(yaml.SafeDumper):
    """Multi-line strings (schema.sql, scripts) as readable `|` blocks; no `&id001` anchors."""

    def ignore_aliases(self, data) -> bool:
        return True


def _str(dumper: yaml.SafeDumper, value: str):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)


def dump(doc: dict) -> str:
    return yaml.dump(doc, Dumper=_Dumper, sort_keys=False, width=100, allow_unicode=True)


def to_yaml(docs: list[dict]) -> str:
    return "---\n".join(dump(d) for d in docs)


def write(docs: list[dict], out: Path) -> list[Path]:
    """One file per object, numbered in apply order, so `oc apply -f DIR` works."""
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, doc in enumerate(docs, 1):
        path = out / f"{i:02d}-{doc['kind'].lower()}-{doc['metadata']['name']}.yaml"
        path.write_bytes(dump(doc).encode("utf-8"))
        paths.append(path)
    return paths


def secret_manifest(app_id: str, namespace: str, values: dict[str, str] | None = None) -> dict:
    """The app's Secret. Values are generated unless given; never written to disk by labctl."""
    values = values or {k: secrets.token_urlsafe(24) for k in SECRET_KEYS}
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": _meta(app_id, "secrets", secret_name(app_id), namespace),
        "stringData": {k: values[k] for k in SECRET_KEYS},
    }


# --- talking to the cluster ------------------------------------------------------------------


def _oc(
    args: list[str], *, stdin: str | None = None, check: bool = True, timeout: int = 900
) -> subprocess.CompletedProcess:
    exe = shutil.which("oc")
    if exe is None:
        raise OcpError(
            "`oc` (the OpenShift command line) is not on PATH. Install it from your cluster's "
            "web console (? > Command line tools), log in with `oc login`, and try again."
        )
    try:
        out = subprocess.run(
            [exe, *args], input=stdin, capture_output=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False,
        )  # fmt: skip
    except subprocess.TimeoutExpired as exc:
        raise OcpError(f"`oc {' '.join(args[:3])}` did not finish within {timeout} s") from exc
    out.stdout, out.stderr = out.stdout or "", out.stderr or ""
    if check and out.returncode != 0:
        raise OcpError(f"`oc {' '.join(args[:4])}` failed:\n{(out.stderr or out.stdout)[-3000:]}")
    return out


def ensure_secret(app_id: str, namespace: str, log=print) -> bool:
    """Create the app's Secret with generated values when it is missing. Never overwrites one.
    The manifest goes to `oc` on stdin, so no value appears on a command line. True if created."""
    got = _oc(["get", "secret", secret_name(app_id), "-n", namespace, "-o", "name"], check=False)
    if got.returncode == 0:
        log(f"secret {secret_name(app_id)} exists; kept as it is")
        return False
    if "NotFound" not in got.stderr and "not found" not in got.stderr:
        raise OcpError(f"cannot read secret {secret_name(app_id)}:\n{got.stderr[-2000:]}")
    _oc(
        ["create", "-n", namespace, "-f", "-"], stdin=json.dumps(secret_manifest(app_id, namespace))
    )
    log(f"secret {secret_name(app_id)} created with generated values")
    return True


def secret_value(app_id: str, namespace: str, key: str) -> str:
    out = _oc(["get", "secret", secret_name(app_id), "-n", namespace, "-o", "json"])
    data = json.loads(out.stdout).get("data") or {}
    if key not in data:
        raise OcpError(f"secret {secret_name(app_id)} has no key {key!r}")
    return base64.b64decode(data[key]).decode("utf-8")


def route_host(app_id: str, namespace: str) -> str:
    out = _oc(["get", "route", name(app_id, "web"), "-n", namespace, "-o", "json"])
    host = (json.loads(out.stdout).get("spec") or {}).get("host")
    if not host:
        raise OcpError(f"route {name(app_id, 'web')} has no host yet")
    return host


def _run_setup_job(app_id: str, namespace: str, job: dict, timeout: int) -> None:
    job_name = name(app_id, "db-setup")
    # A Job's pod template cannot change, so it is replaced: delete, create, wait.
    _oc(["delete", "job", job_name, "-n", namespace, "--ignore-not-found", "--wait=true"])
    _oc(["apply", "-n", namespace, "-f", "-"], stdin=json.dumps(job))
    done = _oc(
        ["wait", "-n", namespace, "--for=condition=complete", f"job/{job_name}",
         f"--timeout={timeout}s"],
        check=False, timeout=timeout + 60,
    )  # fmt: skip
    if done.returncode != 0:
        logs = _oc(["logs", "-n", namespace, f"job/{job_name}", "--tail=50"], check=False)
        raise OcpError(
            f"job {job_name} did not complete:\n{done.stderr[-1500:]}\n{logs.stdout[-1500:]}"
        )


def deploy(
    root: Path,
    app_id: str,
    *,
    registry: str,
    tag: str,
    namespace: str,
    postgres_image: str = POSTGRES_IMAGE,
    pull_secret: str | None = None,
    timeout: int = 600,
    smoke_check: bool = True,
    verify_tls: bool = True,
    expose_db: bool = False,
    log=print,
) -> str | None:
    """Render, create the Secret when missing, apply, wait for the rollouts, set up the database
    logins (before and after the app created its tables, as `lab.up` does) and smoke-check the
    Route. Returns the Route's URL when the smoke check ran."""
    docs = render(
        root, app_id, registry=registry, tag=tag, namespace=namespace,
        postgres_image=postgres_image, pull_secret=pull_secret, expose_db=expose_db,
    )  # fmt: skip
    ext = _external_server(root, load_app(root, app_id))
    if ext is not None:
        from labctl import dbservers

        try:  # the full settings, passwords included, only now
            ext = dbservers.server(root, load_app(root, app_id).lab.database)
        except dbservers.DbServerError as exc:
            raise OcpError(str(exc)) from exc
    _oc(["whoami"])  # a clear failure when not logged in, before anything changes
    ensure_secret(app_id, namespace, log)
    if ext is not None:  # refreshed every deploy: the DBA may have changed a password
        _oc(["apply", "-n", namespace, "-f", "-"],
            stdin=json.dumps(conn_secret_manifest(app_id, namespace, ext)))  # fmt: skip
        log(f"secret {conn_secret_name(app_id)} updated for {ext.label} at {ext.host}")
    job = next(d for d in docs if d["kind"] == "Job")
    rest = [d for d in docs if d["kind"] != "Job"]
    log(f"applying {len(rest)} objects to {namespace}")
    out = _oc(["apply", "-n", namespace, "-f", "-"], stdin=json.dumps({"apiVersion": "v1", "kind": "List", "items": rest}))  # fmt: skip
    log(out.stdout.strip())
    if ext is None:
        _oc(["rollout", "status", f"deployment/{name(app_id, 'db')}", "-n", namespace,
             f"--timeout={timeout}s"], timeout=timeout + 60)  # fmt: skip
        log("database ready; setting up its logins")
    else:
        log(f"creating the app's tables on {ext.label} where missing")
    _run_setup_job(app_id, namespace, job, timeout)
    for component in ("agent", "app"):
        _oc(["rollout", "status", f"deployment/{name(app_id, component)}", "-n", namespace,
             f"--timeout={timeout}s"], timeout=timeout + 60)  # fmt: skip
        log(f"{name(app_id, component)} rolled out")
    if ext is None:
        _run_setup_job(app_id, namespace, job, timeout)  # grants on the tables the app created
        log("database grants refreshed")
    if not smoke_check:
        return None
    return smoke(root, app_id, namespace=namespace, verify_tls=verify_tls, log=log)


def smoke(
    root: Path,
    app_id: str,
    *,
    namespace: str,
    verify_tls: bool = True,
    wait: float = 180,
    baseline: bool = True,
    log=print,
) -> str:
    """Through the Route: the agent's health (app and db up), the app's own health path, and, the
    first time, the app's starting data recorded as the baseline (as `lab.up` does)."""
    app = load_app(root, app_id)
    health_path = app.run.api.health if app.run.api else "/"
    url = f"https://{route_host(app_id, namespace)}"
    token = secret_value(app_id, namespace, "lab-token")
    headers = {"X-Lab-Token": token}
    deadline = time.monotonic() + wait
    last = ""
    with httpx.Client(base_url=url, verify=verify_tls, timeout=15) as http:
        while True:
            try:
                r = http.get("/_lab/health", headers=headers)
                last = f"{r.status_code} {r.text[:200]}"
                if r.status_code == 200:
                    break
            except httpx.HTTPError as exc:
                last = f"{exc.__class__.__name__}: {exc}"
            if time.monotonic() > deadline:
                raise OcpError(f"smoke check failed: {url}/_lab/health -> {last}")
            time.sleep(5)
        log(f"{url}/_lab/health -> 200 {r.text.strip()[:200]}")
        r = http.get(health_path)
        if r.status_code >= 400:
            raise OcpError(f"smoke check failed: {url}{health_path} -> {r.status_code}")
        log(f"{url}{health_path} -> {r.status_code}")
        if baseline:
            info = http.get("/_lab/info", headers=headers)
            if info.status_code == 200 and not (info.json() or {}).get("baseline"):
                made = http.post("/_lab/data/baseline", headers=headers, json={})
                log(f"baseline of the starting data: {made.status_code}")
    return url
