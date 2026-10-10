"""``spin_zoom``: the first picture spins (up to 12 degrees) and zooms (1.0 -> 1.4) away with
a rotational/radial blur; the second unspins from -8 degrees while settling from 1.3x.

    thetaA = max_angle_deg * t,            sA = 1 + (a_scale_end - 1) * t
    thetaB = b_angle_start_deg * (1 - t),  sB = 1 + (b_scale_start - 1) * (1 - t)
    blur: N samples (as zoom_through), extra turn bt = 3 deg * sin(pi t), extra scale
          0.05 * sin(pi t)
    sX = max(sX, cover(thetaX), cover(thetaX + bt))       (never shows the frame's outside,
          cover(a) = cos|a| + max(W/H, H/W) sin|a|          not even in the blur's last sample)
    m = smoothstep(0.5 - c/2, 0.5 + c/2, t)               (crossfade window c = 0.3)
    out = mix(spinblur(A), spinblur(B), m)

Both pictures share the blur, which is linear, so the engine mixes the two turned pictures
first and blurs once (the same frame at half the cost). Positive angles turn clockwise;
``clockwise: false`` mirrors the spin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .common import FrameContext, mix, zoom_rotate, zoom_rotate_blur
from .easing import smoothstep


def cover_scale(width: int, height: int, degrees: float) -> float:
    """The smallest magnification that keeps a frame turned by ``degrees`` covering it."""
    theta = math.radians(min(abs(degrees), 89.0))
    ratio = max(width / height, height / width)
    return math.cos(theta) + ratio * math.sin(theta)


@dataclass(frozen=True)
class SpinTerms:
    """Where both pictures are at one frame of ``spin_zoom``."""

    scale_a: float
    degrees_a: float
    scale_b: float
    degrees_b: float
    blur_scale: float
    """The blur's extra magnification (its last sample is ``1 + blur_scale`` times bigger)."""
    blur_degrees: float
    """The blur's extra turn (its last sample is turned this much further)."""
    mix: float
    """How much of B is showing (0 = only A)."""


def spin_terms(ctx: FrameContext) -> SpinTerms:
    t = ctx.t
    turn = 1.0 if ctx.flag("clockwise", True) else -1.0
    width, height = ctx.width, ctx.height
    deg_a = ctx.num(("max_angle_deg", "degrees_a"), 12.0) * t * turn
    deg_b = ctx.num(("b_angle_start_deg",), -ctx.num("degrees_b", 8.0)) * (1.0 - t) * turn
    end_a = ctx.num(("a_scale_end",), 1.4)
    start_b = ctx.num(("b_scale_start",), 1.3)
    bump = math.sin(math.pi * t)
    blur_deg = ctx.num("blur_degrees", 3.0) * bump * turn
    blur_scale = max(ctx.num("blur_scale", 0.05), 0.0) * bump
    scale_a = max(1.0 + (end_a - 1.0) * t, cover_scale(width, height, deg_a),
                  cover_scale(width, height, deg_a + blur_deg))
    scale_b = max(1.0 + (start_b - 1.0) * (1.0 - t), cover_scale(width, height, deg_b),
                  cover_scale(width, height, deg_b + blur_deg))
    window = min(max(ctx.num(("crossfade_window", "crossfade"), 0.3), 0.0), 1.0)
    return SpinTerms(
        scale_a=scale_a, degrees_a=deg_a, scale_b=scale_b, degrees_b=deg_b,
        blur_scale=blur_scale, blur_degrees=blur_deg,
        mix=smoothstep(0.5 - window / 2.0, 0.5 + window / 2.0, t),
    )


def spin_zoom(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    if ctx.t <= 0.0:
        return a.copy()
    if ctx.t >= 1.0:
        return b.copy()
    s = spin_terms(ctx)
    if s.mix <= 0.0:
        sharp = zoom_rotate(a, s.scale_a, s.degrees_a)
    elif s.mix >= 1.0:
        sharp = zoom_rotate(b, s.scale_b, s.degrees_b)
    else:
        sharp = mix(zoom_rotate(a, s.scale_a, s.degrees_a),
                    zoom_rotate(b, s.scale_b, s.degrees_b), s.mix)
    return zoom_rotate_blur(sharp, s.blur_scale, s.blur_degrees)
