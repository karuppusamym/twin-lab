"""A request that starts a few ms before its step is logged belongs to that step, by one rule
for step facts and the seed plan (navigation, API catalogue and page rules read theirs)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from labctl import seedplan, stepfacts


def test_step_at_allows_the_recorders_lag():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    timed = [(1, t0), (2, t0 + timedelta(seconds=1)), (3, t0 + timedelta(seconds=2))]
    assert stepfacts.step_at(timed, t0 + timedelta(seconds=1, milliseconds=-3)) == 2
    assert stepfacts.step_at(timed, t0 + timedelta(seconds=1, milliseconds=-400)) == 1
    assert stepfacts.step_at(timed, t0 + timedelta(seconds=5)) == 3
    assert stepfacts.step_at(timed, None) is None


def test_step_facts_and_seed_plan_agree(tmp_path):
    rec = tmp_path / "rec"
    (rec / "dom").mkdir(parents=True)
    page = "<html><body><a id='next' class='carousel-control-next'>Next</a>" \
           "<a class='list-group-item' id='itemc'>Phones</a></body></html>"  # fmt: skip
    (rec / "dom" / "0001.html").write_text(page, encoding="utf-8")
    call = {"startedDateTime": "2026-01-01T00:00:01.997Z", "time": 5, "_resourceType": "fetch",
            "request": {"method": "POST", "url": "https://shop.example/bycat", "headers": [],
                        "postData": {"mimeType": "application/json", "text": '{"cat": "phone"}'}},
            "response": {"status": 200, "content": {"mimeType": "application/json",
                                                    "text": '{"Items": []}'}}}  # fmt: skip
    (rec / "recording.har").write_text(json.dumps({"log": {"entries": [call]}}), encoding="utf-8")
    steps = [
        {"seq": 1, "ts": "2026-01-01T00:00:00Z", "url": "https://shop.example/", "action": "goto",
         "dom": "dom/0001.html"},
        {"seq": 2, "ts": "2026-01-01T00:00:01Z", "url": "https://shop.example/", "action": "click",
         "selector": "a.carousel-control-next"},
        {"seq": 3, "ts": "2026-01-01T00:00:02Z", "url": "https://shop.example/", "action": "click",
         "selector": "a#itemc"},
    ]  # fmt: skip
    (rec / "steps.jsonl").write_text("".join(json.dumps(s) + "\n" for s in steps), "utf-8")
    f = stepfacts.facts(rec)
    by = {s["n"]: s.get("requests") for s in f.steps}
    assert by[3] == ["POST /bycat 200"] and by[2] is None  # not the carousel click before it
    part = seedplan.load_part("shop", rec)
    assert [e.route for e in part.owner.get(3, [])] == ["/bycat"] and 2 not in part.owner
