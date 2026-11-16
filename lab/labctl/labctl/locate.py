"""Selectors that cross frames: `iframe#content >>> button#save`.

CSS cannot go into a frame, so Twin Lab writes a frame boundary as ` >>> `: every part before
the last names a frame, the last names the element. The recorder writes selectors this way for
actions inside frames, the UiPath analyser for selectors that go through an IFRAME, and
conformance, fidelity replay and the harness bot use `locate()` to find them. Open shadow roots
need nothing: Playwright's CSS looks inside them. Elements in closed shadow roots cannot be
located by any selector; recordings still show them.
"""

from __future__ import annotations

FRAME = " >>> "


def parts(selector: str) -> tuple[list[str], str]:
    """(frames, element) of a selector."""
    bits = [b.strip() for b in selector.split(">>>")]
    return [b for b in bits[:-1] if b], bits[-1]


def locate(page, selector: str):
    """A Playwright Locator for `selector`, entering each frame it names."""
    frames, element = parts(selector)
    scope = page
    for frame in frames:
        scope = scope.frame_locator(frame)
    return scope.locator(element)


def join(frames: list[str], element: str) -> str:
    return FRAME.join([*frames, element]) if frames else element
