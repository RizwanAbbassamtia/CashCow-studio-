"""Warping transitions: ``crosswarp``, ``directional_warp`` (both after the MIT-licensed
GL Transitions of the same names, by Eke Peter and pschroen) and ``morph_blend``.

``crosswarp`` (per-pixel progress grows from right to left):

    x  = smoothstep(0, 1, 2t + u - 1)
    out = mix(A((p - .5)(1 - x) + .5), B((p - .5) x + .5), x)

``directional_warp`` (a soft diagonal front; ``direction`` is in GL axes, y up, default
``(-1, 1)``, ``smoothness`` 0.5):

    v = normalize(direction); v /= |v.x| + |v.y|;  d = v . (0.5, 0.5)
    m = 1 - smoothstep(-s, 0, v . (u, 1 - v_img) - (d - 0.5 + t (1 + s)))
    out = mix(A((p - .5)(1 - m) + .5), B((p - .5) m + .5), m)

``morph_blend`` (a soft "morph" without optical flow): the luma difference of the two
pictures pushes pixels along the direction of the local structure, at most ``max_px``
(18 px at 1080p), while they crossfade:

    LA, LB = smooth luma of A and B (0.299 R + 0.587 G + 0.114 B, Gaussian ``smooth_px``)
    n = grad(LA + LB) / sqrt(|grad|^2 + eps^2)
    V = max_px * (LB - LA) * n
    out = mix(A(p + V t), B(p - V (1 - t)), t)
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .common import BORDER, FrameContext, mix, mix_map, pixel_grid, smoothstep_map, uv_grid


def _remap(frame: np.ndarray, map_x: np.ndarray, map_y: np.ndarray,
           interpolation: int = cv2.INTER_CUBIC) -> np.ndarray:
    return cv2.remap(frame, map_x, map_y, interpolation=interpolation, borderMode=BORDER)


def _scaled_maps(width: int, height: int, k: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Source pixel maps for sampling at ``(p - .5) * k + .5`` (``k`` a row or a full map)."""
    u, v = uv_grid(width, height)
    du = (u - np.float32(0.5)) * np.float32(width)
    dv = (v - np.float32(0.5)) * np.float32(height)
    cx = np.float32(0.5 * width - 0.5)
    cy = np.float32(0.5 * height - 0.5)
    map_x = np.ascontiguousarray(np.broadcast_to(du * k + cx, (height, width)), dtype=np.float32)
    map_y = np.ascontiguousarray(np.broadcast_to(dv * k + cy, (height, width)), dtype=np.float32)
    return map_x, map_y


