"""Builder settings: environment first, then the `builder:` block of `twinlab.local.yaml`.

    builder:
      name: claude            # manual (default) | claude | devin
      claude:
        executable: claude    # a path, or a command; may be an absolute path to claude.cmd
        model: null           # unset: the CLI's own default applies
        args: []              # extra arguments, placed before -p

`builder: claude` (a bare string) is the short form. Environment: TWINLAB_BUILDER,
TWINLAB_CLAUDE_MODEL, TWINLAB_CLAUDE_EXECUTABLE. No secrets belong here: Claude Code keeps its own
login.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

NAMES = ("manual", "claude", "devin")
LOCAL_FILE = "twinlab.local.yaml"


class BuilderConfigError(Exception):
    pass


@dataclass(frozen=True)
class BuilderConfig:
    name: str = "manual"
    claude_executable: str = "claude"
    claude_model: str | None = None
    claude_args: tuple[str, ...] = ()
    sources: dict[str, str] = field(default_factory=dict)


def _block(root: Path) -> dict:
    path = Path(root) / LOCAL_FILE
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise BuilderConfigError(f"{path}: cannot read: {exc}") from exc
    if not isinstance(data, dict):
        return {}
    value = next((v for k, v in data.items() if str(k).lower() == "builder"), None)
    if isinstance(value, str):
        return {"name": value}
    return value if isinstance(value, dict) else {}


def load_config(repo_root: Path, env: Mapping[str, str] | None = None) -> BuilderConfig:
    env = os.environ if env is None else env
    block = _block(repo_root)
    claude = block.get("claude") if isinstance(block.get("claude"), dict) else {}
    sources: dict[str, str] = {}

    def pick(key: str, env_key: str | None, file_value, default):
        if env_key and (env.get(env_key) or "").strip():
            sources[key] = "environment"
            return env[env_key].strip()
        if file_value not in (None, ""):
            sources[key] = LOCAL_FILE
            return file_value
        return default

    name = str(pick("name", "TWINLAB_BUILDER", block.get("name"), "manual")).strip().lower()
    if name not in NAMES:
        raise BuilderConfigError(f"unknown builder {name!r}; choose one of {', '.join(NAMES)}")
    args = claude.get("args") or []
    if not isinstance(args, list):
        raise BuilderConfigError("builder.claude.args must be a list of strings")
    model = pick("claude_model", "TWINLAB_CLAUDE_MODEL", claude.get("model"), None)
    return BuilderConfig(
        name=name,
        claude_executable=str(
            pick(
                "claude_executable", "TWINLAB_CLAUDE_EXECUTABLE", claude.get("executable"), "claude"
            )
        ),
        claude_model=str(model) if model else None,
        claude_args=tuple(str(a) for a in args),
        sources=sources,
    )
