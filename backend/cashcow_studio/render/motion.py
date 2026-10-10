"""Motion v2: the camera move over each picture, as maths every renderer shares.

docs/TRANSITIONS-V2-CONTRACT.md section 2; the formulas are written out for the M5 preview
engine in ``docs/design/MOTION-PARITY.md``.

A scene's move is two rectangles of its picture, ``start`` and ``end`` (``(x, y, w, h)`` in
any pixel space: source-image pixels in ``timeline.json``, canvas pixels for FFmpeg,
working pixels for the Python renderer), an easing curve and a preset that names the feel.
At progress ``t`` in ``[0, 1]`` the visible window is

* ``E = ease(t)``                      (``ease_in_out_cubic`` unless the preset says otherwise)
* centre ``c = c0 + (c1 - c0) * E``    (a straight line between the two centres)
* size ``w = w0 * (w1 / w0) ** E``     (the zoom runs at an even rate: equal steps of scale)
* ``handheld`` adds a smooth wobble of ``A * w`` (``A`` = 0.4 %) made of three sines per axis

Frame ``k`` of a clip of ``n`` frames is ``t = k / (n - 1)`` and ``k / fps`` seconds: the
path spans every frame of the scene clip, its transition overlaps included, so the slow
start and end of the easing fall under the cuts.

The same path comes out three ways: :meth:`MotionPath.rect` (Python, sub-pixel; used by
``render/parallax/`` and the scene clips), :func:`zoompan_expressions` (FFmpeg ``zoompan``,
whose own window is whole pixels) and the M5 preview. For a timeline scene,
``scene_path(scene.motion, image_size, frame_size, seed=scene.index)`` gives the path in
source pixels and :func:`canvas_path` the same path on an FFmpeg canvas.

2.5D parallax moves every pixel with the camera, scaled by ``(depth - mean_depth) *
strength``: :func:`parallax_offsets` is that formula and :meth:`MotionPath.camera` gives the
camera terms it needs. The per-frame warp lives in ``render/parallax/warp.py``.

The timeline builder is the only place that assigns rectangles to scenes: it starts from
the preset rectangles in :data:`PRESETS` and runs :func:`apply_continuity` (alternate
directions; a zoom-through cut leaves a scene that ends zoomed in and lands on one that
starts wide; whips follow the outgoing pan) before converting them to pixels.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any, Literal

from ..models.timeline import (
    DEFAULT_MOTION_STYLE,
    DEFAULT_PARALLAX_STRENGTH,
    MotionStyle,
    normalize_motion_style,
)
from .geometry import cover_region

log = logging.getLogger(__name__)

Rect = tuple[float, float, float, float]
Energy = Literal["calm", "medium", "high"]
Axis = Literal["x", "y", "z"]

KEN_BURNS: MotionStyle = "ken_burns"
PARALLAX: MotionStyle = "parallax"
DEFAULT_EASING = "ease_in_out_cubic"
HANDHELD_AMPLITUDE = 0.004
"""Handheld wobble: 0.4 % of the visible width at most."""
PARALLAX_REFERENCE_MOVE = 0.18
"""A camera move of this many frame widths (about the travel of ``pan_left``) moves a pixel
one full depth unit nearer than average by ``strength`` of the frame width."""
RENDER_FILE = "render.yaml"

# Handheld wobble: per axis, three sines (weights add up to 1) with frequencies in Hz.
_SHAKE_WEIGHTS: tuple[float, float, float] = (0.55, 0.3, 0.15)
_SHAKE_HZ_X: tuple[float, float, float] = (0.23, 0.57, 1.13)
_SHAKE_HZ_Y: tuple[float, float, float] = (0.31, 0.67, 1.31)
_GOLDEN = 0.6180339887498949


# Easing ---------------------------------------------------------------------------------------


EASINGS: tuple[str, ...] = (
    "ease_in_out_cubic",
    "ease_out_quart",
    "ease_in_out_sine",
    "linear",
    "snap",
)
"""The curves a camera move may use. ``ease_in_out_cubic``, ``ease_out_quart``, ``linear``
and ``snap`` are the same formulas as ``render/transitions/easing.py``."""


def known_easing(name: str | None) -> str:
    return name if name in EASINGS else DEFAULT_EASING


def ease(name: str | None, t: float) -> float:
    """Eased progress for linear progress ``t`` (clamped to ``[0, 1]``)."""
    x = 0.0 if t <= 0.0 else 1.0 if t >= 1.0 else float(t)
    curve = known_easing(name)
    if curve == "linear":
        return x
    if curve == "ease_out_quart":
        return 1.0 - (1.0 - x) ** 4
    if curve == "ease_in_out_sine":
        return (1.0 - math.cos(math.pi * x)) / 2.0
    if curve == "snap":
        return 16.0 * x**5 if x < 0.5 else 1.0 - (2.0 - 2.0 * x) ** 5 / 2.0
    return 4.0 * x**3 if x < 0.5 else 1.0 - (2.0 - 2.0 * x) ** 3 / 2.0


def ease_expr(name: str | None, t: str) -> str:
    """The same curve as an FFmpeg expression of ``t`` (an expression already in ``[0, 1]``)."""
    curve = known_easing(name)
    if curve == "linear":
        return f"({t})"
    if curve == "ease_out_quart":
        return f"(1-pow(1-{t},4))"
    if curve == "ease_in_out_sine":
        return f"((1-cos(PI*{t}))/2)"
    if curve == "snap":
        return f"if(lt({t},0.5),16*pow({t},5),1-pow(2-2*{t},5)/2)"
    return f"if(lt({t},0.5),4*pow({t},3),1-pow(2-2*{t},3)/2)"


def frame_progress(frame: int, frames: int) -> float:
    """``t`` of frame ``frame`` (0-based) in a clip of ``frames`` frames."""
    if frames <= 1:
        return 0.0
    return min(max(frame / (frames - 1), 0.0), 1.0)


# Presets ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MotionPreset:
    """A named camera move: two fraction rectangles of the picture (``[x, y, w, h]`` in 0-1
    of the part of the picture that covers the frame), an energy and an easing."""

    id: str
    label: str
    energy: Energy
    start: Rect
    end: Rect
    easing: str = DEFAULT_EASING
    flips: tuple[tuple[str, str], ...] = ()
    """``(axis, preset)``: the preset that moves the other way along ``axis``."""
    handheld: bool = False
    feel: str = ""

    def flip_to(self, axis: str) -> str | None:
        return next((target for name, target in self.flips if name == axis), None)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "energy": self.energy,
            "start_rect": list(self.start),
            "end_rect": list(self.end),
            "easing": self.easing,
            "handheld": self.handheld,
            "feel": self.feel,
        }


_FULL: Rect = (0.0, 0.0, 1.0, 1.0)

PRESETS: dict[str, MotionPreset] = {
    preset.id: preset
    for preset in (
        MotionPreset("zoom_in", "Zoom in", "medium", _FULL, (0.1, 0.1, 0.8, 0.8),
                     flips=(("z", "zoom_out"),), feel="a steady push towards the middle"),
        MotionPreset("zoom_out", "Zoom out", "medium", (0.1, 0.1, 0.8, 0.8), _FULL,
                     flips=(("z", "zoom_in"),), feel="pulls back to show the whole picture"),
        MotionPreset("pan_left", "Pan left", "medium", (0.15, 0.0, 0.85, 1.0),
                     (0.0, 0.0, 0.85, 1.0), flips=(("x", "pan_right"),),
                     feel="the camera slides to the left"),
        MotionPreset("pan_right", "Pan right", "medium", (0.0, 0.0, 0.85, 1.0),
                     (0.15, 0.0, 0.85, 1.0), flips=(("x", "pan_left"),),
                     feel="the camera slides to the right"),
        MotionPreset("pan_up", "Pan up", "medium", (0.0, 0.15, 1.0, 0.85),
                     (0.0, 0.0, 1.0, 0.85), flips=(("y", "pan_down"),),
                     feel="the camera slides up"),
        MotionPreset("pan_down", "Pan down", "medium", (0.0, 0.0, 1.0, 0.85),
                     (0.0, 0.15, 1.0, 0.85), flips=(("y", "pan_up"),),
                     feel="the camera slides down"),
        MotionPreset("hold", "Hold still", "calm", _FULL, _FULL,
                     feel="no movement; lets a strong picture breathe"),
        MotionPreset("slow_push", "Slow push in", "calm", _FULL, (0.04, 0.04, 0.92, 0.92),
                     feel="a barely noticeable push in"),
        MotionPreset("drift", "Drift", "calm", (0.0, 0.03, 0.94, 0.94),
                     (0.05, 0.03, 0.94, 0.94), easing="ease_in_out_sine",
                     flips=(("x", "drift"),), feel="a very slow sideways float"),
        MotionPreset("tilt_up", "Tilt up", "medium", (0.1, 0.2, 0.8, 0.8),
                     (0.11, 0.0, 0.78, 0.78), flips=(("y", "tilt_down"),),
                     feel="the camera looks up through the picture"),
        MotionPreset("tilt_down", "Tilt down", "medium", (0.1, 0.0, 0.8, 0.8),
                     (0.11, 0.22, 0.78, 0.78), flips=(("y", "tilt_up"),),
                     feel="the camera looks down through the picture"),
        MotionPreset("dolly_in", "Dolly in", "medium", _FULL, (0.13, 0.07, 0.78, 0.78),
                     flips=(("z", "dolly_out"),), feel="moves in with a slight drift up"),
        MotionPreset("dolly_out", "Dolly out", "medium", (0.13, 0.07, 0.78, 0.78), _FULL,
                     flips=(("z", "dolly_in"),), feel="moves back with a slight drift down"),
        MotionPreset("handheld", "Handheld", "medium", (0.02, 0.02, 0.96, 0.96),
                     (0.02, 0.02, 0.96, 0.96), handheld=True,
                     feel="a tiny natural wobble, like a camera held by hand"),
    )
}
PRESET_IDS: tuple[str, ...] = tuple(PRESETS)
NEW_PRESET_IDS: tuple[str, ...] = (
    "drift", "tilt_up", "tilt_down", "dolly_in", "dolly_out", "handheld",
)
"""The presets Motion v2 adds to the eight of M2."""


def preset_spec(preset: str | None) -> MotionPreset | None:
    return PRESETS.get(str(preset or "").strip().lower().replace("-", "_").replace(" ", "_"))


def preset_rects(preset: str | None) -> tuple[Rect, Rect] | None:
    """The preset's fraction rectangles ``(start, end)``; ``None`` for an unknown preset."""
    spec = preset_spec(preset)
    return (spec.start, spec.end) if spec else None


