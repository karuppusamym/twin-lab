"""Bindings: what an automation is told about the lab apps it runs against.

`{{showcase.web}}`, `{{showcase.db}}` or `{{showcase.credentials.username}}` in an automation's
`bindings:` resolve to the running lab's addresses and the app's test credentials.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from labctl import repo
from labctl.models import App, AppBinding, Automation, Bindings

TEMPLATE = re.compile(r"\{\{\s*([\w-]+)((?:\.[\w-]+)+)\s*\}\}")
LAB_HOST = os.environ.get("TWINLAB_HOST", "localhost")


class BindingError(Exception):
    pass


def load_app(root: Path, app_id: str) -> App:
    path = root / "apps" / app_id / "app.yaml"
    model, problems = repo.load(path, "app")
    if model is None:
        raise BindingError(f"apps/{app_id}/app.yaml: " + "; ".join(str(p) for p in problems))
    return model


def app_binding(app: App, host: str = LAB_HOST) -> AppBinding:
    if app.lab is None:
        raise BindingError(f"apps/{app.id}/app.yaml has no `lab:` section (port, db_port)")
    base = f"http://{host}:{app.lab.port}"
    return AppBinding(
        web=base,
        api=base,
        db=f"postgresql://lab:lab@{host}:{app.lab.db_port}/{app.id}",
        credentials=dict(app.lab.credentials),
    )


def for_automation(root: Path, automation: Automation, host: str = LAB_HOST) -> Bindings:
    apps = {}
    for app_id in automation.apps():
        apps[app_id] = app_binding(load_app(root, app_id), host)
    return Bindings(apps=apps, inputs=dict(automation.test_inputs))


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
