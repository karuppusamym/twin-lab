"""Browser dialogs keep their message: recorded as interface text (data masked), carried in the
steps, the rules and the brief, read from the application's own code, and checked by the replay."""

from __future__ import annotations

import json

from labctl import pagerules, recorder, stepfacts
from labctl.behaviour import fidelity


class _Secrets:
    def redact_text(self, text: str) -> str:
        return text.replace("Rosalind", "Aaaaaaaa")


def test_the_recorder_keeps_the_message_masked():
    assert recorder.dialog_message("Product added.") == "Product added."
    assert recorder.dialog_message("  Order  ORD-10442 saved ") == "Order XXX-99999 saved"
    assert (
        recorder.dialog_message("Mail sent to ann@bank.example") == "Mail sent to xxx@xxxx.xxxxxxx"
    )
    assert recorder.dialog_message("Hello Rosalind", _Secrets()) == "Hello Aaaaaaaa"
    assert recorder.dialog_message(None) == "" and recorder.dialog_message("  ") == ""


def test_the_replay_compares_the_words_and_lets_values_differ():
    assert fidelity.same_message("Product added.", "Product added.")
    assert fidelity.same_message("Order XXX-99999 saved", "Order SYN-10001 saved")
    assert not fidelity.same_message("Product added.", "Product removed.")
    assert not fidelity.same_message("Product added.", "Product added. Thanks")


def test_dialogs_in_the_applications_code():
    src = (
        'function add(){ $.post(u, d, function(){ alert("Product added."); }); }\n'
        "function send(){ alert('Thanks for the message!!'); window.alert(data.msg); }\n"
        'if (confirm("Delete \\"it\\"?", 1)) {}  x.alert("not a dialog"); alert(`Hi ${n}`)'
    )
    assert pagerules.dialog_texts(src) == [("alert", "Product added."),
                                           ("alert", "Thanks for the message!!"),
                                           ("confirm", 'Delete "it"?')]  # fmt: skip


def _part(tmp_path, message):
    rec = tmp_path / "rec"
    (rec / "dom").mkdir(parents=True)
    page = "<html><body><a class='btn' id='add'>Add to cart</a></body></html>"
    (rec / "dom" / "0001.html").write_text(page, encoding="utf-8")
    (rec / "recording.har").write_text(json.dumps({"log": {"entries": []}}), encoding="utf-8")
    steps = [
        {"seq": 1, "ts": "2026-01-01T00:00:00Z", "url": "https://shop.example/prod.html",
         "action": "goto", "dom": "dom/0001.html"},
        {"seq": 2, "ts": "2026-01-01T00:00:01Z", "url": "https://shop.example/prod.html",
         "action": "click", "selector": "a#add"},
        {"seq": 3, "ts": "2026-01-01T00:00:02Z", "url": "https://shop.example/prod.html",
         "action": "dialog", "caused_by": 2,
         "dialog": {"kind": "alert", "choice": "accept", "message": message}},
    ]  # fmt: skip
    (rec / "steps.jsonl").write_text("".join(json.dumps(s) + "\n" for s in steps), "utf-8")
    return rec


def test_the_steps_carry_the_message(tmp_path):
    f = stepfacts.facts(_part(tmp_path, "Product added."))
    row = next(s for s in f.steps if s["action"] == "dialog")
    assert row["dialog"] == {"kind": "alert", "choice": "accept", "message": "Product added."}
    assert row["messages"] == [{"selector": "browser alert", "text": "Product added."}]
    assert any(m["text"] == "Product added." for e in f.effects for m in e["messages"])