def preset_energy(preset: str | None) -> Energy:
    spec = preset_spec(preset)
    return spec.energy if spec else "medium"


def presets_json() -> list[dict[str, Any]]:
    """Every preset as plain data (for the storyboard pickers and the preview engine)."""
    return [preset.to_json() for preset in PRESETS.values()]


# The path ---------------------------------------------------------------------------------------


def _centre(rect: Sequence[float]) -> tuple[float, float]:
    return float(rect[0]) + float(rect[2]) / 2.0, float(rect[1]) + float(rect[3]) / 2.0


def _phase(seed: int, index: int) -> float:
    """A repeatable phase in radians: ``2 pi * frac(0.618... * (seed + 1) * (index + 1))``."""
    value = _GOLDEN * (int(seed) + 1) * (index + 1)
    return 2.0 * math.pi * (value - math.floor(value))


def shake_offsets(time_s: float, seed: int) -> tuple[float, float]:
    """The handheld wobble at ``time_s`` seconds, each axis in ``[-1, 1]``.

    ``x = sum(wi * sin(2 pi fi t + phase(seed, i)))`` with ``w = (0.55, 0.3, 0.15)`` and
    ``f = (0.23, 0.57, 1.13)`` Hz; ``y`` the same with ``f = (0.31, 0.67, 1.31)`` Hz and the
    phases ``phase(seed, i + 3)``."""
    x = sum(
        w * math.sin(2.0 * math.pi * hz * time_s + _phase(seed, i))
        for i, (w, hz) in enumerate(zip(_SHAKE_WEIGHTS, _SHAKE_HZ_X, strict=True))
    )
    y = sum(
        w * math.sin(2.0 * math.pi * hz * time_s + _phase(seed, i + 3))
        for i, (w, hz) in enumerate(zip(_SHAKE_WEIGHTS, _SHAKE_HZ_Y, strict=True))
    )
    return x, y


