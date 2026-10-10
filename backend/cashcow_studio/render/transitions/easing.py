"""Easing curves for transitions and camera moves.

Every curve maps linear progress ``x`` in ``[0, 1]`` to eased progress in ``[0, 1]`` with
``f(0) = 0`` and ``f(1) = 1``. The same formulas are written out in
``docs/design/TRANSITION-PARITY.md`` so the browser preview can reproduce them exactly.

The four names the transition catalogue uses (``config/transitions.yaml``) are
``linear``, ``ease_in_out_cubic``, ``ease_out_quart`` and ``snap`` (a quintic ease-in-out:
slow, very fast through the middle, slow, the curve of a whip pan). The others are used
inside individual transitions.
"""

from __future__ import annotations

import math
from collections.abc import Callable

Easing = Callable[[float], float]

DEFAULT_EASING = "ease_in_out_cubic"


def clamp01(x: float) -> float:
    return 0.0 if x <= 0.0 else 1.0 if x >= 1.0 else float(x)


def linear(x: float) -> float:
    return clamp01(x)


def ease_in_quad(x: float) -> float:
    x = clamp01(x)
    return x * x


def ease_out_quad(x: float) -> float:
    x = clamp01(x)
    return 1.0 - (1.0 - x) * (1.0 - x)


def ease_in_cubic(x: float) -> float:
    x = clamp01(x)
    return x * x * x


def ease_out_cubic(x: float) -> float:
    x = clamp01(x)
    return 1.0 - (1.0 - x) ** 3


def ease_in_out_cubic(x: float) -> float:
    x = clamp01(x)
    if x < 0.5:
        return 4.0 * x * x * x
    return 1.0 - (-2.0 * x + 2.0) ** 3 / 2.0


def ease_out_quart(x: float) -> float:
    x = clamp01(x)
    return 1.0 - (1.0 - x) ** 4


def snap(x: float) -> float:
    """Quintic ease-in-out: ``16x^5`` below one half, ``1 - (2 - 2x)^5 / 2`` above."""
    x = clamp01(x)
    if x < 0.5:
        return 16.0 * x**5
    return 1.0 - (-2.0 * x + 2.0) ** 5 / 2.0


def smoothstep(edge0: float, edge1: float, x: float) -> float:
    """GLSL ``smoothstep``: 0 below ``edge0``, 1 above ``edge1``, ``3u^2 - 2u^3`` between."""
    if edge1 == edge0:
        return 0.0 if x < edge0 else 1.0
    u = clamp01((x - edge0) / (edge1 - edge0))
    return u * u * (3.0 - 2.0 * u)


def sine_bump(x: float) -> float:
    """``sin(pi x)`` on ``[0, 1]``: 0 at both ends, 1 in the middle."""
    return math.sin(math.pi * clamp01(x))


EASINGS: dict[str, Easing] = {
    "linear": linear,
    "ease_in_out_cubic": ease_in_out_cubic,
    "ease_out_quart": ease_out_quart,
    "snap": snap,
    "ease_in_quad": ease_in_quad,
    "ease_out_quad": ease_out_quad,
    "ease_in_cubic": ease_in_cubic,
    "ease_out_cubic": ease_out_cubic,
}
CATALOGUE_EASINGS: tuple[str, ...] = ("ease_in_out_cubic", "ease_out_quart", "linear", "snap")


def get_easing(name: str | None, default: str = DEFAULT_EASING) -> Easing:
    """The curve called ``name``; unknown or empty names give ``default``."""
    if name and name in EASINGS:
        return EASINGS[name]
    return EASINGS.get(default, ease_in_out_cubic)


def known_easing(name: str | None, default: str = DEFAULT_EASING) -> str:
    return name if name and name in EASINGS else default


def ease(name: str | None, x: float) -> float:
    return get_easing(name)(x)


def speed(name: str | None, x: float, h: float = 1e-3) -> float:
    """``d ease(x) / dx`` by a central difference (one-sided at the ends): how fast eased
    progress moves at linear progress ``x``. Divide by the duration for "per second"."""
    curve = get_easing(name)
    lo = max(0.0, x - h)
    hi = min(1.0, x + h)
    if hi <= lo:
        return 0.0
    return (curve(hi) - curve(lo)) / (hi - lo)
