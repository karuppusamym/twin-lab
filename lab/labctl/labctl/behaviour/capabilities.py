"""What the recorder and fidelity replay can do, each claim with the fixture test that proves it
(replication gap R12). Docs and the portal render this table; nothing is advertised without a
passing local fixture. A surface that is not supported says so, with what the replay reports.

`TABLE` rows: `id`, `capability`, `status` (supported | partial | unsupported), `test` (the
pytest node that proves the row: for an unsupported surface, the test that proves it is reported
as such, never silently skipped), and `note`. `rows()` returns them as dicts, `markdown()` as a
table, `test_names()` the test functions a check can confirm exist.
"""

from __future__ import annotations

SUPPORTED, PARTIAL, UNSUPPORTED = "supported", "partial", "unsupported"
MODULE = "lab/labctl/tests/test_capability_fixtures.py"
MULTI = "lab/labctl/tests/test_replay_across_apps.py"

TABLE: list[dict] = [
    {"id": "same-origin-iframe", "capability": "Actions inside a same-origin iframe",
     "status": SUPPORTED, "test": f"{MODULE}::test_same_origin_iframe",
     "note": "the step's selector names the frame (`iframe#x >>> button#y`)"},
    {"id": "cross-origin-iframe", "capability": "Actions inside a cross-origin iframe",
     "status": SUPPORTED, "test": f"{MODULE}::test_cross_origin_iframe",
     "note": "the frame's origin must be mapped to a replica or declared excluded"},
    {"id": "open-shadow-dom", "capability": "Open shadow DOM",
     "status": SUPPORTED, "test": f"{MODULE}::test_open_shadow_dom",
     "note": "ranked candidates pierce open shadow roots (role, placeholder, text, structure)"},
    {"id": "closed-shadow-dom", "capability": "Closed shadow DOM",
     "status": UNSUPPORTED, "test": f"{MODULE}::test_closed_shadow_dom_is_reported",
     "note": "captured and shown in snapshots; replay BLOCKS: no selector reaches a closed root"},
    {"id": "file-upload", "capability": "File upload",
     "status": SUPPORTED, "test": f"{MODULE}::test_file_upload",
     "note": "the recording keeps name shape, type and size class, never the file; replay "
             "uploads the synthetic file the bindings name (`file:`)"},
    {"id": "file-download", "capability": "File download",
     "status": SUPPORTED, "test": f"{MODULE}::test_file_download",
     "note": "a `download` step (file name shape and type); replay asserts a download happened"},
    {"id": "popup", "capability": "Popup / new window (window.open, target=_blank)",
     "status": SUPPORTED, "test": f"{MODULE}::test_popup_window",
     "note": "tab-open/switch/close are performed; the new page is matched to the recorded tab"},
    {"id": "dialogs", "capability": "confirm / alert / prompt dialogs",
     "status": SUPPORTED, "test": f"{MODULE}::test_dialogs",
     "note": "the recorded choice is replayed; prompt text is a typed value: bound, never stored"},
    {"id": "async-content", "capability": "Content after a delayed response",
     "status": SUPPORTED, "test": f"{MODULE}::test_async_content",
     "note": "replay waits for the network to settle (no request in flight), not a fixed sleep"},
    {"id": "select-by-index", "capability": "<select> chosen by index binding",
     "status": SUPPORTED, "test": f"{MODULE}::test_select_by_index",
     "note": "bindings `index: N` chooses the Nth option"},
    {"id": "slow-navigation", "capability": "Navigation slower than the settle time; "
     "POST-redirect-GET; Back/Forward",
     "status": SUPPORTED, "test": f"{MODULE}::test_slow_post_redirect_and_history",
     "note": "the causing step is observed at the page it loaded (`after: navigation`)"},
    {"id": "drag-and-drop", "capability": "Drag and drop",
     "status": UNSUPPORTED, "test": f"{MODULE}::test_drag_and_drop_is_reported",
     "note": "captured as a `drag` step; replay BLOCKS with an unsupported-surface message"},
    {"id": "canvas", "capability": "Canvas-only interaction",
     "status": UNSUPPORTED, "test": f"{MODULE}::test_canvas_interaction_is_reported",
     "note": "a click on a canvas has no element to compare: replay BLOCKS, never claims it"},
    {"id": "service-worker", "capability": "Service worker",
     "status": PARTIAL, "test": f"{MODULE}::test_service_worker_is_classified",
     "note": "the page's own steps replay; the worker is blocked in replay and its behaviour "
             "(offline cache, push) is listed as unsupported in the report"},
    {"id": "other-engine", "capability": "A second browser engine (Firefox, WebKit)",
     "status": PARTIAL, "test": f"{MODULE}::test_other_browser_engines",
     "note": "runs on each engine installed locally; the report says which ran"},
    {"id": "multi-app-replay", "capability": "One replay across applications and tabs",
     "status": SUPPORTED, "test": f"{MULTI}::test_the_unsplit_recording_passes_within_declared_scope",
     "note": "an origin map sends each application's steps to its replica; no request reaches "
             "a source or an unmapped origin"},
    {"id": "desktop-citrix", "capability": "Desktop, Citrix and other non-DOM applications",
     "status": UNSUPPORTED, "test": f"{MODULE}::test_canvas_interaction_is_reported",
     "note": "never advertised as covered by DOM replay"},
]  # fmt: skip


def rows() -> list[dict]:
    return [dict(r) for r in TABLE]


def by_id(cap_id: str) -> dict | None:
    return next((dict(r) for r in TABLE if r["id"] == cap_id), None)


def test_names() -> list[tuple[str, str]]:
    """(test file, test function) of every row."""
    out = []
    for r in TABLE:
        path, _, name = r["test"].partition("::")
        out.append((path, name))
    return out


def markdown() -> str:
    lines = ["| Capability | Status | Proved by | Note |", "|---|---|---|---|"]
    for r in TABLE:
        lines.append(f"| {r['capability']} | {r['status']} | `{r['test'].split('/')[-1]}` "
                     f"| {r['note']} |")  # fmt: skip
    return "\n".join(lines) + "\n"
