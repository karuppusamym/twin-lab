"""Visual fidelity (replication gap R08): the replica's screen compared with the source's.

Declared in `visual.yaml` beside the recording (`--visual` names another file), and versioned
with it: the states to compare, their baselines, thresholds and excluded regions, all set before
any comparison runs.

```yaml
version: 1
viewport: desktop          # desktop (1280x720, what the recorder uses) | mobile (390x844)
clock: 2026-01-05T09:00:00Z   # optional: Date is fixed at this time where the page shows time
fonts: [Georgia, Arial]    # optional: families to report as available or not
states:
  - id: two-tasks
    after: 5               # the step; its baseline is the recorder's picture screens/0005.png
    required: true
    mode: exact            # exact: zero differing pixels | threshold: up to max_ratio
    max_ratio: 0.0
    tolerance: 0           # a channel difference up to this is not a difference
    exclude: [.todo-list label, {css: .banner}]   # regions left out, by locator
    region: .todoapp       # optional: compare only this element's box
    # approved: approved/two-tasks.png  # instead of the recorder's picture: an approved image
```

The setup is pinned: Chromium (its version is reported), the viewport, device scale factor 1,
reduced motion, animations and the caret frozen by the screenshot, zoom 1, the clock when one is
declared, and the fonts reported. Pictures are of the viewport, as the recorder takes them.

The recorder paints what shows a typed value in solid magenta (255, 0, 255) before it takes a
picture. Those pixels (and one pixel around them) are excluded automatically and reported as
`masked`, never counted as passed. A state that is all masked or excluded is BLOCKED. "Exact"
means zero differing pixels in what is compared. Pictures of different sizes FAIL: the layout or
the viewport changed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

FILE = "visual.yaml"
VIEWPORTS = {"desktop": {"width": 1280, "height": 720}, "mobile": {"width": 390, "height": 844}}
MASK_RGB = (255, 0, 255)  # Playwright's default mask colour, which the recorder uses
PASS, FAIL, BLOCKED, SKIPPED = "PASS", "FAIL", "BLOCKED", "SKIPPED"


@dataclass
class State:
    id: str
    after: int | str
    required: bool = True
    mode: str = "exact"
    max_ratio: float = 0.0
    tolerance: int = 0
    exclude: list = field(default_factory=list)
    region: Any = None
    approved: str | None = None
    viewport: str | None = None  # default: the file's
    problem: str | None = None


@dataclass
class Spec:
    states: list[State] = field(default_factory=list)
    viewport: str = "desktop"
    clock: str | None = None
    fonts: list[str] = field(default_factory=list)
    path: Path | None = None

    def size(self) -> dict:
        return dict(VIEWPORTS.get(self.viewport, VIEWPORTS["desktop"]))


class VisualResult(BaseModel):
    id: str
    after: str
    viewport: str
    required: bool = True
    status: str
    mode: str = "exact"
    max_ratio: float = 0.0
    compared: int = 0  # pixels compared
    differing: int = 0
    ratio: float = 0.0
    excluded: int = 0  # pixels left out by declared regions
    masked: int = 0  # pixels left out because the source picture masked them
    before: str | None = None  # the baseline (source picture or approved image)
    after_path: str | None = None  # the replica's picture
    diff: str | None = None
    note: str | None = None


def load(path: Path | None) -> Spec | None:
    if path is None or not Path(path).exists():
        return None
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a visual baseline file")
    spec = Spec(path=Path(path), viewport=str(data.get("viewport") or "desktop"),
                clock=str(data["clock"]) if data.get("clock") else None,
                fonts=[str(f) for f in data.get("fonts") or []])  # fmt: skip
    seen: set[str] = set()
    for n, e in enumerate(data.get("states") or [], 1):
        if not isinstance(e, dict):
            spec.states.append(State(id=f"state-{n}", after="end", problem="not a mapping"))
            continue
        after = e.get("after", "end")
        st = State(id=str(e.get("id") or f"state-{n}"), after=after,
                   required=bool(e.get("required", True)), mode=str(e.get("mode") or "exact"),
                   max_ratio=float(e.get("max_ratio") or 0.0), tolerance=int(e.get("tolerance") or 0),
                   exclude=list(e.get("exclude") or []), region=e.get("region"),
                   approved=e.get("approved"), viewport=e.get("viewport"))  # fmt: skip
        if str(after).lower() != "end":
            try:
                st.after = int(after)
            except (TypeError, ValueError):
                st.problem = f"after: {after!r} is neither a step number nor end"
        if st.mode not in ("exact", "threshold"):
            st.problem = f"mode {st.mode!r}: exact or threshold"
        if (st.viewport or spec.viewport) not in VIEWPORTS:
            st.problem = f"viewport {st.viewport or spec.viewport!r}: desktop or mobile"
        if st.after == "end" and not st.approved:
            st.problem = st.problem or "a state at the end needs an approved image"
        if st.id in seen:
            # both states stay in the result; the second cannot stand in for the first
            st.problem = st.problem or f"the id {st.id!r} is used twice"
            st.id = f"{st.id}#{n}"
        seen.add(st.id)
        spec.states.append(st)
    return spec


def digest(spec: Spec | None, rec_path: Path | None, pictures: dict[int, str]) -> str | None:
    """The file and every baseline image it uses: changing either makes a result out of date."""
    if spec is None or spec.path is None:
        return None
    h = hashlib.sha256(Path(spec.path).read_bytes())
    for st in spec.states:
        img = baseline_image(st, rec_path, pictures, spec.path)
        if img is not None and img.exists():
            h.update(img.read_bytes())
    return h.hexdigest()[:12]


def baseline_image(st: State, rec_path: Path | None, pictures: dict[int, str],
                   spec_path: Path | None = None) -> Path | None:  # fmt: skip
    if st.approved:
        base = Path(spec_path).parent if spec_path else Path(rec_path or ".")
        return (base / st.approved).resolve()
    if isinstance(st.after, int) and st.after in pictures and rec_path is not None:
        return (Path(rec_path) / pictures[st.after]).resolve()
    return None


def pictures_of(rec_path: Path) -> dict[int, str]:
    """seq -> the recorder's picture after that step (`screenshot` in steps.jsonl)."""
    from labctl.behaviour.acceptance import _raw_steps

    out = {}
    for d in _raw_steps(rec_path):
        if isinstance(d.get("screenshot"), str) and isinstance(d.get("seq"), int):
            out[d["seq"]] = d["screenshot"]
    return out