@dataclass(frozen=True)
class CameraTerms:
    """Where the camera is relative to the middle of its move (eased progress 0.5):
    ``px, py`` in frame widths (the window centre's offset divided by the window width
    there), ``zoom`` in natural-log units of scale (positive = closer than the middle)."""

    px: float = 0.0
    py: float = 0.0
    zoom: float = 0.0

    @property
    def is_still(self) -> bool:
        return abs(self.px) < 1e-9 and abs(self.py) < 1e-9 and abs(self.zoom) < 1e-9


@dataclass(frozen=True)
class MotionPath:
    """One camera move in one pixel space (see the module docstring for the maths)."""

    start: Rect
    end: Rect
    easing: str = DEFAULT_EASING
    handheld: float = 0.0
    """Wobble amplitude as a share of the visible width (0 = none, 0.004 for handheld)."""
    seed: int = 0
    bounds: Rect | None = None
    """When given, every rectangle is kept inside ``(x, y, w, h)`` (the picture or canvas)."""

    @classmethod
    def for_preset(
        cls,
        preset: str | None,
        start: Sequence[float],
        end: Sequence[float],
        *,
        seed: int = 0,
        bounds: Sequence[float] | None = None,
        easing: str | None = None,
        handheld_amplitude: float = HANDHELD_AMPLITUDE,
    ) -> MotionPath:
        """The path a renderer draws for a timeline scene: the preset's easing (unless
        ``easing`` is given) and the wobble for ``handheld``."""
        spec = preset_spec(preset)
        return cls(
            start=_rect(start),
            end=_rect(end),
            easing=known_easing(easing or (spec.easing if spec else DEFAULT_EASING)),
            handheld=float(handheld_amplitude) if spec is not None and spec.handheld else 0.0,
            seed=int(seed),
            bounds=_rect(bounds) if bounds is not None else None,
        )

    # Geometry ------------------------------------------------------------------------------

    def eased(self, t: float) -> float:
        return ease(self.easing, t)

    def _size(self, e: float) -> tuple[float, float]:
        w0, h0 = max(self.start[2], 1e-9), max(self.start[3], 1e-9)
        w1, h1 = max(self.end[2], 1e-9), max(self.end[3], 1e-9)
        return w0 * (w1 / w0) ** e, h0 * (h1 / h0) ** e

    def _centre_at(self, e: float) -> tuple[float, float]:
        (cx0, cy0), (cx1, cy1) = _centre(self.start), _centre(self.end)
        return cx0 + (cx1 - cx0) * e, cy0 + (cy1 - cy0) * e

    def _clamp(self, x: float, y: float, w: float, h: float) -> Rect:
        if self.bounds is None:
            return (x, y, w, h)
        bx, by, bw, bh = self.bounds
        x = bx if w >= bw else min(max(x, bx), bx + bw - w)
        y = by if h >= bh else min(max(y, by), by + bh - h)
        return (x, y, w, h)

    def rect(self, t: float, time_s: float = 0.0) -> Rect:
        """The window ``(x, y, w, h)`` at progress ``t`` (and ``time_s`` for the wobble)."""
        e = self.eased(t)
        w, h = self._size(e)
        cx, cy = self._centre_at(e)
        if self.handheld:
            sx, sy = shake_offsets(time_s, self.seed)
            cx += self.handheld * w * sx
            cy += self.handheld * w * sy
        return self._clamp(cx - w / 2.0, cy - h / 2.0, w, h)

    def frame_rect(self, frame: int, frames: int, fps: float) -> Rect:
        return self.rect(frame_progress(frame, frames), frame / max(float(fps), 1e-6))

    def frame_rects(self, frames: int, fps: float) -> list[Rect]:
        """The window for every frame of a clip of ``frames`` frames."""
        return [self.frame_rect(k, frames, fps) for k in range(max(int(frames), 0))]

    def width_range(self) -> tuple[float, float]:
        """The narrowest and the widest window of the move (the zoom is monotonic)."""
        return min(self.start[2], self.end[2]), max(self.start[2], self.end[2])

    def camera(self, t: float, time_s: float = 0.0) -> CameraTerms:
        """The parallax camera terms at progress ``t`` (see :class:`CameraTerms`)."""
        x, y, w, h = self.rect(t, time_s)
        w_ref, h_ref = self._size(0.5)
        cx_ref, cy_ref = self._centre_at(0.5)
        ref_x, ref_y, _w, _h = self._clamp(cx_ref - w_ref / 2.0, cy_ref - h_ref / 2.0, w_ref,
                                           h_ref)
        cx_ref, cy_ref = ref_x + w_ref / 2.0, ref_y + h_ref / 2.0
        w_ref = max(w_ref, 1e-9)
        return CameraTerms(
            px=(x + w / 2.0 - cx_ref) / w_ref,
            py=(y + h / 2.0 - cy_ref) / w_ref,
            zoom=math.log(w_ref / max(w, 1e-9)),
        )

    def frame_camera(self, frame: int, frames: int, fps: float) -> CameraTerms:
        return self.camera(frame_progress(frame, frames), frame / max(float(fps), 1e-6))

    # Other pixel spaces --------------------------------------------------------------------

    def scaled(self, factor: float, origin: tuple[float, float] = (0.0, 0.0)) -> MotionPath:
        """The same move in another pixel space: ``(p - origin) * factor`` (for example
        source-image pixels -> canvas or working pixels)."""
        ox, oy = origin

        def convert(rect: Sequence[float]) -> Rect:
            x, y, w, h = (float(v) for v in rect)
            return ((x - ox) * factor, (y - oy) * factor, w * factor, h * factor)

        return replace(
            self,
            start=convert(self.start),
            end=convert(self.end),
            bounds=convert(self.bounds) if self.bounds is not None else None,
        )


