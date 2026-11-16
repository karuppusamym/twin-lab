"""Case triage agent: the agentic counterpart of the UiPath case update (T-17).

Where the UiPath bot is told the category, subcategory and status, this agent works them out. It
reads the case, looks at the categories the application offers, decides which fits the case
title, explains its reasoning in a note, and updates the case. Its policy is a small set of
keyword rules, a stand-in for a language model, so every run is repeatable and needs no
external service. It only uses the application's API.

Bindings (ATS_BINDINGS: a path to a JSON file, or the JSON itself):
    {"apps": {"showcase": {"api": "http://...", "credentials": {"username": "...", "password": "..."}}},
     "inputs": {"in_CaseNo": "C-1002"},
     "output_path": "result.json"}                                   # optional; else stdout

Run:  ATS_BINDINGS=bindings.json python agent.py
"""

from __future__ import annotations

import json
import os
import sys

import httpx

APP = "showcase"
# keyword in the case title -> (category code, subcategory code); first match wins
POLICY = [
    ("declined", "CARDS", "CARD_DECLINE"),
    ("stolen", "CARDS", "CARD_LOST"),
    ("duplicate", "PAYMENTS", "PAY_DUP"),
    ("refund", "PAYMENTS", "PAY_REFUND"),
    ("pending", "PAYMENTS", "PAY_DELAY"),
    ("transfer", "PAYMENTS", "PAY_DELAY"),
    ("sign in", "ACCESS", "ACC_LOGIN"),
    ("locked", "ACCESS", "ACC_LOCK"),
    ("address", "PROFILE", "PRO_ADDR"),
    ("name", "PROFILE", "PRO_NAME"),
]


def load_bindings() -> dict:
    raw = os.environ.get("ATS_BINDINGS")
    if not raw:
        raise SystemExit("ATS_BINDINGS is not set (path to the bindings JSON, or the JSON itself)")
    if raw.lstrip().startswith("{"):
        return json.loads(raw)
    with open(raw, encoding="utf-8-sig") as fh:
        return json.load(fh)


def decide(title: str) -> tuple[str, str, str] | None:
    """(category code, subcategory code, why) for a case title, or None when nothing fits."""
    text = title.lower()
    for word, cat, sub in POLICY:
        if word in text:
            return cat, sub, f"the title mentions '{word}'"
    return None


def run(api: str, username: str, password: str, case_no: str) -> dict:
    with httpx.Client(base_url=api, timeout=60) as http:
        r = http.post("/api/login", json={"username": username, "password": password})
        r.raise_for_status()
        http.headers["Authorization"] = f"Bearer {r.json()['token']}"

        case = http.get(f"/api/cases/{case_no}")
        case.raise_for_status()
        current = case.json()
        title, status = current["title"], current["status"]
        if status in ("CLOSED", "RESOLVED"):
            return {"status": "SKIPPED", "case_no": case_no, "why": f"the case is {status}"}

        choice = decide(title)
        if choice is None:
            return {"status": "NEEDS_PERSON", "case_no": case_no, "why": "no rule fits the title"}
        cat_code, sub_code, why = choice
        categories = http.get("/api/categories")
        categories.raise_for_status()
        category = next(c for c in categories.json() if c["code"] == cat_code)
        subs = http.get(f"/api/categories/{category['id']}/subcategories")
        subs.raise_for_status()
        sub = next(s for s in subs.json() if s["code"] == sub_code)

        note = f"Triage agent: {category['name']} / {sub['name']}, because {why}."
        added = http.post(f"/api/cases/{case_no}/notes", json={"text": note})
        added.raise_for_status()
        updated = http.put(f"/api/cases/{case_no}", json={
            "status": "IN_PROGRESS", "category_id": category["id"], "subcategory_id": sub["id"],
        })  # fmt: skip
        updated.raise_for_status()
        return {
            "status": "DONE",
            "case_no": case_no,
            "category": updated.json()["category"],
            "subcategory": updated.json()["subcategory"],
            "case_status": updated.json()["status"],
            "why": why,
        }


def main() -> int:
    bindings = load_bindings()
    app = bindings["apps"][APP]
    creds = app.get("credentials") or {}
    inputs = bindings.get("inputs") or {}
    case_no = inputs.get("in_CaseNo") or inputs.get("case_no")
    if not case_no:
        raise SystemExit("give inputs.in_CaseNo")
    result = run(app.get("api") or app["web"], creds["username"], creds["password"], case_no)
    text = json.dumps(result, indent=2)
    if bindings.get("output_path"):
        with open(bindings["output_path"], "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    return 0 if result["status"] in ("DONE", "SKIPPED") else 1


if __name__ == "__main__":
    sys.exit(main())
