"""Selectors across frames: the ` >>> ` convention, from UiPath selectors to Playwright."""

from __future__ import annotations

from labctl import locate
from labctl.analyse.uipath import to_css
from labctl.behaviour import dom, fidelity


def test_uipath_iframe_selectors_mark_the_frame():
    assert (
        to_css(
            "<html app='chrome.exe' /><webctrl tag='IFRAME' id='content' /><webctrl tag='BUTTON' id='save' />"
        )
        == "iframe#content >>> button#save"
    )
    assert to_css(
        "<webctrl tag='IFRAME' id='a' /><webctrl tag='FRAME' name='b' /><webctrl id='x' />"
    ) == ("iframe#a >>> frame[name='b'] >>> #x")
    assert to_css("<webctrl tag='INPUT' id='caseNo' />") == "input#caseNo"


def test_parts_and_join():
    assert locate.parts("iframe#a >>> iframe#b >>> #x") == (["iframe#a", "iframe#b"], "#x")
    assert locate.parts("#x") == ([], "#x")
    assert locate.join(["iframe#a"], "#x") == "iframe#a >>> #x"
    assert dom.key_of("iframe#a >>> button#save") == "#save"


class FakeScope:
    def __init__(self, path=()):
        self.path = list(path)

    def frame_locator(self, sel):
        return FakeScope([*self.path, f"frame:{sel}"])

    def locator(self, sel):
        return [*self.path, sel]


def test_locate_enters_each_frame():
    assert locate.locate(FakeScope(), "iframe#a >>> iframe#b >>> #x") == [
        "frame:iframe#a", "frame:iframe#b", "#x"
    ]  # fmt: skip


def test_frame_and_shadow_content_compare_like_any_element():
    recorded = ('<html><body><iframe id="f"></iframe><tl-frame data-tl-frame="iframe#f">'
                '<button id="save">Save</button></tl-frame><x-c><tl-shadow data-tl-mode="closed">'
                '<input id="amount" value="12"></tl-shadow></x-c></body></html>')  # fmt: skip
    live_missing = recorded.replace('<button id="save">Save</button>', "")
    diffs = fidelity.compare_dom(recorded, live_missing)
    assert any(d.element == "button#save" and d.check == "missing" for d in diffs)
    assert not fidelity.compare_dom(recorded, recorded)
