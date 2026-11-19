"""Pull-request guard: an AI builder (Devin, Copilot) cannot approve its own work or move the goal.

Rules (AGENTS.md states them; this makes CI enforce them):

1. No self-approval, on every pull request. No rule in `apps/<app>/app.yaml` may be raised to
   `owner_approved` or `reference_verified` unless the pull request has the `owner-approved`
   label, added by the application owner after their review.
2. Inputs stay as generated, on AI-built pull requests (branch `devin/...` or `build/...`, or label `ai-built`).
   The builder may not edit what the factory generated from the evidence: `BRIEF.md`,
   `model.yaml`, `db/schema.sql`, `db/seed.sql` (and `db/seed.json`: the seed the acceptance
   replay depends on), `behaviours.json`, `rules.json` (the field rules), `pages/`, `look/`,
   `scripts/` (the application's scripts, carried on the owner's request), or anything in
   `app.yaml` outside its `run:` section.
3. Independent oracle, on AI-built pull requests. The checks that judge the app (`automations/`,
   `flows/`, `lab/`, `conditions/`) may not change in the same pull request as an app. Otherwise
   the same author can make both wrong in the same way and every check still passes. Override with
   the `oracle-reviewed` label after a person has read the change.

Usage:  python tools/guard_ai_changes.py <base-ref>        (e.g. origin/main)
Labels come from TWINLAB_PR_LABELS (comma separated), the branch from TWINLAB_PR_BRANCH.
Exit 0 = OK, 1 = a rule is broken, 2 = usage error.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import yaml

APPROVED = {"owner_approved", "reference_verified"}
GENERATED = ("BRIEF.md", "model.yaml", "db/schema.sql", "db/seed.sql", "db/seed.json",
             "behaviours.json", "codemap.json", "rules.json")  # fmt: skip
GENERATED_DIRS = ("pages/", "look/", "scripts/")
ORACLES = ("automations/", "flows/", "lab/", "conditions/")
APP_FILE = re.compile(r"^apps/([^/]+)/(.+)$")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, encoding="utf-8", errors="replace"
    ).stdout


def show(ref: str, path: str) -> str | None:
    try:
        return git("show", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        return None


def load(text: str | None) -> dict:
    if not text:
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def levels(app: dict) -> dict[str, str]:
    return {
        str(r.get("id")): str(r.get("verification", "hypothesis"))
        for r in app.get("rules") or []
        if isinstance(r, dict)
    }


def check(changed: list[str], read_old, read_new, labels: set[str], ai_built: bool) -> list[str]:
    """The problems with a change. `read_old/read_new(path)` give a file's text before and after."""
    problems: list[str] = []
    apps_touched = sorted({m.group(1) for p in changed if (m := APP_FILE.match(p))})

    for path in changed:
        m = APP_FILE.match(path)
        if not m or m.group(2) != "app.yaml":
            continue
        old, new = load(read_old(path)), load(read_new(path))
        if "owner-approved" not in labels:
            before = levels(old)
            for rule, level in levels(new).items():
                if level in APPROVED and before.get(rule) != level:
                    problems.append(
                        f"{path}: rule {rule} raised to {level}. Only the application owner may "
                        "approve a rule (they add the 'owner-approved' label after reviewing it)."
                    )
        if ai_built:
            outside = sorted(
                k for k in set(old) | set(new) if k != "run" and old.get(k) != new.get(k)
            )
            if old and outside:
                problems.append(
                    f"{path}: changed outside `run:` ({', '.join(outside)}). Those parts come "
                    "from the brief; regenerate the brief instead of editing them."
                )

    if ai_built:
        for path in changed:
            m = APP_FILE.match(path)
            if m and (m.group(2) in GENERATED or m.group(2).startswith(GENERATED_DIRS)):
                problems.append(
                    f"{path}: generated from the evidence; the builder may not edit it. If it is "
                    "wrong, say so in BUILD-NOTES.md."
                )
        oracles = sorted(p for p in changed if p.startswith(ORACLES))
        if apps_touched and oracles and "oracle-reviewed" not in labels:
            problems.append(
                f"This change edits app(s) {', '.join(apps_touched)} and the checks that judge "
                f"them ({', '.join(oracles[:3])}{' ...' if len(oracles) > 3 else ''}) together. "
                "Split it, or have a person read the checks and add the 'oracle-reviewed' label."
            )
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    base = git("merge-base", argv[1], "HEAD").strip()
    changed = [p for p in git("diff", "--name-only", base, "HEAD").splitlines() if p.strip()]
    labels = {x.strip() for x in os.environ.get("TWINLAB_PR_LABELS", "").split(",") if x.strip()}
    branch = os.environ.get("TWINLAB_PR_BRANCH", "")
    ai_built = branch.startswith(("devin/", "build/")) or "ai-built" in labels
    problems = check(changed, lambda p: show(base, p), lambda p: show("HEAD", p), labels, ai_built)
    kind = "AI-built" if ai_built else "person-made"
    print(f"guard: {len(changed)} file(s) changed since {base[:10]}; {kind}; labels: "
          f"{sorted(labels) or '-'}")  # fmt: skip
    for p in problems:
        print("FAIL", p)
    if not problems:
        print("OK   no self-approved rules" + ("; inputs and checks untouched" if ai_built else ""))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
