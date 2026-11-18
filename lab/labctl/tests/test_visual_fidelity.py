"""Visual fidelity (replication gap R08): the replica's screen against the source's pictures.

Image comparison is checked on made-up pictures; the live part records the to-do fixture's
source page (its pictures are the baselines) and replays on copies of it in tmp_path: an exact
copy, one shifted by one pixel, and one that changes only an excluded region.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PIL = pytest.importorskip("PIL")
pytest.importorskip("playwright.sync_api")

import replica_kit as kit  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from labctl import recorder  # noqa: E402
from labctl.behaviour import fidelity  # noqa: E402
from labctl.behaviour import visual as vis  # noqa: E402


def picture(path: Path, boxes=(), size=(120, 80), fill="white") -> Path:
    im = Image.new("RGB", size, fill)
    d = ImageDraw.Draw(im)
    for (x, y, w, h), colour in boxes:
        d.rectangle([x, y, x + w - 1, y + h - 1], fill=colour)
    im.save(path)
    return path


# --- comparing pictures -------------------------------------------------------------------------


def test_exact_needs_zero_differing_pixels_and_a_shift_fails(tmp_path):
    a = picture(tmp_path / "a.png", [((10, 10, 30, 20), "black")])
    same = picture(tmp_path / "same.png", [((10, 10, 30, 20), "black")])
    shifted = picture(tmp_path / "shift.png", [((11, 10, 30, 20), "black")])  # one pixel right
    got = vis.compare_images(a, same, diff_out=tmp_path / "d0.png")
    assert (got["status"], got["differing"], got["compared"]) == ("PASS", 0, 120 * 80)
    got = vis.compare_images(a, shifted, diff_out=tmp_path / "d1.png")
    assert got["status"] == "FAIL" and got["differing"] == 40  # a column left, a column right
    assert (tmp_path / "d1.png").exists()
    # a threshold allows what exact does not; exact stays strict
    loose = vis.compare_images(a, shifted, mode="threshold", max_ratio=0.01)
    assert loose["status"] == "PASS" and loose["ratio"] == pytest.approx(40 / 9600)
    assert vis.compare_images(a, shifted, mode="threshold", max_ratio=0.001)["status"] == "FAIL"


def test_excluded_regions_and_masked_pixels_are_left_out_not_passed(tmp_path):
    a = picture(tmp_path / "a.png", [((50, 40, 20, 10), "black")])
    b = picture(tmp_path / "b.png", [((50, 40, 20, 10), "red")])  # only the excluded box differs
    got = vis.compare_images(a, b, exclude=[(50, 40, 20, 10)])
    assert got["status"] == "PASS" and got["excluded"] == 200 and got["compared"] == 9600 - 200
    assert vis.compare_images(a, b)["status"] == "FAIL"  # without the exclusion
    # the source masked a typed value in magenta: those pixels (and one around) are excluded
    masked = picture(tmp_path / "m.png", [((10, 10, 20, 10), vis.MASK_RGB)])
    replica = picture(tmp_path / "r.png", [((10, 10, 18, 10), "blue")])  # synthetic text there
    got = vis.compare_images(masked, replica)
    assert got["status"] == "PASS" and got["masked"] == 22 * 12
    assert "masked" in got["note"] and "not compared" in got["note"]
    # a region: only it is compared
    got = vis.compare_images(a, b, region=(0, 0, 40, 30))
    assert got["status"] == "PASS" and got["compared"] == 1200
    # nothing left to compare is BLOCKED, never PASS
    got = vis.compare_images(a, b, exclude=[(0, 0, 120, 80)])
    assert got["status"] == "BLOCKED" and got["compared"] == 0


def test_pictures_of_different_sizes_fail(tmp_path):
    a = picture(tmp_path / "a.png")
    b = picture(tmp_path / "b.png", size=(120, 81))
    got = vis.compare_images(a, b)
    assert got["status"] == "FAIL" and "differ in size" in got["note"]


# --- against the source's pictures ---------------------------------------------------------------


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    src = kit.serve_dir(kit.make_app(tmp_path_factory.mktemp("src"), "source.html"))
    out = tmp_path_factory.mktemp("vis") / "rec"
    recorder.record(f"http://127.0.0.1:{src.server_address[1]}/index.html", out,
                    script=kit.add_one_flow, headless=True)  # fmt: skip
    src.shutdown()
    kit.write_bindings(out, kit.SYNTHETIC[:1])
    return out


def visual_doc(seq: int, **state) -> dict:
    return {"version": 1, "viewport": "desktop", "fonts": ["Arial", "No Such Font Twinlab"],
            "states": [{"id": "one-task", "after": seq, "mode": "exact",
                        "exclude": [".todo-list label"], **state}]}  # fmt: skip


def replay_copy(rec, tmp_path, doc, html=()):
    app = kit.make_app(tmp_path / "replica", "source.html", html=html)  # the source's own page
    server = kit.serve_dir(app)
    spec = tmp_path / "visual.yaml"
    spec.write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")
    try:
        return fidelity.replay(rec, f"http://127.0.0.1:{server.server_address[1]}",
                               visual=spec, acceptance=False, artifacts=tmp_path / "art")  # fmt: skip
    finally:
        server.shutdown()


def _state_after_enter(rec) -> int:
    from labctl.botlog.recording import load

    return next(s.seq for s in load(rec).steps if s.action == "keypress")


def test_the_recorders_pictures_are_the_pinned_desktop_size(recorded):
    pictures = vis.pictures_of(recorded)
    assert pictures, "the recorder kept no picture"
    size = vis.picture_size(recorded / pictures[max(pictures)])
    assert size == (vis.VIEWPORTS["desktop"]["width"], vis.VIEWPORTS["desktop"]["height"])


def test_the_same_page_is_pixel_exact_with_masked_text_excluded(recorded, tmp_path):
    seq = _state_after_enter(recorded)
    report = replay_copy(recorded, tmp_path, visual_doc(seq))
    [v] = report.visual
    assert v.status == "PASS" and v.differing == 0, (v, report.markdown())
    assert v.compared > 0 and v.excluded > 0
    assert v.masked > 0  # the typed title was masked in the source picture: excluded, not passed
    assert Path(v.before).name == f"{seq:04d}.png" and Path(v.after_path).exists()
    assert report.categories["appearance"].status == "PASS"
    assert report.verdict == "PASS WITHIN DECLARED SCOPE"
    setup = report.visual_setup
    assert setup["browser"] == "chromium" and setup["browser_version"]
    assert setup["size"] == {"width": 1280, "height": 720} and setup["device_scale_factor"] == 1
    assert setup["reduced_motion"] == "reduce" and setup["zoom"] == 1
    assert setup["fonts_available"]["No Such Font Twinlab"] is False
    assert report.fingerprint["viewport"] == {"width": 1280, "height": 720}
    assert report.fingerprint["files"]["visual"]["digest"]


def test_a_one_pixel_layout_shift_fails_the_state(recorded, tmp_path):
    seq = _state_after_enter(recorded)
    report = replay_copy(recorded, tmp_path, visual_doc(seq),
                         html=[("body { font: 16px sans-serif; margin: 2em; }",
                                "body { font: 16px sans-serif; margin: 2em; padding-top: 1px; }")])  # fmt: skip
    [v] = report.visual
    assert v.status == "FAIL" and v.differing > 0 and v.diff and Path(v.diff).exists()
    assert report.verdict == "FAIL" and report.categories["appearance"].status == "FAIL"
    assert report.categories["replay"].status == "PASS"  # the structure is the same
    div = report.first_divergence
    assert div.kind == "visual" and div.before and div.after and div.diff


def test_a_change_inside_an_excluded_region_does_not_fail(recorded, tmp_path):
    seq = _state_after_enter(recorded)
    change = [('<p class="info">Source page</p>', '<p class="info">Replica page, other text</p>')]
    report = replay_copy(recorded, tmp_path, visual_doc(seq, exclude=[".todo-list label",
                                                                      ".info"]), html=change)  # fmt: skip
    assert report.visual[0].status == "PASS", report.markdown()
    report = replay_copy(recorded, tmp_path / "2", visual_doc(seq), html=change)
    assert report.visual[0].status == "FAIL"  # the same change, not excluded


def test_a_mobile_state_needs_a_mobile_run_and_an_approved_image(recorded, tmp_path):
    seq = _state_after_enter(recorded)
    doc = visual_doc(seq, viewport="mobile")
    report = replay_copy(recorded, tmp_path, doc)
    assert report.visual[0].status == "BLOCKED" and "mobile size" in report.visual[0].note
    assert report.verdict == "BLOCKED"
    # at the mobile size the recorder's (desktop) picture cannot be the baseline
    app = kit.make_app(tmp_path / "m" / "replica", "source.html")
    server = kit.serve_dir(app)
    spec = tmp_path / "m" / "visual.yaml"
    spec.write_text(yaml.safe_dump(doc), "utf-8")
    try:
        report = fidelity.replay(recorded, f"http://127.0.0.1:{server.server_address[1]}",
                                 visual=spec, acceptance=False, viewport="mobile",
                                 artifacts=tmp_path / "m" / "art")  # fmt: skip
    finally:
        server.shutdown()
    v = report.visual[0]
    assert v.status == "FAIL" and "differ in size" in v.note


def test_the_visual_template_lists_the_recorders_pictures(recorded, tmp_path):
    from typer.testing import CliRunner

    from labctl.behaviour.cli import app

    out = tmp_path / "visual.yaml"
    result = CliRunner().invoke(app, ["visual", str(recorded), "--out", str(out)])
    assert result.exit_code == 0, result.output
    doc = yaml.safe_load(out.read_text("utf-8"))
    assert [s["after"] for s in doc["states"]] == sorted(vis.pictures_of(recorded))
    assert "1280x720" in out.read_text("utf-8")
