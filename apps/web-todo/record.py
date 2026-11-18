"""Re-record the public demo using Twin Lab, with only synthetic task data.
Run from the repository root with PYTHONPATH=lab/labctl.
"""

from pathlib import Path

from verify import HERE, exercise

from labctl.recorder import record

if __name__ == "__main__":
    (HERE / "validation").mkdir(exist_ok=True)
    record(
        "https://demo.playwright.dev/todomvc/",
        Path("evidence/web-todo/full-flow"),
        script=lambda page: exercise(page, "https://demo.playwright.dev/todomvc/"),
        headless=True,
    )
