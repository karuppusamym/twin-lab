"""Settings, read once from the environment. See README.md for each variable."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _host_port(value: str, default_port: int) -> tuple[str, int]:
    value = value.strip()
    if value.startswith("["):  # [::1]:5432
        host, _, rest = value[1:].partition("]")
        port = rest.lstrip(":")
        return host, int(port) if port else default_port
    host, sep, port = value.rpartition(":")
    if not sep:
        return value, default_port
    return host, int(port)


@dataclass(frozen=True)
class Settings:
    app_id: str
    app_upstream: str
    state_dir: Path
    lab_port: int = 9000
    db_upstream: str | None = None
    db_proxy_port: int = 15432
    admin_database_url: str | None = None
    schema_path: Path | None = None
    lab_token: str | None = None
    app_version: str = "dev"
    app_health_path: str = "/"
    db_schema: str = "public"
    upstream_timeout_s: float = 300.0

    @property
    def db_upstream_addr(self) -> tuple[str, int] | None:
        if not self.db_upstream:
            return None
        return _host_port(self.db_upstream, 5432)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if env is None else env)
        missing = [k for k in ("APP_ID", "APP_UPSTREAM", "STATE_DIR") if not env.get(k)]
        if missing:
            raise SystemExit(f"lab-agent: missing environment variable(s): {', '.join(missing)}")
        app_id = env["APP_ID"]
        if not SLUG.match(app_id):
            raise SystemExit(f"lab-agent: APP_ID must be a slug (a-z, 0-9, '-'), not {app_id!r}")
        schema = env.get("SCHEMA_PATH")
        return cls(
            app_id=app_id,
            app_upstream=env["APP_UPSTREAM"].rstrip("/"),
            state_dir=Path(env["STATE_DIR"]),
            lab_port=int(env.get("LAB_PORT") or 9000),
            db_upstream=env.get("DB_UPSTREAM") or None,
            db_proxy_port=int(env.get("DB_PROXY_PORT") or 15432),
            admin_database_url=env.get("ADMIN_DATABASE_URL") or None,
            schema_path=Path(schema) if schema else None,
            lab_token=env.get("LAB_TOKEN") or None,
            app_version=env.get("APP_VERSION") or "dev",
            app_health_path=env.get("APP_HEALTH_PATH") or "/",
            db_schema=env.get("DB_SCHEMA") or "public",
            upstream_timeout_s=float(env.get("UPSTREAM_TIMEOUT_S") or 300),
        )
