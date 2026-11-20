"""The mainframe recorder: a TN3270 proxy between a terminal and a host, reading the 3270 data
stream into screens, fields and keys, with no value kept. Uses the synthetic host and terminal
in reference/mainframe/demo_host.py."""

from __future__ import annotations

import importlib.util
import json
import threading
import time
from pathlib import Path

from labctl.workstation import tn3270

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "demo_host", ROOT / "reference" / "mainframe" / "demo_host.py"
)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


def _host():
    stop = threading.Event()
    server = demo.serve(0, stop)
    return server, stop, server.getsockname()[1]


def _steps(folder: Path) -> list[dict]:
    return [
        json.loads(x) for x in (folder / "steps.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def test_addresses_round_trip():
    for addr in (0, 1, 79, 80, 1919, 3563):
        b = tn3270.encode_addr(addr)
        assert tn3270.decode_addr(b[0], b[1]) == addr
    assert tn3270.decode_addr(0x07, 0x80) == (7 << 8) | 0x80  # 14-bit form


def test_screen_fields_from_an_erase_write():
    s = tn3270.Screen()
    s.write(demo.signon(), (24, 80))
    fields = s.fields()
    pw = s.field_at(demo.at(6, 25))
    assert pw["hidden"] and not pw["protected"]
    user = s.field_at(demo.at(5, 25))
    assert not user["protected"] and user["len"] == 8
    assert s.text(demo.at(1, 31), 12) == "DEMO SIGN ON"
    assert any(f["intensified"] for f in fields)
    assert s.rc(s.cursor) == (5, 25)


def test_proxy_records_screens_fields_and_keys_without_values(tmp_path):
    server, stop, port = _host()
    proxy = tn3270.Proxy("127.0.0.1", port, tmp_path / "mf")
    proxy.start()
    try:
        demo.flow(proxy.listen)
        time.sleep(0.5)
    finally:
        proxy.stop()
        proxy.join(10)
        stop.set()
        server.close()
    steps = _steps(tmp_path / "mf")
    actions = [s["action"] for s in steps]
    assert actions[0] == "screen" and steps[0]["screen"]["title"] == "DEMO SIGN ON"
    assert actions.count("aid") == 5
    assert [s["key"] for s in steps if s["action"] == "aid"] == [
        "Enter",
        "Enter",
        "Enter",
        "Enter",
        "PF3",
    ]
    types = [s for s in steps if s["action"] == "type"]
    assert types[0]["field"]["label"] == "USERID" and types[0]["typed_shape"] == "XXXXXX"
    assert (
        types[1]["field"]["label"] == "PASSWORD"
        and types[1].get("secret")
        and "typed_shape" not in types[1]
    )
    acct = [s for s in types if s["field"]["label"] == "ACCOUNT NO"]
    assert [s["typed_shape"] for s in acct] == ["9999999", "9999999"]
    # the same map with another account's data is the same screen
    inquiries = {
        s["screen"]["id"]
        for s in steps
        if s["action"] == "screen" and s["screen"]["title"] == "ACCOUNT INQUIRY"
    }
    assert len(inquiries) == 1
    titles = [s["screen"]["title"] for s in steps if s["action"] == "screen"]
    assert titles[-1] == "SIGNED OFF"
    everything = (tmp_path / "mf" / "steps.jsonl").read_text(encoding="utf-8") + "".join(
        f.read_text(encoding="utf-8") for f in (tmp_path / "mf" / "screens").glob("*.json")
    )
    for value in ("TESTER", "WRONG", "DEMO123", "1000001", "1000002", "1,250.00", "80.15"):
        assert value not in everything, value
    meta = json.loads((tmp_path / "mf" / "mainframe.json").read_text(encoding="utf-8"))
    assert meta["connections"] == 1 and meta["steps"] == len(steps)


def test_a_session_with_a_mainframe_target_sends_labels_only(tmp_path):
    from labctl.workstation.levels import Targets
    from labctl.workstation.session import Session

    server, stop, port = _host()
    targets = Targets.from_list([{"tn3270": f"127.0.0.1:{port}", "listen": 0, "name": "Demo CICS"}])

    def script(session):
        demo.flow(session.mainframe[0].listen)

    try:
        folder = Session(tmp_path / "rec", targets, name="mf trial", max_seconds=60).run(
            script=script
        )
    finally:
        stop.set()
        server.close()
    box = json.loads((folder / "portal-outbox.json").read_text(encoding="utf-8"))
    key = f"mainframe:127.0.0.1:{port}"
    assert [a["key"] for a in box["apps"]] == [key]
    assert box["apps"][0]["display"] == "Demo CICS"
    flow = box["flows"][key]
    labels = [s.get("label") for s in flow if s["action"] == "type"]
    assert labels[:2] == ["USERID", "PASSWORD"]
    assert {"action": "aid", "control": "key", "label": "PF3"}.items() <= next(
        s for s in flow if s.get("label") == "PF3"
    ).items()
    assert any(s.get("screen") == "ACCOUNT INQUIRY" for s in flow)
    text = json.dumps(box)
    for value in ("TESTER", "DEMO123", "1000001", "ANNA", "1,250"):
        assert value not in text, value
    journal = (folder / "journal.jsonl").read_text(encoding="utf-8")
    assert '"kind": "mainframe"' in journal
