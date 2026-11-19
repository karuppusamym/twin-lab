"""Drafts name steps in one numbering (found judging Demoblaze): the whole recording's drafted
acceptance cases had `after:` in the whole recording's numbers but ids and notes in the split
part's ("rows-before-12 ... when step 12 acted on one" where the acting step is 13). And a
bindings file now says where to check a record a click acted on: its `before` snapshot and the
next recorder picture (a click has no picture of its own: `screens/0013.png` does not exist).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import yaml

from labctl import seedplan
from labctl.behaviour import bindings, drafts

CARDS = ('<div id="cards" class="row"><div class="col"><div class="card"><a class="t">Amber lamp s6'
         '</a><h5>$360</h5><p>A lamp</p></div></div><div class="col"><div class="card"><a '
         'class="t">Birch stool 1520</a><h5>$820</h5><p>A stool</p></div></div></div>')  # fmt: skip
CART = "<table><tbody id='rows'><tr class='r'><td>a</td></tr><tr class='r'><td>b</td></tr></tbody></table>"
CART1 = "<table><tbody id='rows'><tr class='r'><td>a</td></tr></tbody></table>"

# the part's steps (its own numbers) and the whole recording's: a setup step comes first there
RAW = [
    {"seq": 11, "global_seq": 12, "action": "goto", "dom": "d11"},
    {"seq": 12, "global_seq": 13, "action": "click",
     "locators": [{"kind": "record", "rows": "#cards > div.col"}]},
    {"seq": 13, "global_seq": 14, "action": "goto", "caused_by": 12, "dom": "d13"},
    {"seq": 25, "global_seq": 26, "action": "goto", "dom": "d25"},
    {"seq": 26, "global_seq": 27, "action": "click",
     "locators": [{"kind": "record", "rows": "#rows > tr.r"}]},
    {"seq": 28, "global_seq": 29, "action": "goto", "nav": "reload", "dom": "d28"},
]  # fmt: skip


def _part():
    return SimpleNamespace(raw=RAW, befores={12: CARDS, 26: CART},
                           doms={13: "<h2 class='name'>Birch stool 1520</h2>", 28: CART1},
                           html_at=lambda seq: None)  # fmt: skip


def test_the_whole_recordings_cases_use_its_numbers_in_ids_notes_and_after(tmp_path, monkeypatch):
    root = tmp_path
    (root / "apps/shop/steps").mkdir(parents=True)
    (root / "apps/shop/steps" / f"rec{seedplan.VALUES}").write_text(
        yaml.safe_dump({"records": [{"n": 12, "record": "Birch stool 1520"}]}), "utf-8"
    )
    part = root / "evidence/shop/rec"
    part.mkdir(parents=True)
    (part / "steps.jsonl").write_text("".join(json.dumps(s) + "\n" for s in RAW), "utf-8")
    monkeypatch.setattr(seedplan, "load_part", lambda app, p: _part())
    monkeypatch.setattr(seedplan, "dialog_texts", lambda p: {})
    whole = seedplan.acceptance_whole(root, root / "rec")
    by = {c["id"]: c for c in whole["cases"]}
    assert set(by) == {"shop-rows-before-13", "shop-record-shown-13", "shop-rows-before-27",
                       "shop-rows-after-27"}  # fmt: skip
    assert by["shop-rows-before-13"]["after"] == 12
    assert "when step 13 acted on one" in by["shop-rows-before-13"]["note"]
    assert by["shop-record-shown-13"]["after"] == 14
    assert by["shop-record-shown-13"]["equals"] == {"record": 13}
    assert "the page step 13 opened" in by["shop-record-shown-13"]["note"]
    assert by["shop-rows-after-27"]["after"] == 29
    assert "step 27 changed the list from 2 to 1" in by["shop-rows-after-27"]["note"]
    # the part's own draft keeps the part's numbers throughout
    own = {c["id"]: c for c in drafts.acceptance_cases(_part(), [{"n": 12, "record": "x"}])}
    assert own["rows-before-12"]["after"] == 11 and "step 12 acted" in own["rows-before-12"]["note"]


def test_a_record_binding_says_which_files_show_the_record(tmp_path):
    from labctl.botlog.recording import load

    rec = tmp_path / "rec"
    (rec / "dom").mkdir(parents=True)
    (rec / "screens").mkdir()
    (rec / "dom/0003.before.html").write_text(f"<html><body>{CARDS}</body></html>", "utf-8")
    (rec / "dom/0004.html").write_text("<h2 class='name'>Birch stool 1520</h2>", "utf-8")
    (rec / "screens/0004.png").write_bytes(b"")
    loc = [{"kind": "record", "rows": "#cards > div.col", "control": "a.t", "text_from": None,
            "title_shape": "Aaaaa aaaaa 9999"}]  # fmt: skip
    steps = [
        {"seq": 2, "url": "http://x/", "action": "goto", "nav": "navigate"},
        {"seq": 3, "url": "http://x/", "action": "click", "selector": "a.t",
         "dom_before": "dom/0003.before.html", "locators": loc},
        {"seq": 4, "url": "http://x/p", "action": "goto", "caused_by": 3, "dom": "dom/0004.html",
         "screenshot": "screens/0004.png"},
    ]  # fmt: skip
    (rec / "steps.jsonl").write_text("".join(json.dumps(s) + "\n" for s in steps), "utf-8")
    loaded = load(rec)
    assert bindings.where_to_check(loaded, 3) == (
        "dom/0003.before.html (the page as the step met it); screens/0004.png (the picture "
        "after step 4)"
    )
    entry = yaml.safe_load(bindings.template(loaded))["steps"][3]
    assert "screens/0003.png" not in entry["check_against"]
    assert entry["check_against"].startswith("dom/0003.before.html")
