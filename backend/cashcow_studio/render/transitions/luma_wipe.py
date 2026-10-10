"""``luma_wipe_*``: a soft-edged wipe driven by a gradient map ``G`` (0 where the second
picture appears first, 1 where it appears last) with a 12% feather.

    e = t (1 + f)
    alpha = smoothstep(0, 1, (e - G) / f)          (f = feather = 0.12)
    out = mix(A, B, alpha)

Maps (``u``, ``v`` pixel centres in 0-1, ``v`` down): left ``1 - u`` (the edge travels
leftwards, B enters from the right), right ``u``, up ``1 - v`` (B rises from the bottom),
down ``v``, diag ``(u + v) / 2`` (from the top-left corner).
"""

from __future__ import annotations

import numpy as np

from .common import FrameContext, mix_map, smoothstep_map, uv_grid


def gradient_map(width: int, height: int, direction: str) -> np.ndarray:
    u, v = uv_grid(width, height)
    if direction == "left":
        grad = np.broadcast_to(1.0 - u, (height, width))
    elif direction == "right":
        grad = np.broadcast_to(u, (height, width))
    elif direction == "up":
        grad = np.broadcast_to(1.0 - v, (height, width))
    elif direction == "down":
        grad = np.broadcast_to(v, (height, width))
    elif direction == "diag":
        grad = (u + v) * 0.5
    else:
        raise ValueError(f"unknown wipe direction {direction!r}")
    return np.ascontiguousarray(grad, dtype=np.float32)


def wipe(a: np.ndarray, b: np.ndarray, ctx: FrameContext, direction: str) -> np.ndarray:
    t = ctx.t
    if t <= 0.0:
        return a.copy()
    if t >= 1.0:
        return b.copy()
    feather = min(max(ctx.num("feather", 0.12), 1e-3), 1.0)
    key = f"wipe:{direction}:{feather}"
    grad = ctx.cache.get(key)
    if grad is None:
        grad = gradient_map(ctx.width, ctx.height, direction)
        ctx.cache[key] = grad
    edge = t * (1.0 + feather)
    alpha = smoothstep_map(0.0, 1.0, (np.float32(edge) - grad) * np.float32(1.0 / feather))
    return mix_map(a, b, alpha)


def luma_wipe_left(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    return wipe(a, b, ctx, "left")


def luma_wipe_right(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    return wipe(a, b, ctx, "right")


def luma_wipe_up(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    return wipe(a, b, ctx, "up")


def luma_wipe_down(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    return wipe(a, b, ctx, "down")


def luma_wipe_diag(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    return wipe(a, b, ctx, "diag")