def _rect(values: Sequence[float]) -> Rect:
    x, y, w, h = (float(v) for v in values)
    return (x, y, w, h)


def scene_path(
    scene_motion: Any,
    image_size: tuple[int, int],
    frame_size: tuple[int, int],
    *,
    seed: int = 0,
    config: MotionConfig | None = None,
) -> MotionPath:
    """The path of a timeline scene's ``motion`` (``TimelineMotion``: preset and source-pixel
    rectangles) in source-image pixels, kept inside the picture's cover region. Use the
    scene index as ``seed`` (it only moves the handheld wobble)."""
    region = cover_region(image_size[0], image_size[1], frame_size[0], frame_size[1])
    amplitude = (config or load_motion_config()).handheld_amplitude
    return MotionPath.for_preset(
        scene_motion.preset, scene_motion.start_rect, scene_motion.end_rect, seed=seed,
        bounds=region, handheld_amplitude=amplitude,
    )


def canvas_path(path: MotionPath, canvas: tuple[int, int]) -> MotionPath:
    """A source-pixel path (bounded by the cover region, as :func:`scene_path` makes it) on a
    canvas: the cover region scaled to ``canvas`` (what ``scale`` + ``crop`` give FFmpeg)."""
    if path.bounds is None:
        raise ValueError("the path needs its cover region as bounds")
    rx, ry, rw, _rh = path.bounds
    return path.scaled(float(canvas[0]) / max(rw, 1e-9), (rx, ry))


