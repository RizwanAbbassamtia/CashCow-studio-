"""Render the animated transition previews the storyboard and edit pickers show.

    .venv/Scripts/python.exe tools/make_transition_previews.py            # every transition
    .venv/Scripts/python.exe tools/make_transition_previews.py --only film_dissolve,dip_black
    .venv/Scripts/python.exe tools/make_transition_previews.py --check    # files present?

For every transition in ``config/transitions.yaml`` (docs/TRANSITIONS-V2-CONTRACT.md section 1)
it renders the transition between two built-in placeholder pictures (A: a warm sunset with a
sun disc and hills, B: a cool night with a snowy peak, a moon and stars, so movement, blur and
wipes are easy to see) with the render engine
(``cashcow_studio.render.transitions.registry``) and writes, into
``frontend/public/transitions/``:

* ``<id>.webp``: animated, 320x180, 24 frames (A held, 22 steps of the transition, B held),
  looping;
* ``<id>.poster.webp``: one still from the middle of the transition, shown until the pointer
  is over the card;
* ``catalogue.json``: the YAML as JSON with the preview URLs, which the front end reads
  (no API route needed). ``previews.engine`` says what drew the previews.

When the engine is not installed yet (or fails on a transition), a pure-numpy stand-in draws
a film dissolve instead and the file is marked ``"preview_engine": "stand-in"``: run the tool
again once the engine is in place. Nothing here touches the network; the output is the same
on every run.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "backend") not in sys.path:  # runs without `pip install -e .` too
    sys.path.insert(0, str(REPO / "backend"))

from cashcow_studio.models.transitions import (  # noqa: E402
    TransitionCatalog,
    TransitionCatalogueFile,
)

CONFIG = REPO / "config" / "transitions.yaml"
OUT_DIR = REPO / "frontend" / "public" / "transitions"
CATALOGUE = "catalogue.json"
WIDTH, HEIGHT = 320, 180
FRAMES = 24
HOLD_A_MS, STEP_MS, HOLD_B_MS = 600, 45, 700
POSTER_EASED = 0.4
"""The poster shows the transition this far through (eased progress), where most of them are
easiest to recognise; dips and the burn are shown earlier, before the frame is all one colour."""
POSTER_EASED_BY_ID: dict[str, float] = {"dip_black": 0.22, "dip_white": 0.22, "film_burn": 0.3}
SUPERSAMPLE = 4
REGISTRY_MODULE = "cashcow_studio.render.transitions.registry"
EASING_MODULE = "cashcow_studio.render.transitions.easing"

Frame = np.ndarray  # uint8, HEIGHT x WIDTH x 3, RGB
RenderFn = Callable[[str, Frame, Frame, float, float], Frame]


# Placeholder pictures ------------------------------------------------------------------------


def _gradient(size: tuple[int, int], top: tuple[int, int, int],
              bottom: tuple[int, int, int]) -> Image.Image:
    width, height = size
    ramp = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None, None]
    colours = (np.array(top, np.float32) * (1 - ramp) + np.array(bottom, np.float32) * ramp)
    return Image.fromarray(np.repeat(colours, width, axis=1).astype(np.uint8), "RGB")


def frame_a() -> Image.Image:
    """Warm sunset: orange-to-plum sky, a sun disc on the left, light streaks, dark hills."""
    w, h = WIDTH * SUPERSAMPLE, HEIGHT * SUPERSAMPLE
    image = _gradient((w, h), (250, 176, 92), (122, 46, 92))
    glow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(glow).ellipse((int(w * 0.12), int(h * 0.18), int(w * 0.46), int(h * 0.78)),
                                 fill=150)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=w * 0.05))
    image = Image.composite(Image.new("RGB", (w, h), (255, 214, 150)), image, glow)
    draw = ImageDraw.Draw(image)
    draw.ellipse((int(w * 0.20), int(h * 0.30), int(w * 0.38), int(h * 0.62)),
                 fill=(255, 236, 178))
    for k, y in enumerate((0.22, 0.30, 0.38)):
        draw.rounded_rectangle((int(w * (0.52 + 0.04 * k)), int(h * y), int(w * 0.94),
                                int(h * y) + int(h * 0.018)), radius=h // 90,
                               fill=(255, 205, 140))
    hills = [(0, h), (0, int(h * 0.74)), (int(w * 0.18), int(h * 0.64)),
             (int(w * 0.36), int(h * 0.72)), (int(w * 0.58), int(h * 0.60)),
             (int(w * 0.80), int(h * 0.70)), (w, int(h * 0.62)), (w, h)]
    draw.polygon(hills, fill=(70, 30, 66))
    draw.rectangle((0, int(h * 0.86), w, h), fill=(48, 20, 48))
    return image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)


def frame_b() -> Image.Image:
    """Cool night: navy-to-teal sky, stars, a moon, a snow-capped peak on the right."""
    w, h = WIDTH * SUPERSAMPLE, HEIGHT * SUPERSAMPLE
    image = _gradient((w, h), (16, 42, 84), (36, 150, 150))
    draw = ImageDraw.Draw(image)
    stars = np.random.default_rng(7)  # a fixed scatter: the same stars on every run
    for x_share, y_share, size in zip(stars.uniform(0.02, 0.98, 28), stars.uniform(0.03, 0.5, 28),
                                      stars.integers(1, 4, 28), strict=True):
        x, y, r = int(w * x_share), int(h * y_share), max(2, int(size) * SUPERSAMPLE // 2)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(230, 240, 255))
    moon = Image.new("L", (w, h), 0)
    moon_draw = ImageDraw.Draw(moon)
    moon_draw.ellipse((int(w * 0.10), int(h * 0.12), int(w * 0.22), int(h * 0.33)), fill=255)
    moon_draw.ellipse((int(w * 0.13), int(h * 0.10), int(w * 0.25), int(h * 0.31)), fill=0)
    image = Image.composite(Image.new("RGB", (w, h), (242, 246, 255)), image, moon)
    draw = ImageDraw.Draw(image)
    peak = [(int(w * 0.38), h), (int(w * 0.70), int(h * 0.26)), (w, int(h * 0.78)), (w, h)]
    draw.polygon(peak, fill=(24, 70, 92))
    snow = [(int(w * 0.60), int(h * 0.45)), (int(w * 0.70), int(h * 0.26)),
            (int(w * 0.81), int(h * 0.47)), (int(w * 0.74), int(h * 0.42)),
            (int(w * 0.70), int(h * 0.48)), (int(w * 0.65), int(h * 0.42))]
    draw.polygon(snow, fill=(236, 246, 250))
    draw.polygon([(0, h), (0, int(h * 0.80)), (int(w * 0.30), int(h * 0.70)),
                  (int(w * 0.52), int(h * 0.84)), (int(w * 0.52), h)], fill=(14, 52, 70))
    return image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)


# Easing --------------------------------------------------------------------------------------


def ease(name: str, t: float) -> float:
    """The easing curves of config/transitions.yaml: the engine's own functions when it is
    installed, else the same formulas (docs/design/TRANSITION-PARITY.md)."""
    t = min(max(float(t), 0.0), 1.0)
    try:
        return float(importlib.import_module(EASING_MODULE).ease(name, t))
    except Exception:  # noqa: BLE001 - no engine: the documented curves
        pass
    if name == "ease_in_out_cubic":
        return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2
    if name == "ease_out_quart":
        return 1 - (1 - t) ** 4
    if name == "snap":  # quintic ease-in-out, the curve of a whip pan
        return 16 * t**5 if t < 0.5 else 1 - (-2 * t + 2) ** 5 / 2
    return t


def poster_progress(transition: Any) -> float:
    """The linear progress at which the poster is drawn: where the transition's easing
    reaches :data:`POSTER_EASED` (bisection; every curve rises from 0 to 1)."""
    target = POSTER_EASED_BY_ID.get(transition.type, POSTER_EASED)
    low, high = 0.0, 1.0
    for _ in range(40):
        middle = (low + high) / 2
        if ease(transition.easing, middle) < target:
            low = middle
        else:
            high = middle
    return round((low + high) / 2, 4)


# The stand-in ----------------------------------------------------------------------------------


def _to_linear(rgb: Frame) -> np.ndarray:
    c = rgb.astype(np.float32) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _to_srgb(linear: np.ndarray) -> Frame:
    c = np.clip(linear, 0.0, 1.0)
    out = np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)
    return np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)


def stand_in_film_dissolve(a: Frame, b: Frame, t: float, luma_lift: float = 0.03) -> Frame:
    """Gamma-correct crossfade (blend in linear light) with a small lift of light at the
    middle: the film_dissolve of the contract, used while the engine is missing."""
    mix = (1.0 - t) * _to_linear(a) + t * _to_linear(b)
    return _to_srgb(mix * (1.0 + luma_lift * math.sin(math.pi * t)))


# The engine ------------------------------------------------------------------------------------


def load_engine() -> tuple[RenderFn | None, str]:
    """``render(id, a, b, t_raw, duration_s) -> frame`` over the engine
    (``cashcow_studio.render.transitions.registry.render_frame``: raw linear progress in, the
    catalogue's easing and params applied by the engine), or ``(None, reason)`` without it."""
    try:
        registry = importlib.import_module(REGISTRY_MODULE)
        render_frame = registry.render_frame
    except Exception as exc:  # noqa: BLE001 - any import problem means "no engine"
        return None, f"{REGISTRY_MODULE} could not be used ({exc.__class__.__name__}: {exc})"
    caches: dict[str, dict[str, Any]] = {}

    def render(tid: str, a: Frame, b: Frame, t_raw: float, duration_s: float) -> Frame:
        frame = render_frame(tid, a, b, t_raw, duration_s=duration_s, fps=30.0, seed=0,
                             cache=caches.setdefault(tid, {}))
        return np.ascontiguousarray(np.asarray(frame, dtype=np.uint8)[:, :, :3])

    return render, f"{REGISTRY_MODULE}.render_frame"


def engine_frame_time(k: int, n: int) -> float:
    """Linear progress of transition frame ``k`` of ``n``: the engine's own sampling (the
    middle of the frame's slot) when it is installed, else the same formula."""
    try:
        return float(importlib.import_module(REGISTRY_MODULE).frame_time(k, n))
    except Exception:  # noqa: BLE001
        return (k + 0.5) / max(n, 1)


# Writing -------------------------------------------------------------------------------------


def progress_steps(frames: int = FRAMES) -> list[float]:
    """Linear progress per frame: A held, the transition sampled like the engine samples a
    cut (the middle of each frame's slot), B held."""
    steps = frames - 2
    return [0.0] + [engine_frame_time(k, steps) for k in range(steps)] + [1.0]


def durations_ms(frames: int = FRAMES) -> list[int]:
    return [HOLD_A_MS] + [STEP_MS] * (frames - 2) + [HOLD_B_MS]


def render_preview(transition: Any, a: Frame, b: Frame, engine: RenderFn | None,
                   frames: int = FRAMES) -> tuple[list[Frame], Frame, str]:
    """The animation frames, the poster and what drew them ("engine" or "stand-in")."""
    def draw(t: float, use_engine: bool) -> Frame:
        if t <= 0.0:
            return a.copy()
        if t >= 1.0:
            return b.copy()
        if use_engine and engine is not None:
            return engine(transition.type, a, b, t, transition.duration_s)
        return stand_in_film_dissolve(a, b, ease(transition.easing, t))

    if engine is not None:
        try:
            images = [draw(t, True) for t in progress_steps(frames)]
            return images, draw(poster_progress(transition), True), "engine"
        except Exception as exc:  # noqa: BLE001 - one broken transition must not stop the rest
            print(f"  {transition.type}: the engine failed ({exc.__class__.__name__}: {exc}); "
                  "drew the stand-in instead", file=sys.stderr)
    images = [draw(t, False) for t in progress_steps(frames)]
    return images, draw(poster_progress(transition), False), "stand-in"


def write_webp(path: Path, images: list[Frame]) -> None:
    pictures = [Image.fromarray(image, "RGB") for image in images]
    pictures[0].save(path, "WEBP", save_all=True, append_images=pictures[1:],
                     duration=durations_ms(len(pictures)), loop=0, quality=78, method=4)


def write_poster(path: Path, image: Frame) -> None:
    Image.fromarray(image, "RGB").save(path, "WEBP", quality=82, method=4)


def load_catalog(config: Path = CONFIG) -> TransitionCatalog:
    """The YAML, strictly validated (a broken file stops the tool with the reason)."""
    data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    TransitionCatalogueFile.model_validate(data)
    return TransitionCatalog.from_mapping(data)


def catalogue_json(catalog: TransitionCatalog, engines: Mapping[str, str]) -> dict[str, Any]:
    data = catalog.to_json()
    for row in data["transitions"]:
        row["preview_engine"] = engines.get(row["id"], "missing")
    kinds = {engines.get(t.type, "missing") for t in catalog.transitions}
    data["previews"] = {
        "engine": kinds.pop() if len(kinds) == 1 else "mixed",
        "frames": FRAMES,
        "width": WIDTH,
        "height": HEIGHT,
        "frame_ms": durations_ms(),
        "poster_eased": POSTER_EASED,
    }
    return data


def previous_engines(out_dir: Path) -> dict[str, str]:
    try:
        data = json.loads((out_dir / CATALOGUE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(row.get("id")): str(row.get("preview_engine") or "stand-in")
            for row in data.get("transitions", []) if isinstance(row, dict)}


def make_previews(out_dir: Path = OUT_DIR, only: Sequence[str] | None = None,
                  stand_in: bool = False, config: Path = CONFIG) -> dict[str, str]:
    """Render the previews and write catalogue.json. Returns ``{id: what drew it}``."""
    catalog = load_catalog(config)
    unknown = [i for i in only or [] if catalog.get(i) is None or catalog.resolve(i) != i]
    if unknown:
        raise SystemExit(f"Unknown transition id(s): {', '.join(unknown)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    engine, source = (None, "stand-in requested") if stand_in else load_engine()
    print(f"Drawing with: {source if engine is not None else 'the film dissolve stand-in'}"
          + ("" if engine is not None else f" ({source})"))
    a = np.asarray(frame_a(), dtype=np.uint8)
    b = np.asarray(frame_b(), dtype=np.uint8)
    engines = previous_engines(out_dir)
    for transition in catalog.transitions:
        if only and transition.type not in only:
            continue
        images, poster, drawn_by = render_preview(transition, a, b, engine)
        write_webp(out_dir / f"{transition.type}.webp", images)
        write_poster(out_dir / f"{transition.type}.poster.webp", poster)
        engines[transition.type] = drawn_by
        print(f"  {transition.type:18s} {drawn_by}")
    engines = {k: v for k, v in engines.items() if catalog.resolve(k) == k}
    (out_dir / CATALOGUE).write_text(
        json.dumps(catalogue_json(catalog, engines), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    stand_ins = sorted(k for k, v in engines.items() if v != "engine")
    if stand_ins:
        print(f"NOTE: {len(stand_ins)} preview(s) use the film dissolve stand-in. Run this tool "
              "again once render/transitions/ is installed.")
    return engines


def check(out_dir: Path = OUT_DIR, config: Path = CONFIG) -> list[str]:
    """Problems with the committed previews (empty list = every id has its files)."""
    catalog = load_catalog(config)
    problems: list[str] = []
    for transition in catalog.transitions:
        for name in (f"{transition.type}.webp", f"{transition.type}.poster.webp"):
            if not (out_dir / name).is_file():
                problems.append(f"missing {name}")
    try:
        data = json.loads((out_dir / CATALOGUE).read_text(encoding="utf-8"))
        listed = [row.get("id") for row in data.get("transitions", [])]
        if listed != catalog.types:
            problems.append("catalogue.json does not list the same transitions as the YAML")
    except (OSError, ValueError):
        problems.append(f"missing or unreadable {CATALOGUE}")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", default="", help="comma-separated ids (default: all)")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="output folder")
    parser.add_argument("--stand-in", action="store_true",
                        help="draw every preview with the film dissolve stand-in")
    parser.add_argument("--check", action="store_true",
                        help="only check that every transition has its preview files")
    options = parser.parse_args(argv)
    if options.check:
        problems = check(options.out)
        for problem in problems:
            print(problem)
        print("All previews present." if not problems else f"{len(problems)} problem(s).")
        return 1 if problems else 0
    only = [part.strip() for part in options.only.split(",") if part.strip()]
    make_previews(options.out, only or None, stand_in=options.stand_in)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
