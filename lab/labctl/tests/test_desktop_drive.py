"""The desktop Driver's waits (labctl.desktop.drive): the foreground lock, a covered point and a
drop-down list still filling. A fake `win` stands in for Windows, so these run on every platform."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from labctl.desktop import drive

ME, OTHER = 100, 200  # the driven process, and another one
TOP, ELSEWHERE = 1, 2  # the driven window, and another application's


class Rect:
    left, top, right, bottom = 10, 10, 30, 30


class FakeWin:
    """Just enough of labctl.desktop.win. `fg` is a list of foreground windows, one per look
    (the last one stays); `under` the same for the window under a point; `items` for find_all
    on a combo box."""

    def __init__(self, fg=(TOP,), under=(TOP,), items=(1,), grant_after_nudges=0):
        self.fg, self.under, self.items = list(fg), list(under), list(items)
        self.grant_after = grant_after_nudges
        self.nudges, self.set_fg, self.clicks, self.selected = 0, [], [], []
        self.user32 = SimpleNamespace(SetForegroundWindow=self._set_foreground)

    @staticmethod
    def _next(seq):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def _set_foreground(self, hwnd):
        self.set_fg.append(hwnd)
        if self.grant_after is not None and self.nudges >= self.grant_after:
            self.fg = [hwnd]  # granted: the last input came from this process

    def nudge_input(self):
        self.nudges += 1

    def foreground(self):
        return self._next(self.fg)

    def window_pid(self, hwnd):
        return ME if hwnd == TOP else OTHER

    def window_at(self, x, y):
        return self._next(self.under)

    def root_window(self, hwnd):
        return TOP

    def mouse_click(self, x, y):
        self.clicks.append((x, y))

    def find_all(self, scope, selector):
        if "ListItem" in selector:
            n = self._next(self.items)
            return [SimpleNamespace(name=f"item{i}") for i in range(n)]
        return [SimpleNamespace(CurrentBoundingRectangle=Rect())]

    def expand(self, el, on):
        pass

    def select(self, el):
        self.selected.append(el)


def driver(fake: FakeWin) -> drive.Driver:
    d = drive.Driver.__new__(drive.Driver)  # no require(): this is not Windows
    d.win, d.pids, d.pause_s = fake, {ME}, 0
    d.FOREGROUND_S = d.UNCOVERED_S = d.ITEMS_S = 0.6
    return d


WINDOW = SimpleNamespace(CurrentNativeWindowHandle=TOP)


def test_click_wins_the_foreground_from_the_lock():
    # another application is in front; SetForegroundWindow is refused until this process has
    # sent input (the zero-pixel move), as Windows' foreground lock does
    fake = FakeWin(fg=[ELSEWHERE], grant_after_nudges=1)
    assert driver(fake).click(WINDOW, "#btnSave") == "mouse"
    assert fake.nudges >= 1 and fake.set_fg and fake.clicks == [(20, 20)]


def test_click_refuses_when_the_foreground_never_comes():
    fake = FakeWin(fg=[ELSEWHERE], grant_after_nudges=None)  # always refused
    with pytest.raises(drive.DriveError, match="not in the foreground"):
        driver(fake).click(WINDOW, "#btnSave")
    assert fake.clicks == [] and fake.nudges >= 2  # it kept trying, then clicked nothing


def test_click_waits_for_a_covering_window_to_go():
    fake = FakeWin(under=[ELSEWHERE, ELSEWHERE, TOP])  # a toast closing
    driver(fake).click(WINDOW, "#btnSave")
    assert fake.clicks == [(20, 20)]


def test_click_never_clicks_through_a_window_that_stays():
    fake = FakeWin(under=[ELSEWHERE])
    with pytest.raises(drive.DriveError, match="another window covers it"):
        driver(fake).click(WINDOW, "#btnSave")
    assert fake.clicks == []


def test_choose_waits_for_a_list_still_filling():
    fake = FakeWin(items=[0, 0, 1])
    driver(fake).choose(WINDOW, "#cboStatus", "Resolved")
    assert len(fake.selected) == 1


def test_choose_still_refuses_a_missing_or_ambiguous_item():
    with pytest.raises(drive.DriveError, match="found 0 time"):
        driver(FakeWin(items=[0])).choose(WINDOW, "#cboStatus", "Resolved")
    with pytest.raises(drive.DriveError, match="found 2 time"):
        driver(FakeWin(items=[2])).choose(WINDOW, "#cboStatus", "Resolved")


def test_click_menu_has_the_same_guards():
    fake = FakeWin(fg=[ELSEWHERE], under=[ELSEWHERE, TOP], grant_after_nudges=1)
    driver(fake).click_menu(WINDOW, "Case")
    assert fake.nudges >= 1 and fake.clicks == [(20, 20)]
    stuck = FakeWin(under=[ELSEWHERE])
    with pytest.raises(drive.DriveError, match="covered by another window"):
        driver(stuck).click_menu(WINDOW, "Case")
    assert stuck.clicks == []