def frame_rects(
    start: Sequence[float],
    end: Sequence[float],
    frames: int,
    *,
    fps: float = 30.0,
    preset: str | None = None,
    easing: str | None = None,
    seed: int = 0,
    bounds: Sequence[float] | None = None,
) -> list[Rect]:
    """The window ``(x, y, w, h)`` of every frame of a clip (same space as the rectangles)."""
    path = MotionPath.for_preset(preset, start, end, seed=seed, bounds=bounds, easing=easing)
    return path.frame_rects(frames, fps)


# Parallax displacement (the formula; render/parallax/warp.py applies it) ------------------------


def parallax_gain(strength: float, out_width: float) -> float:
    """Output pixels a pixel moves per unit of ``(depth - mean) * camera``."""
    return float(strength) * float(out_width) / PARALLAX_REFERENCE_MOVE


def parallax_offsets(
    depth: Any,
    mean_depth: float,
    nx: Any,
    ny: Any,
    camera: CameraTerms,
    *,
    strength: float,
    out_width: float,
) -> tuple[Any, Any]:
    """The extra screen movement of pixels (output pixels) on top of the camera move.

    ``offset = gain * (depth - mean_depth) * (n * zoom - (px, py))`` with
    ``gain = strength * out_width / 0.18``, ``depth`` in 0-1 (1 = nearest),
    ``n = (p - frame centre) / (out_width / 2)`` (both axes divided by half the width).
    Works on floats and on NumPy arrays alike."""
    gain = parallax_gain(strength, out_width) * (depth - mean_depth)
    return gain * (nx * camera.zoom - camera.px), gain * (ny * camera.zoom - camera.py)


# FFmpeg zoompan -------------------------------------------------------------------------------


