"""Make lab_agent and labctl importable when the tests run from the repository root."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = HERE.parent
REPO = AGENT.parents[1]
for path in (AGENT, REPO / "lab" / "labctl"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def pytest_configure(config):
    config.addinivalue_line("markers", "docker: needs a working Docker engine (skipped otherwise)")
