"""``dip_black`` / ``dip_white``: the first picture fades into a solid colour (ease-in, so it
accelerates into the dip), the colour holds for ``hold`` of the duration, then the second
picture comes out of it (ease-out). Blends are in display space, like a camera fade.

    h0 = 0.5 - hold/2, h1 = 0.5 + hold/2
    t <  h0: out = mix(A, C, (t / h0)^2)
    t <= h1: out = C
    t >  h1: out = mix(C, B, 1 - (1 - (t - h1) / (1 - h1))^2)
"""

from __future__ import annotations

import numpy as np

from .common import FrameContext, mix, solid
from .easing import ease_in_quad, ease_out_quad

BLACK = (0.0, 0.0, 0.0)
WHITE = (1.0, 1.0, 1.0)


def _dip(a: np.ndarray, b: np.ndarray, ctx: FrameContext, default: tuple[float, float, float]
         ) -> np.ndarray:
    t = ctx.t
    if t <= 0.0:
        return a.copy()
    if t >= 1.0:
        return b.copy()
    colour = ctx.colour(("color", "colour"), default)
    key = f"dip:{colour}"
    plate = ctx.cache.get(key)
    if plate is None:
        plate = solid(ctx.height, ctx.width, colour)
        ctx.cache[key] = plate
    hold = min(max(ctx.num("hold", 0.08), 0.0), 0.9)
    h0 = 0.5 - hold / 2.0
    h1 = 0.5 + hold / 2.0
    if t < h0:
        return mix(a, plate, ease_in_quad(t / h0))
    if t <= h1:
        return plate.copy()
    return mix(plate, b, ease_out_quad((t - h1) / (1.0 - h1)))


def dip_black(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    return _dip(a, b, ctx, BLACK)


def dip_white(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    return _dip(a, b, ctx, WHITE)