def _num(value: float) -> str:
    """A number for an FFmpeg expression: plain decimal, no exponent, no locale."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-", "-0") else "0"


@dataclass(frozen=True)
class ZoompanExprs:
    """``zoompan`` option values (plain FFmpeg expressions, commas unescaped)."""

    z: str
    x: str
    y: str
    frames: int

    def for_filter(self) -> tuple[str, str, str]:
        """The three values escaped for a filter graph written to a script file, the way
        ``render/ffmpeg.py`` writes them (commas as ``\\,`` inside single quotes)."""
        return (
            self.z.replace(",", "\\,"),
            self.x.replace(",", "\\,"),
            self.y.replace(",", "\\,"),
        )


def zoompan_expressions(
    path: MotionPath,
    *,
    frames: int,
    canvas_w: float,
    fps: float = 30.0,
    frame_var: str = "on",
) -> ZoompanExprs:
    """FFmpeg ``zoompan`` expressions that draw ``path`` (given in canvas pixels, the picture
    already scaled to cover a ``canvas_w`` wide canvas) over ``frames`` output frames.

    ``z = canvas_w / w(t)``, ``x = cx(t) - iw/zoom/2``, ``y = cy(t) - ih/zoom/2`` with
    ``t = min(on/(frames-1), 1)``, so ``iw/zoom`` is the Python window width exactly.
    FFmpeg keeps the window inside the canvas and rounds it to whole pixels itself."""
    var = frame_var
    t = f"min({var}/{frames - 1},1)" if frames > 1 else "0"
    e = ease_expr(path.easing, t)
    w0 = max(path.start[2], 1e-9)
    ratio = max(path.end[2], 1e-9) / w0
    (cx0, cy0), (cx1, cy1) = _centre(path.start), _centre(path.end)
    z = f"{_num(canvas_w)}/({_num(w0)}*pow({_num(ratio)},{e}))"
    x = f"{_num(cx0)}+({_num(cx1 - cx0)})*{e}-iw/zoom/2"
    y = f"{_num(cy0)}+({_num(cy1 - cy0)})*{e}-ih/zoom/2"
    if path.handheld:
        rate = max(float(fps), 1e-6)

        def wobble(hz: tuple[float, float, float], offset: int) -> str:
            terms = [
                f"{_num(w)}*sin({_num(2.0 * math.pi * f / rate)}*{var}+"
                f"{_num(_phase(path.seed, i + offset))})"
                for i, (w, f) in enumerate(zip(_SHAKE_WEIGHTS, hz, strict=True))
            ]
            return "+".join(terms)

        amp = _num(path.handheld)
        x += f"+{amp}*iw/zoom*({wobble(_SHAKE_HZ_X, 0)})"
        y += f"+{amp}*iw/zoom*({wobble(_SHAKE_HZ_Y, 3)})"
    return ZoompanExprs(z=z, x=x, y=y, frames=int(frames))


def zoompan_filter(
    path: MotionPath,
    *,
    frames: int,
    canvas: tuple[int, int],
    size: tuple[int, int],
    fps: int,
) -> str:
    """A complete ``zoompan=...`` filter for a filter script (one still picture in, ``frames``
    frames of ``size`` out)."""
    exprs = zoompan_expressions(path, frames=frames, canvas_w=canvas[0], fps=fps)
    z, x, y = exprs.for_filter()
    return (
        f"zoompan=z='{z}':x='{x}':y='{y}':d={max(int(frames), 1)}"
        f":s={int(size[0])}x{int(size[1])}:fps={int(fps)}"
    )


# Continuity rules (used by the timeline builder) ---------------------------------------------


@dataclass
class ScenePlan:
    """One scene's move while the builder decides it: fraction rectangles of the picture,
    the preset, the transition out and what the reviewer locked."""

    preset: str
    start: Rect
    end: Rect
    transition: str = ""
    motion_locked: bool = False
    transition_locked: bool = False
    notes: list[str] = field(default_factory=list)


_MOVE_MIN = 0.02
"""Below this a move has no direction (in window widths or log-scale units)."""
_PAN_MIN = 0.05
"""A sideways move this large (in window widths) counts as a pan for a whip."""


def _components(start: Sequence[float], end: Sequence[float]) -> tuple[float, float, float]:
    (cx0, cy0), (cx1, cy1) = _centre(start), _centre(end)
    mean_w = max((float(start[2]) + float(end[2])) / 2.0, 1e-9)
    mean_h = max((float(start[3]) + float(end[3])) / 2.0, 1e-9)
    dz = math.log(max(float(start[2]), 1e-9) / max(float(end[2]), 1e-9))
    return (cx1 - cx0) / mean_w, (cy1 - cy0) / mean_h, dz


def move_direction(start: Sequence[float], end: Sequence[float]) -> tuple[Axis, int] | None:
    """The main direction of a move: ``("x", +1)`` = the window travels right (the picture
    slides left), ``("y", +1)`` = travels down, ``("z", +1)`` = zooms in. ``None`` = still."""
    dx, dy, dz = _components(start, end)
    axis, value = max((("x", dx), ("y", dy), ("z", dz)), key=lambda item: abs(item[1]))
    if abs(value) < _MOVE_MIN:
        return None
    return axis, (1 if value > 0 else -1)  # type: ignore[return-value]


def camera_pan(start: Sequence[float], end: Sequence[float]) -> tuple[Axis, int] | None:
    """The sideways direction of a move (zoom ignored), when it pans at least 5 %."""
    dx, dy, _dz = _components(start, end)
    axis, value = max((("x", dx), ("y", dy)), key=lambda item: abs(item[1]))
    if abs(value) < _PAN_MIN:
        return None
    return axis, (1 if value > 0 else -1)  # type: ignore[return-value]


def zooms_in(start: Sequence[float], end: Sequence[float]) -> bool:
    return float(end[2]) < float(start[2]) * 0.97


def starts_wide(start: Sequence[float]) -> bool:
    return float(start[2]) >= 0.93 and float(start[3]) >= 0.93


WHIP_DIRECTIONS: tuple[str, ...] = ("left", "right", "up", "down")
_WHIP_FOR_PAN: dict[tuple[str, int], str] = {
    ("x", 1): "left",
    ("x", -1): "right",
    ("y", 1): "up",
    ("y", -1): "down",
}
"""The camera travelling right makes the picture slide left, so the whip goes left."""
_PAN_FOR_WHIP: dict[str, str] = {
    "left": "pan_right", "right": "pan_left", "up": "pan_down", "down": "pan_up",
}
_LEGACY_ZOOM_THROUGH = {"zoomin"}


def _transition_key(name: str | None) -> str:
    return str(name or "").strip().lower().replace("-", "_").replace(" ", "_")


def is_zoom_through(name: str | None) -> bool:
    key = _transition_key(name)
    return key == "zoom_through" or key in _LEGACY_ZOOM_THROUGH


def whip_direction(name: str | None) -> str | None:
    """``"left"`` for ``whip_left`` and so on; ``None`` for anything that is not a whip."""
    key = _transition_key(name)
    if key.startswith("whip_") and key[5:] in WHIP_DIRECTIONS:
        return key[5:]
    return None


def mirror_rect(rect: Sequence[float], axis: str) -> Rect:
    x, y, w, h = (float(v) for v in rect)
    if axis == "x":
        return (round(1.0 - x - w, 6), y, w, h)
    if axis == "y":
        return (x, round(1.0 - y - h, 6), w, h)
    return (x, y, w, h)


def _set(plan: ScenePlan, preset: str, start: Rect, end: Rect, note: str) -> None:
    plan.preset, plan.start, plan.end = preset, start, end
    plan.notes.append(note)


def _use_preset(plan: ScenePlan, preset: str, note: str) -> bool:
    spec = PRESETS.get(preset)
    if spec is None:
        return False
    _set(plan, preset, spec.start, spec.end, note)
    return True


def _flip(plan: ScenePlan, axis: str, note: str) -> bool:
    """Send the move the other way along ``axis``; False when the preset cannot."""
    spec = preset_spec(plan.preset)
    target = spec.flip_to(axis) if spec else None
    if target is None:
        return False
    if axis == "z":
        start, end = plan.end, plan.start
    else:
        start, end = mirror_rect(plan.start, axis), mirror_rect(plan.end, axis)
    _set(plan, target, start, end, note)
    return True


def _zoom_in_choice(plan: ScenePlan) -> str:
    spec = preset_spec(plan.preset)
    flipped = spec.flip_to("z") if spec else None
    if flipped and zooms_in(PRESETS[flipped].start, PRESETS[flipped].end):
        return flipped
    return "slow_push" if spec is not None and spec.energy == "calm" else "zoom_in"


def _wide_start_choice(plan: ScenePlan) -> str:
    spec = preset_spec(plan.preset)
    flipped = spec.flip_to("z") if spec else None
    if flipped and starts_wide(PRESETS[flipped].start):
        return flipped
    return "slow_push" if spec is not None and spec.energy == "calm" else "zoom_in"


def apply_continuity(plans: list[ScenePlan]) -> list[str]:
    """Make neighbouring moves and cuts agree (docs/TRANSITIONS-V2-CONTRACT.md section 2).

    1. A ``zoom_through`` cut leaves a scene that ends zoomed in and lands on a scene that
       starts wide.
    2. A whip follows the outgoing scene's pan: the camera travelling right means
       ``whip_left`` (the picture keeps sliding left). The whip turns unless the reviewer
       locked the transition; then the move turns unless that is locked too.
    3. Neighbouring moves alternate direction (a pan right is not followed by another pan
       right, a zoom in not by another zoom in) where the preset has an opposite.

    Locked moves and locked transitions are never changed. Returns plain-English notes
    (also kept on each plan)."""
    pinned: set[int] = set()
    count = len(plans)
    for i in range(count - 1):
        if not is_zoom_through(plans[i].transition):
            continue
        before, after = plans[i], plans[i + 1]
        if not zooms_in(before.start, before.end) and not before.motion_locked:
            choice = _zoom_in_choice(before)
            _use_preset(before, choice, f"Scene {i + 1} now ends zoomed in ({choice}) so the "
                                        "zoom-through cut carries its push on.")
        pinned.add(i)
        if not starts_wide(after.start) and not after.motion_locked:
            choice = _wide_start_choice(after)
            _use_preset(after, choice, f"Scene {i + 2} now starts on the whole picture "
                                       f"({choice}) so the zoom-through cut lands wide.")
        pinned.add(i + 1)

    for i, plan in enumerate(plans):
        wanted = whip_direction(plan.transition)
        if wanted is None:
            continue
        pan = camera_pan(plan.start, plan.end)
        if pan is None:
            continue
        follows = _WHIP_FOR_PAN[pan]
        if follows == wanted:
            pinned.add(i)
            continue
        if not plan.transition_locked:
            plan.transition = f"whip_{follows}"
            plan.notes.append(
                f"The cut after scene {i + 1} now whips {follows} to follow the camera."
            )
            pinned.add(i)
        elif not plan.motion_locked and i not in pinned:
            target = _PAN_FOR_WHIP[wanted]
            target_axis = "x" if wanted in ("left", "right") else "y"
            note = f"Scene {i + 1} now pans the way the locked whip after it goes."
            if pan[0] != target_axis or not _flip(plan, target_axis, note):
                _use_preset(plan, target, note)
            pinned.add(i)

    previous: tuple[Axis, int] | None = None
    for i, plan in enumerate(plans):
        direction = move_direction(plan.start, plan.end)
        if i > 0 and direction is not None and direction == previous:
            note_here = f"Scene {i + 1} now moves the other way to scene {i}."
            if not plan.motion_locked and i not in pinned:
                if _flip(plan, direction[0], note_here):
                    direction = move_direction(plan.start, plan.end)
            else:
                earlier = plans[i - 1]
                before_that = (
                    move_direction(plans[i - 2].start, plans[i - 2].end) if i >= 2 else None
                )
                flipped_dir = (direction[0], -direction[1])
                if (
                    not earlier.motion_locked
                    and (i - 1) not in pinned
                    and before_that != flipped_dir
                ):
                    _flip(earlier, direction[0],
                          f"Scene {i} now moves the other way to scene {i + 1}.")
        previous = direction

    notes = [note for plan in plans for note in plan.notes]
    for note in notes:
        log.info("motion continuity: %s", note)
    return notes


# Settings (config/render.yaml: motion and parallax) -------------------------------------------

INTERPOLATIONS: tuple[str, ...] = ("lanczos", "cubic", "linear")


@dataclass(frozen=True)
class MotionConfig:
    """The ``motion`` and ``parallax`` sections of ``config/render.yaml``."""

    style: MotionStyle = DEFAULT_MOTION_STYLE
    easing: str = DEFAULT_EASING
    handheld_amplitude: float = HANDHELD_AMPLITUDE
    interpolation: str = "lanczos"
    """Ken Burns frames drawn in Python: lanczos (sharpest), cubic or linear."""
    proxy_interpolation: str = "linear"
    parallax_strength: float = DEFAULT_PARALLAX_STRENGTH
    parallax_interpolation: str = "cubic"
    grid_step_px: int = 8
    """The parallax displacement is computed every ``grid_step_px`` output pixels at 1080p."""
    iterations: int = 2
    inpaint_radius: int = 3
    object_scale: float = 0.25
    """Near objects narrower than this share of the picture's short side get their own layer."""
    layer_threshold: float = 0.08
    edge_threshold: float = 0.1
    max_overscan: float = 0.03
    mesh: tuple[int, int] = (64, 36)


