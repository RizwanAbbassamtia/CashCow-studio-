"""Procedural light effects (no bundled footage): ``light_leak_warm`` and ``film_burn``.

``light_leak_warm``: a linear-light crossfade with two soft warm blobs of light drifting
across the frame, added with a screen blend (display values), strongest at the middle:

    I  = strength * sin(pi t')^2,  t' = t^(ln 0.5 / ln peak_t)   (peak at t = peak_t, 0.5)
    g1 = exp(-|P - c1(t)|^2 / (2 r1^2)),  c1 = (-0.15 + 1.1 t, 0.30 + 0.10 sin(2 pi t)), r1 = 0.45
    g2 = exp(-|P - c2(t)|^2 / (2 r2^2)),  c2 = (1.10 - 0.90 t, 0.85 - 0.35 t),          r2 = 0.35
         (P and c in frame heights: P = (u W/H, v), c.x scaled by W/H; odd seeds mirror c.x)
    L  = min(1, I * (g1 * C1 + 0.8 g2 * C2 + wash * C1))  (C1 = ``color``; display values)
    base = mix_linear(A, B, t)                          (as film_dissolve, without the lift)
    out = base + L (1 - base)                           (screen)

``film_burn``: the first picture burns away behind a ragged glowing edge into warm white,
then the second picture comes up out of the white.

    F = 0.7 * G + 0.3 * fbm(seed)        (G = 0 -> 1 across the frame, left to right)
    WARM = ``color`` (255, 240, 220), HOT = ``edge_color`` (255, 140, 40), w = 0.06
    first half  (s = 2t):     e = -w + s (1 + 2w),  z = (e - F) / w
        burnt = smoothstep(0, 1, z), glow = max(0, 1 - z^2)^2,
        char  = max(0, 1 - ((z + 1.6) / 0.8)^2)^2 * smoothstep(0, 0.15, s)
        out = mix(A, WARM, burnt) * (1 - 0.5 char) + glow * 0.9 * HOT
    second half (s = 2t - 1): out = mix(WARM, B, 1 - (1 - s)^3)
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .common import (
    FrameContext,
    mix,
    mix_linear,
    mix_map,
    smoothstep_map,
    solid,
    uv_grid,
)
from .easing import ease_out_cubic, smoothstep
from .noise import fbm

LEAK_COLOUR_1 = (1.0, 176 / 255, 96 / 255)
LEAK_COLOUR_2 = (1.0, 0.36, 0.12)
WARM_WHITE = (1.0, 240 / 255, 220 / 255)
HOT_GLOW = (1.0, 140 / 255, 40 / 255)


def _rgb(rgb: tuple[float, float, float]) -> np.ndarray:
    return np.array(rgb, dtype=np.float32)


def leak_layer(ctx: FrameContext, t: float, intensity: float) -> np.ndarray:
    """The leak light ``L`` (display values 0-1), ``(h, w, 3)`` float32 at quarter size."""
    width, height = ctx.width, ctx.height
    small_w = max(16, width // 4)
    small_h = max(9, height // 4)
    aspect = width / height
    xs = ((np.arange(small_w, dtype=np.float32) + 0.5) / small_w * aspect).reshape(1, small_w)
    ys = ((np.arange(small_h, dtype=np.float32) + 0.5) / small_h).reshape(small_h, 1)
    mirror = bool(ctx.seed % 2)

    def blob(cx: float, cy: float, radius: float) -> np.ndarray:
        if mirror:
            cx = 1.0 - cx
        dx2 = (xs - np.float32(cx * aspect)) ** 2
        dy2 = (ys - np.float32(cy)) ** 2
        return np.exp((dx2 + dy2) * np.float32(-1.0 / (2.0 * radius * radius)))

    g1 = blob(-0.15 + 1.1 * t, 0.30 + 0.10 * math.sin(2.0 * math.pi * t), ctx.num("radius_1", 0.45))
    g2 = blob(1.10 - 0.90 * t, 0.85 - 0.35 * t, ctx.num("radius_2", 0.35))
    c1 = _rgb(ctx.colour(("color", "colour_1"), LEAK_COLOUR_1))
    c2 = _rgb(ctx.colour("colour_2", LEAK_COLOUR_2))
    wash = ctx.num("wash", 0.06)
    layer = (g1[..., None] * c1 + (0.8 * g2)[..., None] * c2 + np.float32(wash) * c1)
    layer *= np.float32(intensity)
    np.clip(layer, 0.0, 1.0, out=layer)
    return layer.astype(np.float32)


def light_leak_warm(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    t = ctx.t
    if t <= 0.0:
        return a.copy()
    if t >= 1.0:
        return b.copy()
    base = mix_linear(a, b, t)
    peak = min(max(ctx.num("peak_t", 0.5), 0.05), 0.95)
    shaped = t ** (math.log(0.5) / math.log(peak))
    strength = min(max(ctx.num(("strength", "intensity"), 0.85), 0.0), 1.0)
    intensity = strength * math.sin(math.pi * shaped) ** 2
    if intensity < 1.0 / 1024.0:
        return base
    small = leak_layer(ctx, t, intensity)
    light = cv2.resize(cv2.convertScaleAbs(small, alpha=255.0), (ctx.width, ctx.height),
                       interpolation=cv2.INTER_LINEAR)
    # screen in 8 bits: base + L (255 - base) / 255
    return cv2.add(base, cv2.multiply(cv2.bitwise_not(base), light, scale=1.0 / 255.0))


def burn_field(ctx: FrameContext) -> np.ndarray:
    key = "burn:field"
    field = ctx.cache.get(key)
    if field is None:
        width, height = ctx.width, ctx.height
        u, v = uv_grid(width, height)
        direction = ctx.text("direction", "right")
        gradients = {"right": u, "left": 1.0 - u, "down": v, "up": 1.0 - v}
        grad = np.broadcast_to(gradients.get(direction, u), (height, width))
        noise = fbm(width, height, ctx.num("cells", 3.0), int(ctx.num("octaves", 4)), ctx.seed)
        mix_noise = min(max(ctx.num("roughness", 0.3), 0.0), 1.0)
        field = ((1.0 - mix_noise) * grad + mix_noise * noise).astype(np.float32)
        ctx.cache[key] = field
    return field


def _plate(ctx: FrameContext, name: str, rgb: tuple[float, float, float]) -> np.ndarray:
    key = f"burn:{name}:{rgb}"
    plate = ctx.cache.get(key)
    if plate is None:
        plate = solid(ctx.height, ctx.width, rgb)
        ctx.cache[key] = plate
    return plate


def film_burn(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    t = ctx.t
    if t <= 0.0:
        return a.copy()
    if t >= 1.0:
        return b.copy()
    warm_rgb = ctx.colour(("color", "colour"), WARM_WHITE)
    warm = _plate(ctx, "warm", warm_rgb)
    if t >= 0.5:
        s = (t - 0.5) / 0.5
        return mix(warm, b, ease_out_cubic(s))
    s = t / 0.5
    w = min(max(ctx.num("edge", 0.06), 1e-3), 0.5)
    field = burn_field(ctx)
    e = -w + s * (1.0 + 2.0 * w)
    z = (np.float32(e) - field) * np.float32(1.0 / w)
    burnt = smoothstep_map(0.0, 1.0, z)
    out = mix_map(a, warm, burnt)
    char_amount = 0.5 * smoothstep(0.0, 0.15, s)
    if char_amount > 0.0:
        ring = 1.0 - ((z + np.float32(1.6)) * np.float32(1.25)) ** 2
        np.maximum(ring, 0.0, out=ring)
        ring *= ring
        keep = 1.0 - np.float32(char_amount) * ring
        black = _plate(ctx, "black", (0.0, 0.0, 0.0))
        out = cv2.blendLinear(out, black, keep, 1.0 - keep)
    glow = 1.0 - z * z
    np.maximum(glow, 0.0, out=glow)
    glow *= glow
    hot = ctx.colour(("edge_color", "glow"), HOT_GLOW)
    strength = ctx.num("glow_strength", 0.9) * 255.0
    layer = cv2.merge([cv2.convertScaleAbs(glow, alpha=strength * c) for c in hot])
    return cv2.add(out, layer)
