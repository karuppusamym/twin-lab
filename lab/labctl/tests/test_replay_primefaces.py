"""The replay on PrimeFaces pages: it waits while PrimeFaces still holds a request back (a data
table's filter waits out its delay before it sends anything), except after typing, whose
snapshot is the page as the person moved on; and it does not compare the classes PrimeFaces
sets for the pointer, the focus and a filled field."""

from __future__ import annotations

from labctl import jsfviews
from labctl.behaviour import dom as domlib
from labctl.behaviour import fidelity


class FakePage:
    """No request in flight; PrimeFaces says it is busy for the first `busy` looks."""

    def __init__(self, busy: int):
        self._tl_inflight = 0
        self.busy, self.looks, self.waited = busy, 0, 0

    def wait_for_load_state(self, *a, **k):
        pass

    def wait_for_timeout(self, ms):
        self.waited += ms

    def evaluate(self, script):
        assert script == jsfviews.BUSY_JS
        self.looks += 1
        return self.looks <= self.busy


def test_the_replay_waits_while_primefaces_holds_a_request_back():
    page = FakePage(busy=6)  # 300 ms: a filter's delay
    fidelity._settle(page, 150)
    assert page.looks >= 7 and page.waited >= 300 + 150
    typed = FakePage(busy=6)
    fidelity._settle(typed, 150, page_work=False)  # after typing: not waited for
    assert typed.looks == 0 and typed.waited == 150


def test_pointer_state_classes_are_not_part_of_the_compared_page():
    """PrimeFaces marks the element under the mouse and the one with the focus (`ui-state-hover`,
    `ui-state-focus`): where the pointer happened to be, not what the page shows."""
    a = domlib.snapshot(
        '<button id="b" class="ui-button ui-state-hover ui-state-focus">Go</button>'
    )
    b = domlib.snapshot('<button id="b" class="ui-button">Go</button>')
    assert a.signatures == b.signatures == ["button#b.ui-button"]
    assert a.elements["#b"].classes == b.elements["#b"].classes == ["ui-button"]
    c = domlib.snapshot('<a id="p" href="#" class="ui-paginator-page ui-state-active">2</a>')
    assert c.elements["#p"].classes == ["ui-paginator-page", "ui-state-active"]  # a real state
