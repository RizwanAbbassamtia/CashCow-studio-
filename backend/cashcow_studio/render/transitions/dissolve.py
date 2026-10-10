"""``film_dissolve``: a gamma-correct crossfade (blended in linear light, so bright and dark
areas pass through each other without the muddy dip of a plain crossfade) with a gentle 3%
luma lift at the mid-point, like light through two exposures.

    out = clamp((1 + lift * sin(pi t)) * ((1 - t) A^2.2 + t B^2.2)^(1/2.2), 0, 1)
"""

from __future__ import annotations

import math

import numpy as np

from .common import FrameContext, mix_linear


def film_dissolve(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    t = ctx.t
    if t <= 0.0:
        return a.copy()
    if t >= 1.0:
        return b.copy()
    lift = ctx.num(("luma_lift", "lift"), 0.03) * math.sin(math.pi * t)
    return mix_linear(a, b, t, gain=1.0 + lift)
