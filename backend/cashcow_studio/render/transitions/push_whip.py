"""``push_*`` and ``whip_*``: both pictures travel as one strip, the second pushing the first
out of the frame.

The strip is ``[A|B]`` (or ``[B|A]``, stacked vertically for up/down) and the frame is a
window sliding along it by ``p`` frame-lengths. Outside the strip (the 2% overshoot of a
push) the picture is mirrored at its edge.

* push: ``p = t + c t^2 (1 - t)``, ``c`` chosen so the strip overshoots by ``overshoot``
  (2%) and settles back; the catalogue easing is ``ease_out_quart``, which starts fast (a
  0.5 s push at 30 fps moves the strip up to a third of the frame between two frames), so a
  light motion blur along the travel keeps that start from stuttering: ``motion_blur`` 0.5
  (a 180-degree film shutter, at most a quarter of the frame; 0 turns it off). It fades
  away as the push slows, so the picture lands crisp.
* whip: ``p = t``; a box motion blur along the travel of
  ``motion_blur * L * |dp/dsec| * shutter_s`` pixels (``motion_blur`` 1, ``shutter_s``
  1/30 s; capped at ``max_blur`` = half the frame, faded out at the first and last frame
  by ``taper``).

Directions name where the content travels: ``push_left`` moves A out to the left and B in
from the right.
"""

from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np

from .common import BORDER, FrameContext, box_blur, taper

PUSH_MOTION_BLUR = 0.5
"""A push's default blur: the shutter open for half of each frame (180 degrees)."""

DIRECTIONS: dict[str, tuple[int, int]] = {
    "left": (-1, 0),
    "right": (1, 0),
    "up": (0, -1),
    "down": (0, 1),
}


def _overshoot_of(c: float) -> float:
    """How far ``t + c t^2 (1 - t)`` rises above 1 on ``[0, 1]``."""
    if c <= 1.0:
        return 0.0
    t_peak = (2.0 * c + (4.0 * c * c + 12.0 * c) ** 0.5) / (6.0 * c)
    t_peak = min(max(t_peak, 0.0), 1.0)
    return t_peak + c * t_peak * t_peak * (1.0 - t_peak) - 1.0


@lru_cache(maxsize=16)
def overshoot_coefficient(overshoot: float) -> float:
    """The ``c`` whose curve overshoots by ``overshoot`` (bisection, 0 -> c = 1)."""
    if overshoot <= 0.0:
        return 0.0
    lo, hi = 1.0, 8.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if _overshoot_of(mid) < overshoot:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def push_position(t: float, overshoot: float) -> float:
    if overshoot <= 0.0:
        return t
    c = overshoot_coefficient(min(overshoot, 0.25))
    return t + c * t * t * (1.0 - t)


def strip_frame(a: np.ndarray, b: np.ndarray, direction: str, p: float) -> np.ndarray:
    """The window onto the ``[A|B]`` strip after travelling ``p`` frame-lengths."""
    height, width = a.shape[:2]
    dx, dy = DIRECTIONS[direction]
    if dx:
        strip = np.hstack((a, b)) if dx < 0 else np.hstack((b, a))
        offset = p * width if dx < 0 else (1.0 - p) * width
        matrix = np.float32([[1.0, 0.0, -offset], [0.0, 1.0, 0.0]])
    else:
        strip = np.vstack((a, b)) if dy < 0 else np.vstack((b, a))
        offset = p * height if dy < 0 else (1.0 - p) * height
        matrix = np.float32([[1.0, 0.0, 0.0], [0.0, 1.0, -offset]])
    return cv2.warpAffine(strip, matrix, (width, height), flags=cv2.INTER_CUBIC,
                          borderMode=BORDER)


def _travel_blur(frame: np.ndarray, ctx: FrameContext, direction: str, dp_dt: float,
                 shutter_s: float, max_share: float) -> np.ndarray:
    horizontal = DIRECTIONS[direction][0] != 0
    length = ctx.width if horizontal else ctx.height
    travel_px = length * abs(dp_dt) * ctx.speed * shutter_s
    blur = min(travel_px, max_share * length) * taper(ctx.t)
    return box_blur(frame, blur, horizontal)


def push(a: np.ndarray, b: np.ndarray, ctx: FrameContext, direction: str) -> np.ndarray:
    t = ctx.t
    if t <= 0.0:
        return a.copy()
    if t >= 1.0:
        return b.copy()
    overshoot = max(ctx.num("overshoot", 0.02), 0.0)
    p = push_position(t, overshoot)
    frame = strip_frame(a, b, direction, p)
    amount = max(ctx.num("motion_blur", PUSH_MOTION_BLUR), 0.0)
    if amount > 0.0:
        h = 1e-3
        lo, hi = max(t - h, 0.0), min(t + h, 1.0)
        dp_dt = (push_position(hi, overshoot) - push_position(lo, overshoot)) / (hi - lo)
        shutter = max(ctx.num("shutter_s", 1.0 / 30.0), 0.0) * amount
        frame = _travel_blur(frame, ctx, direction, dp_dt, shutter, 0.25)
    return frame


def whip(a: np.ndarray, b: np.ndarray, ctx: FrameContext, direction: str) -> np.ndarray:
    t = ctx.t
    if t <= 0.0:
        return a.copy()
    if t >= 1.0:
        return b.copy()
    frame = strip_frame(a, b, direction, t)
    shutter = max(ctx.num("shutter_s", 1.0 / 30.0), 0.0) * max(ctx.num("motion_blur", 1.0), 0.0)
    max_share = min(max(ctx.num("max_blur", 0.5), 0.0), 1.0)
    return _travel_blur(frame, ctx, direction, 1.0, shutter, max_share)


def push_left(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    return push(a, b, ctx, "left")


def push_right(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    return push(a, b, ctx, "right")


def push_up(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    return push(a, b, ctx, "up")


def push_down(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    return push(a, b, ctx, "down")


def whip_left(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    return whip(a, b, ctx, "left")


def whip_right(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    return whip(a, b, ctx, "right")


def whip_up(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    return whip(a, b, ctx, "up")


def whip_down(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    return whip(a, b, ctx, "down")
