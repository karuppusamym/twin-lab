"""The Devin package. The logic is builder-neutral and lives in `labctl.builder.package`; this
module keeps the old import path (`labctl.devin.package`) working for the Devin code and its
tests. The task file in the zip is now `BUILD_TASK.md`: the Devin API path sends the same text as
the session prompt, so it needs no file of its own.
"""

from labctl.builder.package import *  # noqa: F403
from labctl.builder.package import (  # noqa: F401
    FORMAT,
    MANIFEST_FILE,
    SCREENSHOT_SUFFIXES,
    TASK_FILE,
    TEXT_SUFFIXES,
    PackageError,
    _app_yaml,
    _check_inputs,
    _collect,
    _content,
    _db_example,
    _run_env,
    brief_digest,
    build,
    excluded,
    normalize_repo,
    prompt,
    read_manifest,
)
