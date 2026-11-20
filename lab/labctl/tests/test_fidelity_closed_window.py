"""A click that closes its own window can be reported by the driver as 'target closed' before
it returns; replay counts it as done only when the recording's next step in that tab closes it."""

from types import SimpleNamespace

from labctl.behaviour.fidelity import _closed_its_own_window
from labctl.botlog.recording import RecStep


class TargetClosedError(Exception):
    pass


class _Page:
    def __init__(self, closed):
        self.closed = closed

    def is_closed(self):
        return self.closed


def _step(seq, action, tab):
    return RecStep(seq=seq, ts=None, url=None, action=action, selector=None, dom=None, tab=tab)


def _steps(after="tab-close"):
    return [_step(5, "click", "t1"), _step(6, "click", "t2"), _step(7, after, "t2"),
            _step(8, "click", "t1")]  # fmt: skip


def test_a_window_that_closed_itself_as_recorded_is_not_a_failure():
    steps = _steps()
    run = SimpleNamespace(tabs={"t1": _Page(False), "t2": _Page(True)})
    assert _closed_its_own_window(TargetClosedError("closed"), steps[1], run, steps)
    run.tabs.pop("t2")
    assert _closed_its_own_window(TargetClosedError("closed"), steps[1], run, steps)


def test_other_errors_open_windows_and_unrecorded_closes_still_fail():
    steps = _steps()
    run = SimpleNamespace(tabs={"t1": _Page(False), "t2": _Page(True)})
    assert not _closed_its_own_window(TimeoutError("slow"), steps[1], run, steps)
    open_run = SimpleNamespace(tabs={"t1": _Page(False), "t2": _Page(False)})
    assert not _closed_its_own_window(TargetClosedError("closed"), steps[1], open_run, steps)
    clicked_on = _steps(after="click")
    assert not _closed_its_own_window(TargetClosedError("closed"), clicked_on[1], run, clicked_on)
