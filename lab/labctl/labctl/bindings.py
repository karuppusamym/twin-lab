"""Bindings: what an automation is told about the lab apps it runs against.

`{{showcase.web}}`, `{{showcase.db}}` or `{{showcase.credentials.username}}` in an automation's
`bindings:` resolve to the running lab's addresses and the app's test credentials.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from labctl import repo
from labctl.models import App, AppBinding, Automation, Bindings

TEMPLATE = re.compile(r"\{\{\s*([\w-]+)((?:\.[\w-]+)+)\s*\}\}")
# 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 ::1 first, the lab listens on IPv4
# only, and each connection then waits ~20 s before falling back. Bots would look slow for no reason.
LAB_HOST = "127.0.0.1"


class BindingError(Exception):
    pass


def split_lab_id(lab_id: str) -> tuple[str, str | None]:
    """`showcase` is the app's own lab; `showcase.run-3` a copy of it (labctl lab copy)."""
    base, _, copy = lab_id.partition(".")
    return base, copy or None


def load_app(root: Path, app_id: str) -> App:
    """The app, by app id or lab id. For a copy the `lab:` ports are the copy's own."""
    base, copy = split_lab_id(app_id)
    path = root / "apps" / base / "app.yaml"
    model, problems = repo.load(path, "app")
    if model is None:
        raise BindingError(f"apps/{base}/app.yaml: " + "; ".join(str(p) for p in problems))
    if copy:
        from labctl import copies

        model = copies.apply(root, model, copy)
    return model


def app_binding(
    app: App, host: str = LAB_HOST, db_password: str = "lab", server=None
) -> AppBinding:
    """Addresses a bot uses. The database is reached through the lab agent's database port (so
    slowdowns apply), with the bot's login: `bot` (rows only) for Twin Lab's own databases, the
    bot account the DBA gave for a server of theirs. `server` is that server, when not Postgres."""
    if app.lab is None:
        raise BindingError(f"apps/{app.id}/app.yaml has no `lab:` section (port, db_port)")
    base = f"http://{host}:{app.lab.port}"
    if server is not None:
        return AppBinding(
            web=base,
            api=base,
            http=base,
            db=server.url(bot=True, host=host, port=app.lab.db_port),
            db_engine=server.engine,
            db_connection_string=server.connection_string(
                bot=True, host=host, port=app.lab.db_port
            ),
            credentials=dict(app.lab.credentials),
        )
    return AppBinding(
        web=base,
        api=base,
        http=base,
        db=f"postgresql://bot:{db_password}@{host}:{app.lab.db_port}/{app.id}",
        db_engine="postgresql",
        db_connection_string=(
            f"Host={host};Port={app.lab.db_port};Database={app.id};"
            f"Username=bot;Password={db_password}"
        ),  # fmt: skip
        credentials=dict(app.lab.credentials),
    )


def sqlite_binding(app: App, host: str = LAB_HOST) -> AppBinding:
    """An app on SQLite: its file is inside the lab, reached by no network address, so a bot
    gets the web addresses and `db_engine: sqlite` but no database URL."""
    if app.lab is None:
        raise BindingError(f"apps/{app.id}/app.yaml has no `lab:` section (port)")
    base = f"http://{host}:{app.lab.port}"
    return AppBinding(web=base, api=base, http=base, db=None, db_engine="sqlite",
                      credentials=dict(app.lab.credentials))  # fmt: skip


def for_automation(
    root: Path, automation: Automation, host: str | None = None, labs: dict | None = None
) -> Bindings:
    """Bindings for a bot. `host` defaults to the remembered TWINLAB_HOST (for bots on other
    machines), else 127.0.0.1. `labs` maps an app to the lab to use instead of its own (a copy:
    `{"showcase": "showcase.run-3"}`); the bindings keep the app's name."""
    from labctl import dbservers, lab

    host = host or lab.setting(root, "host")
    labs = labs or {}
    apps = {}
    for app_id in automation.apps():
        app = load_app(root, labs.get(app_id, app_id))
        choice = dbservers.choice(app.lab.database if app.lab else None)
        if choice == "sqlite":
            apps[app_id] = sqlite_binding(app, host)
            continue
        if app.lab is not None and app.lab.db_port is None:
            raise BindingError(f"apps/{app.id}/app.yaml: `lab.db_port` is needed for {choice}")
        server = None
        if choice != "postgres":
            try:
                server = lab.lab_server(root, app_id, choice)
            except lab.LabError as exc:
                raise BindingError(str(exc)) from exc
        apps[app_id] = app_binding(app, host, lab.bot_password(root), server)
    out = Bindings(apps=apps, inputs=dict(automation.test_inputs), output_path="result.json")
    try:  # resolved here too, for ATS-style automations that read `arguments` from the file
        out.arguments = arguments(automation, out)
    except BindingError:
        pass  # an unknown reference is reported where the arguments are used
    return out


def resolve(text: str, bindings: Bindings) -> str:
    """Replace every `{{app.part...}}` with its value; unknown references are an error."""

    def one(match: re.Match) -> str:
        app_id, rest = match.group(1), match.group(2).lstrip(".").split(".")
        if app_id == "inputs":
            if rest[0] not in bindings.inputs:
                raise BindingError(f"no test input named {rest[0]!r}")
            return bindings.inputs[rest[0]]
        binding = bindings.apps.get(app_id)
        if binding is None:
            raise BindingError(f"no app {app_id!r} in the bindings")
        value: object = binding.model_dump()
        for part in rest:
            if not isinstance(value, dict) or part not in value:
                raise BindingError(f"{{{{{app_id}.{'.'.join(rest)}}}}} has no value")
            value = value[part]
        return str(value)

    return TEMPLATE.sub(one, text)


def arguments(automation: Automation, bindings: Bindings) -> dict[str, str]:
    """The automation's arguments with every template resolved."""
    return {name: resolve(value, bindings) for name, value in automation.bindings.items()}


def render(automation: Automation, bindings: Bindings, form: str) -> str:
    """Bindings as a file to hand over: json (the ATS_BINDINGS contract), env, or uipath (Config)."""
    args = arguments(automation, bindings)
    if form == "json":
        return bindings.model_dump_json(indent=2, exclude_none=True)
    if form == "env":
        lines = [f"{k}={v}" for k, v in {**args, **automation.test_inputs}.items()]
        return "\n".join(lines) + "\n"
    if form == "uipath":
        # UiPath arguments as a JSON object, ready for an Orchestrator job's input arguments
        import json

        return json.dumps({**args, **automation.test_inputs}, indent=2)
    if form == "yaml":
        return yaml.safe_dump(
            {"arguments": args, "inputs": automation.test_inputs}, sort_keys=False
        )
    raise BindingError(f"unknown form {form!r}: json, env, uipath or yaml")