def picture_size(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return im.size
    except Exception:  # noqa: BLE001 - not a picture
        return None


# --- the pinned setup -----------------------------------------------------------------------------

_FONTS_JS = r"""async (families) => {
  try { await document.fonts.ready; } catch (e) {}
  const loaded = [...document.fonts].filter(f => f.status === "loaded").map(f => f.family);
  const checks = {};
  for (const f of families) {
    // available: the family draws differently from both generic fallbacks
    const c = document.createElement("canvas").getContext("2d");
    const w = (font) => { c.font = "32px " + font; return c.measureText("mmmmmmmmmmlli1WQ").width; };
    checks[f] = w('"' + f + '", monospace') !== w("monospace") || w('"' + f + '", serif') !== w("serif");
  }
  return {loaded: [...new Set(loaded)], available: checks,
          dpr: window.devicePixelRatio, zoom: window.visualViewport ? window.visualViewport.scale : 1};
}"""


def context_options(spec: Spec) -> dict:
    return {"viewport": spec.size(), "device_scale_factor": 1, "reduced_motion": "reduce"}


def prepare(page, spec: Spec) -> None:
    if spec.clock:
        from datetime import datetime

        when = datetime.fromisoformat(spec.clock.replace("Z", "+00:00"))
        page.clock.set_fixed_time(when)


def setup_report(browser, page, spec: Spec) -> dict:
    try:
        fonts = page.evaluate(_FONTS_JS, spec.fonts)
    except Exception:  # noqa: BLE001 - the page went away
        fonts = {}
    return {
        "browser": browser.browser_type.name,
        "browser_version": browser.version,
        "viewport": spec.viewport,
        "size": spec.size(),
        "device_scale_factor": 1,
        "reduced_motion": "reduce",
        "animations": "disabled",
        "caret": "hide",
        "clock": spec.clock,
        "zoom": fonts.get("zoom"),
        "device_pixel_ratio": fonts.get("dpr"),
        "fonts_loaded": fonts.get("loaded") or [],
        "fonts_available": fonts.get("available") or {},
    }


# --- comparing ------------------------------------------------------------------------------------


def boxes(page, locators: list) -> list[tuple[int, int, int, int]]:
    """Every box (x, y, w, h, in pixels) of every element the locators match."""
    from labctl.behaviour.acceptance import locator_for

    out = []
    for spec in locators:
        loc = locator_for(page, spec)
        for i in range(loc.count()):
            b = loc.nth(i).bounding_box()
            if b and b["width"] > 0 and b["height"] > 0:
                out.append((int(b["x"]), int(b["y"]), int(b["width"] + 0.999),
                            int(b["height"] + 0.999)))  # fmt: skip
    return out


def compare_images(before: Path, after: Path, *, exclude: list[tuple[int, int, int, int]] = (),
                   region: tuple[int, int, int, int] | None = None, mode: str = "exact",
                   max_ratio: float = 0.0, tolerance: int = 0,
                   diff_out: Path | None = None) -> dict:  # fmt: skip
    """Differing pixels between two pictures inside the compared region, leaving out declared
    boxes and the source's masked pixels. Returns the counts, the status and a note."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    a = Image.open(before).convert("RGB")
    b = Image.open(after).convert("RGB")
    if a.size != b.size:
        return {"status": FAIL, "compared": 0, "differing": 0, "ratio": 1.0, "excluded": 0,
                "masked": 0, "note": f"the pictures differ in size ({a.size[0]}x{a.size[1]} and "
                                     f"{b.size[0]}x{b.size[1]}): the layout or viewport changed"}  # fmt: skip
    w, h = a.size
    # what is compared: the region (or everything), minus declared boxes, minus masked pixels
    include = Image.new("L", (w, h), 0 if region else 255)
    draw = ImageDraw.Draw(include)
    if region:
        x, y, rw, rh = region
        draw.rectangle([x, y, x + rw - 1, y + rh - 1], fill=255)
    area = _count(include)
    declared = Image.new("L", (w, h), 0)
    dd = ImageDraw.Draw(declared)
    for x, y, bw, bh in exclude:
        dd.rectangle([x, y, x + bw - 1, y + bh - 1], fill=255)
    declared = ImageChops.multiply(declared, include)
    excluded = _count(declared)
    include = ImageChops.subtract(include, declared)
    r, g, bl = a.split()
    magenta = ImageChops.multiply(
        ImageChops.multiply(r.point(lambda v: 255 if v == MASK_RGB[0] else 0),
                            g.point(lambda v: 255 if v == MASK_RGB[1] else 0)),
        bl.point(lambda v: 255 if v == MASK_RGB[2] else 0),
    )  # fmt: skip
    if _count(magenta):
        magenta = magenta.filter(ImageFilter.MaxFilter(3))
    masked_here = ImageChops.multiply(magenta, include)
    masked = _count(masked_here)
    include = ImageChops.subtract(include, masked_here)
    compared = _count(include)
    diff = ImageChops.difference(a, b)
    dr, dg, db = diff.split()
    channel = ImageChops.lighter(ImageChops.lighter(dr, dg), db)
    differs = channel.point(lambda v: 255 if v > tolerance else 0)
    differs = ImageChops.multiply(differs, include)
    differing = _count(differs)
    ratio = differing / compared if compared else 0.0
    if diff_out is not None:
        Path(diff_out).parent.mkdir(parents=True, exist_ok=True)
        grey = Image.blend(b.convert("L").convert("RGB"), Image.new("RGB", (w, h), "white"), 0.6)
        grey.paste((120, 160, 255), mask=ImageChops.subtract(Image.new("L", (w, h), 255),
                                                             ImageChops.add(include, differs)))  # fmt: skip
        grey.paste((255, 0, 0), mask=differs)
        grey.save(diff_out)
    if compared == 0:
        status, note = BLOCKED, ("nothing is left to compare: the region is all excluded or "
                                 "masked")  # fmt: skip
    elif mode == "exact":
        status = PASS if differing == 0 else FAIL
        note = None if differing == 0 else f"{differing} pixel(s) differ; exact allows none"
    else:
        status = PASS if ratio <= max_ratio else FAIL
        note = None if status == PASS else f"{ratio:.4%} differ; the threshold is {max_ratio:.4%}"
    if masked and status == PASS:
        note = f"{masked} masked pixel(s) of the source were excluded, not compared"
    return {"status": status, "compared": compared, "differing": differing, "ratio": ratio,
            "excluded": excluded, "masked": masked, "area": area, "note": note}  # fmt: skip


def _count(mask) -> int:
    hist = mask.histogram()
    return sum(hist[1:])


def shoot(page, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path), animations="disabled", caret="hide", scale="css")
    return path


def check_state(page, st: State, spec: Spec, rec_path: Path, pictures: dict[int, str],
                out_dir: Path, run_viewport: str) -> VisualResult:  # fmt: skip
    vp = st.viewport or spec.viewport
    res = VisualResult(id=st.id, after=str(st.after), viewport=vp, required=st.required,
                       status=BLOCKED, mode=st.mode, max_ratio=st.max_ratio)  # fmt: skip
    if st.problem:
        res.note = st.problem
        return res
    if vp != run_viewport:
        res.status = SKIPPED if not st.required else BLOCKED
        res.note = f"needs a replay at the {vp} size (this one ran at {run_viewport})"
        return res
    img = baseline_image(st, rec_path, pictures, spec.path)
    if img is None or not img.exists():
        res.note = ("no baseline picture: the recorder kept none after this step (screenshots "
                    "off, or the masked picture could not be taken); give an approved image")  # fmt: skip
        return res
    res.before = str(img)
    try:
        excl = boxes(page, st.exclude)
        region = None
        if st.region is not None:
            found = boxes(page, [st.region])
            if len(found) != 1:
                res.note = f"the region matched {len(found)} element(s), not one"
                return res
            region = found[0]
    except Exception as exc:  # noqa: BLE001 - a locator the page cannot read
        res.note = f"an excluded region or the region cannot be located: {exc}"[:300]
        return res
    shot = shoot(page, out_dir / f"{st.id}.after.png")
    res.after_path = str(shot)
    diff = out_dir / f"{st.id}.diff.png"
    got = compare_images(img, shot, exclude=excl, region=region, mode=st.mode,
                         max_ratio=st.max_ratio, tolerance=st.tolerance, diff_out=diff)  # fmt: skip
    res.diff = str(diff) if diff.exists() else None
    for k in ("status", "compared", "differing", "ratio", "excluded", "masked", "note"):
        setattr(res, k, got[k])
    return res


def template(rec) -> str:
    pictures = pictures_of(rec.path)
    states = []
    for seq in sorted(pictures):
        states.append({"id": f"after-step-{seq}", "after": seq, "required": True, "mode": "exact",
                       "tolerance": 0, "exclude": []})  # fmt: skip
    size = None
    if pictures:
        size = picture_size(rec.path / pictures[max(pictures)])
    head = (
        "# Visual baselines: the recorder's pictures (or approved images), compared with the\n"
        "# replica's at the pinned setup. Set exclude: (regions by locator) and the mode before\n"
        "# comparing; delete states that are not required. Masked pixels are always excluded.\n"
        + (f"# The recorder's pictures are {size[0]}x{size[1]}.\n" if size else
           "# This recording has no pictures: every state needs an approved image.\n")
    )  # fmt: skip
    body = {"version": 1, "recording": rec.id, "viewport": "desktop", "fonts": [],
            "states": states}  # fmt: skip
    return head + yaml.safe_dump(body, sort_keys=False, allow_unicode=True)


def write_template(rec, out: Path | None = None) -> Path:
    out = Path(out) if out else rec.path / FILE
    if out.exists():
        raise FileExistsError(f"{out} exists: it is not overwritten (delete it, or --out)")
    out.write_text(template(rec), encoding="utf-8")
    return out
