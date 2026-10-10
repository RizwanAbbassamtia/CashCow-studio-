"""``blur_dissolve``: the first picture defocuses (Gaussian blur 0 -> ``blur_px``) while it
fades out, the second comes into focus (``blur_px`` -> 0) while it fades in; the crossfade
is in linear light like ``film_dissolve``.

    sigma_A = blur_px * s * t,  sigma_B = blur_px * s * (1 - t)   (s = min(W, H) / 1080)
    out = mix_linear(gauss(A, sigma_A), gauss(B, sigma_B), t)
"""

from __future__ import annotations

import numpy as np

from .common import FrameContext, fast_gaussian, mix_linear


def blur_dissolve(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    t = ctx.t
    if t <= 0.0:
        return a.copy()
    if t >= 1.0:
        return b.copy()
    sigma = ctx.px(ctx.num(("max_blur_px", "blur_px"), 24.0))
    soft_a = fast_gaussian(a, sigma * t)
    soft_b = fast_gaussian(b, sigma * (1.0 - t))
    return mix_linear(soft_a, soft_b, t)
