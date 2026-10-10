"""``zoom_through``: the camera flies into the first picture and out of the second.

    sA = 1 + (a_scale_end - 1) * t           (A magnified about the centre, 1.0 -> 1.25)
    sB = b_scale_start + (1 - b_scale_start) * t   (B settles from 1.15 -> 1.0)
    m  = smoothstep(0.5 - c/2, 0.5 + c/2, t)  (crossfade in the middle c = 40%)
    span = 0.12 * sin(pi t)                   (radial blur: N samples at sX * q^j with
                                               q^(N-1) = 1 + span; ``radial_blur: false`` turns
                                               it off, a number sets the peak span)
    out = mix(zoomblur(A, sA, span), zoomblur(B, sB, span), m)

Every sample magnifies (scale >= 1), so nothing outside the picture is ever shown. The blur
is linear and both pictures share its span, so the engine mixes the two zoomed pictures
first and blurs once: ``zoomblur(mix(zoom(A, sA), zoom(B, sB), m), 1, span)``, the same
frame at half the cost.
"""

from __future__ import annotations

import math

import numpy as np

from .common import FrameContext, mix, zoom_rotate, zoom_rotate_blur
from .easing import smoothstep

DEFAULT_BLUR_SPAN = 0.12


def blur_span(ctx: FrameContext) -> float:
    """The peak span of the radial blur: ``radial_blur`` true (0.12), false or "off" (none),
    or a number (that span)."""
    value = ctx.params.get("radial_blur", ctx.params.get("blur", True))
    if value is None:
        return DEFAULT_BLUR_SPAN
    if isinstance(value, bool):
        return DEFAULT_BLUR_SPAN if value else 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_BLUR_SPAN if ctx.flag(("radial_blur", "blur"), True) else 0.0
    return max(number, 0.0) if math.isfinite(number) else DEFAULT_BLUR_SPAN


def zoom_through(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    t = ctx.t
    if t <= 0.0:
        return a.copy()
    if t >= 1.0:
        return b.copy()
    end_a = max(ctx.num(("a_scale_end",), 1.0 + ctx.num("scale_a", 0.25)), 1.0)
    scale_a = 1.0 + (end_a - 1.0) * t
    start_b = max(ctx.num(("b_scale_start", "scale_b"), 1.15), 1.0)
    scale_b = start_b + (1.0 - start_b) * t
    width = min(max(ctx.num(("crossfade_window", "crossfade"), 0.4), 0.0), 1.0)
    m = smoothstep(0.5 - width / 2.0, 0.5 + width / 2.0, t)
    span = blur_span(ctx) * math.sin(math.pi * t)
    if m <= 0.0:
        sharp = zoom_rotate(a, scale_a)
    elif m >= 1.0:
        sharp = zoom_rotate(b, scale_b)
    else:
        sharp = mix(zoom_rotate(a, scale_a), zoom_rotate(b, scale_b), m)
    return zoom_rotate_blur(sharp, span)
