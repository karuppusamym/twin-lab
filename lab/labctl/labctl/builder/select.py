"""Choose the builder: `--builder`, else TWINLAB_BUILDER, else `builder:` in twinlab.local.yaml,
else `manual`."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from labctl.builder import package as pkg
from labctl.builder.base import Builder, BuilderError
from labctl.builder.claude import ClaudeBuilder
from labctl.builder.config import NAMES, BuilderConfig, load_config
from labctl.builder.devin import DevinBuilder
from labctl.builder.manual import ManualBuilder


def select(
    repo_root: Path,
    name: str | None = None,
    *,
    config: BuilderConfig | None = None,
    env: Mapping[str, str] | None = None,
    open_pr: bool = False,
    detach: bool = False,
) -> Builder:
    cfg = config or load_config(repo_root, env)
    chosen = (name or cfg.name).strip().lower()
    state = pkg.default_state_dir(repo_root)
    if chosen == "manual":
        return ManualBuilder(state)
    if chosen == "claude":
        return ClaudeBuilder(cfg, state, open_pr=open_pr, detach=detach)
    if chosen == "devin":
        return DevinBuilder(repo_root)
    raise BuilderError(f"unknown builder {chosen!r}; choose one of {', '.join(NAMES)}")
