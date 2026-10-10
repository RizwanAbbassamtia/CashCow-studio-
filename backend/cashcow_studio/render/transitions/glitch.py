"""``glitch_subtle``: a few frames of digital break-up on the first picture, then a clean cut.

Timing is counted in frames of a 30 fps reference clock on linear progress (so a 60 fps
preview shows the same states for the same time); the strength follows the easing.

    N     = duration_s * 30                                (reference frames)
    G     = lo + floor(rand(0, 99, seed) * (hi - lo + 1))  (``glitch_frames`` [6, 8])
    cut   = min(1, G / N)                                  (linear progress of the clean cut)
    t_raw >= cut: out = B
    else: k = min(floor(t_raw N), G - 1)                   (the glitch state)
          env   = sqrt(min(1, t / ease(cut)))              (builds up into the cut)
          split = round(max_shift_px * s * env * (0.6 + 0.4 rand(k, 0, seed)))
                  red moves right, blue moves left by ``split`` (wrapping round)
          lo_s + floor(rand(k, 1, seed) * (hi_s - lo_s + 1)) bands (``slices`` [3, 5]):
              top = rand(k, 2+3i) H, height = (0.02 + 0.08 rand(k, 3+3i)) H,
              shift = (2 rand(k, 4+3i) - 1) * slice_px * s * env   (slice_px 40)

``rand`` is the integer hash of noise.py; ``s = min(W, H) / 1080``.
"""

from __future__ import annotations

import math

import numpy as np

from .common import FrameContext
from .easing import ease
from .noise import rand

REFERENCE_FPS = 30.0


def glitch_window(ctx: FrameContext) -> tuple[int, float, float]:
    """``(G, N, cut)``: glitched frames, reference frames in the transition, cut position."""
    lo, hi = ctx.span("glitch_frames", (6.0, 8.0))
    lo_i, hi_i = max(1, int(round(lo))), max(1, int(round(hi)))
    count = lo_i + min(int(rand(0, 99, ctx.seed) * (hi_i - lo_i + 1)), hi_i - lo_i)
    total = max(ctx.duration_s * REFERENCE_FPS, 1.0)
    return count, total, min(1.0, count / total)


def glitch_subtle(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    t = ctx.t
    if t <= 0.0:
        return a.copy()
    if t >= 1.0:
        return b.copy()
    count, total, cut = glitch_window(ctx)
    if ctx.t_raw >= cut:
        return b.copy()
    eased_cut = max(ease(ctx.easing, cut), 1e-6)
    env = math.sqrt(min(1.0, t / eased_cut))
    step = min(int(ctx.t_raw * total), count - 1)
    seed = int(ctx.seed)
    height = a.shape[0]
    out = a.copy()
    max_shift = ctx.px(ctx.num(("max_shift_px", "split_px"), 6.0))
    split = int(round(max_shift * env * (0.6 + 0.4 * rand(step, 0, seed))))
    if split >= 1:
        out[:, :, 0] = np.roll(a[:, :, 0], split, axis=1)
        out[:, :, 2] = np.roll(a[:, :, 2], -split, axis=1)
    lo, hi = ctx.span("slices", (3.0, 5.0))
    lo_i, hi_i = max(0, int(round(lo))), max(0, int(round(hi)))
    bands = lo_i + min(int(rand(step, 1, seed) * (hi_i - lo_i + 1)), hi_i - lo_i)
    reach = ctx.px(ctx.num("slice_px", 40.0)) * env
    for band in range(bands):
        top = int(rand(step, 2 + 3 * band, seed) * height)
        size = max(2, int((0.02 + 0.08 * rand(step, 3 + 3 * band, seed)) * height))
        shift = int(round((2.0 * rand(step, 4 + 3 * band, seed) - 1.0) * reach))
        if shift == 0 or top >= height:
            continue
        rows = slice(top, min(height, top + size))
        out[rows] = np.roll(out[rows], shift, axis=1)
    return out