def _float(data: dict[str, Any], key: str, fallback: float, low: float, high: float) -> float:
    try:
        value = float(data.get(key, fallback))
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(value):
        return fallback
    return min(max(value, low), high)


def _int(data: dict[str, Any], key: str, fallback: int, low: int, high: int) -> int:
    try:
        value = int(data.get(key, fallback))
    except (TypeError, ValueError):
        return fallback
    return min(max(value, low), high)


def _choice(data: dict[str, Any], key: str, fallback: str, allowed: Sequence[str]) -> str:
    value = str(data.get(key) or fallback).strip().lower()
    return value if value in allowed else fallback


def motion_config_from(data: dict[str, Any] | None) -> MotionConfig:
    """A :class:`MotionConfig` from the parsed ``render.yaml`` (defaults fill every gap)."""
    data = data if isinstance(data, dict) else {}
    motion = data.get("motion") if isinstance(data.get("motion"), dict) else {}
    parallax = data.get("parallax") if isinstance(data.get("parallax"), dict) else {}
    base = MotionConfig()
    mesh = parallax.get("mesh")
    mesh_pair = base.mesh
    if isinstance(mesh, list | tuple) and len(mesh) == 2:
        try:
            mesh_pair = (max(2, int(mesh[0])), max(2, int(mesh[1])))
        except (TypeError, ValueError):
            mesh_pair = base.mesh
    return MotionConfig(
        style=normalize_motion_style(motion.get("style")),
        easing=known_easing(str(motion.get("easing") or base.easing)),
        handheld_amplitude=_float(motion, "handheld_amplitude", base.handheld_amplitude, 0, 0.05),
        interpolation=_choice(motion, "interpolation", base.interpolation, INTERPOLATIONS),
        proxy_interpolation=_choice(
            motion, "proxy_interpolation", base.proxy_interpolation, INTERPOLATIONS
        ),
        parallax_strength=_float(parallax, "strength", base.parallax_strength, 0.0, 0.2),
        parallax_interpolation=_choice(
            parallax, "interpolation", base.parallax_interpolation, INTERPOLATIONS
        ),
        grid_step_px=_int(parallax, "grid_step_px", base.grid_step_px, 2, 64),
        iterations=_int(parallax, "iterations", base.iterations, 1, 6),
        inpaint_radius=_int(parallax, "inpaint_radius", base.inpaint_radius, 1, 15),
        object_scale=_float(parallax, "object_scale", base.object_scale, 0.05, 0.6),
        layer_threshold=_float(parallax, "layer_threshold", base.layer_threshold, 0.01, 0.5),
        edge_threshold=_float(parallax, "edge_threshold", base.edge_threshold, 0.01, 0.8),
        max_overscan=_float(parallax, "max_overscan", base.max_overscan, 0.0, 0.1),
        mesh=mesh_pair,
    )


@lru_cache(maxsize=1)
def load_motion_config() -> MotionConfig:
    from ..llm.config import load_yaml

    return motion_config_from(load_yaml(RENDER_FILE))


def clear_caches() -> None:
    load_motion_config.cache_clear()


def resolve_style(*choices: Any) -> MotionStyle:
    """The first motion style that is set among ``choices`` (for example the storyboard's
    own choice, then the channel's default, then ``config/render.yaml``)."""
    for choice in choices:
        if choice is None or (isinstance(choice, str) and not choice.strip()):
            continue
        return normalize_motion_style(choice)
    return load_motion_config().style