def crosswarp(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    t = ctx.t
    if t <= 0.0:
        return a.copy()
    if t >= 1.0:
        return b.copy()
    width, height = ctx.width, ctx.height
    u, _v = uv_grid(width, height)
    x = smoothstep_map(0.0, 1.0, (2.0 * t - 1.0) + u)  # (1, W)
    if float(x.max()) <= 0.0:
        return a.copy()
    if float(x.min()) >= 1.0:
        return b.copy()
    ax, ay = _scaled_maps(width, height, 1.0 - x)
    bx, by = _scaled_maps(width, height, x)
    alpha = np.ascontiguousarray(np.broadcast_to(x, (height, width)), dtype=np.float32)
    # Where a picture is not warped its map is the identity (exact at whole pixels), so the
    # cheaper bilinear sampling only touches pixels that are moving.
    linear = cv2.INTER_LINEAR
    return mix_map(_remap(a, ax, ay, linear), _remap(b, bx, by, linear), alpha)


def _direction(ctx: FrameContext) -> tuple[float, float]:
    raw = ctx.params.get("direction", (-1.0, 1.0))
    try:
        dx, dy = (float(raw[0]), float(raw[1]))  # type: ignore[index]
    except (TypeError, ValueError, IndexError, KeyError):
        dx, dy = -1.0, 1.0
    length = math.hypot(dx, dy)
    if length < 1e-9:
        dx, dy, length = -1.0, 1.0, math.sqrt(2.0)
    dx, dy = dx / length, dy / length
    norm = abs(dx) + abs(dy)
    return dx / norm, dy / norm


def directional_warp(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    t = ctx.t
    if t <= 0.0:
        return a.copy()
    if t >= 1.0:
        return b.copy()
    width, height = ctx.width, ctx.height
    smooth = max(ctx.num("smoothness", 0.5), 1e-3)
    vx, vy = _direction(ctx)
    d = vx * 0.5 + vy * 0.5
    # m is a smooth ramp (the edge is ``smoothness`` = half the frame wide), so it is
    # computed on a quarter-size grid of the same pixel centres and scaled up.
    small_w, small_h = max(8, width // 4), max(8, height // 4)
    u, v = uv_grid(small_w, small_h)
    front = (np.float32(vx) * u) + (np.float32(vy) * (np.float32(1.0) - v))
    front -= np.float32(d - 0.5 + t * (1.0 + smooth))
    m_small = 1.0 - smoothstep_map(-smooth, 0.0, front)
    if float(m_small.max()) <= 0.0:
        return a.copy()
    if float(m_small.min()) >= 1.0:
        return b.copy()
    m = cv2.resize(m_small, (width, height), interpolation=cv2.INTER_LINEAR)
    ax, ay = _scaled_maps(width, height, 1.0 - m)
    bx, by = _scaled_maps(width, height, m)
    linear = cv2.INTER_LINEAR
    return mix_map(_remap(a, ax, ay, linear), _remap(b, bx, by, linear), m)


def _luma_small(frame: np.ndarray, size: tuple[int, int], sigma: float) -> np.ndarray:
    small = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
    luma = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY).astype(np.float32) * np.float32(1.0 / 255.0)
    if sigma >= 0.35:
        luma = cv2.GaussianBlur(luma, (0, 0), sigma, borderType=BORDER)
    return luma


def morph_field(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> tuple[np.ndarray, np.ndarray]:
    """The displacement ``V`` (full-size x and y maps, pixels) of ``morph_blend``. It is
    computed on a quarter-size copy and scaled up: the field is smooth by construction."""
    width, height = ctx.width, ctx.height
    factor = 4
    size = (max(16, width // factor), max(9, height // factor))
    sigma_small = ctx.px(ctx.num("smooth_px", 12.0)) * size[0] / width
    la = _luma_small(a, size, sigma_small)
    lb = _luma_small(b, size, sigma_small)
    diff = lb - la
    total = la + lb
    # Sobel / 8 = luma change per (small) pixel.
    gx = cv2.Sobel(total, cv2.CV_32F, 1, 0, ksize=3, borderType=BORDER) * np.float32(0.125)
    gy = cv2.Sobel(total, cv2.CV_32F, 0, 1, ksize=3, borderType=BORDER) * np.float32(0.125)
    eps = np.float32(ctx.num("edge_eps", 0.02))
    inv = 1.0 / np.sqrt(gx * gx + gy * gy + eps * eps)
    amount = np.float32(ctx.px(ctx.num(("max_displacement_px", "max_px"), 18.0)))
    vx = cv2.resize(diff * gx * inv * amount, (width, height), interpolation=cv2.INTER_LINEAR)
    vy = cv2.resize(diff * gy * inv * amount, (width, height), interpolation=cv2.INTER_LINEAR)
    return vx, vy


def morph_blend(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    t = ctx.t
    if t <= 0.0:
        return a.copy()
    if t >= 1.0:
        return b.copy()
    vx, vy = morph_field(a, b, ctx)
    xs, ys = pixel_grid(ctx.width, ctx.height)
    moved_a = _remap(a, cv2.scaleAdd(vx, t, xs), cv2.scaleAdd(vy, t, ys))
    moved_b = _remap(b, cv2.scaleAdd(vx, -(1.0 - t), xs), cv2.scaleAdd(vy, -(1.0 - t), ys))
    return mix(moved_a, moved_b, t)
