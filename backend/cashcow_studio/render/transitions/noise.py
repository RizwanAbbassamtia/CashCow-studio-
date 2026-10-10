"""Deterministic value noise built on a 32-bit integer hash.

GPU ``sin``-based hashes differ between devices; integer multiply/xor/shift do not, so the
browser preview (WebGL2 ``uint``) gets the same numbers as this reference. The formulas are
in ``docs/design/TRANSITION-PARITY.md`` ("Noise").
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

_M1 = np.uint32(0x8DA6B343)
_M2 = np.uint32(0xD8163841)
_M3 = np.uint32(0x9E3779B1)
_M4 = np.uint32(0x5BD1E995)
_INV_24 = np.float32(1.0 / 16777216.0)


def hash_u32(x: np.ndarray | int, y: np.ndarray | int, seed: int) -> np.ndarray:
    """``h = x*M1 ^ y*M2 ^ seed*M3; h ^= h >> 13; h *= M4; h ^= h >> 15`` (all uint32)."""
    xs = np.asarray(x, dtype=np.uint32)
    ys = np.asarray(y, dtype=np.uint32)
    s = np.asarray(seed & 0xFFFFFFFF, dtype=np.uint32)
    with np.errstate(over="ignore"):
        h = (xs * _M1) ^ (ys * _M2) ^ (s * _M3)
        h = h ^ (h >> np.uint32(13))
        h = h * _M4
        h = h ^ (h >> np.uint32(15))
    return h


def hash01(x: np.ndarray | int, y: np.ndarray | int, seed: int) -> np.ndarray:
    """The hash as a float in ``[0, 1)``: the top 24 bits divided by 2^24."""
    return (hash_u32(x, y, seed) >> np.uint32(8)).astype(np.float32) * _INV_24


def rand(step: int, slot: int, seed: int) -> float:
    """One deterministic number in ``[0, 1)`` for (step, slot, seed)."""
    return float(hash01(int(step), int(slot), int(seed)))


def _value_noise(width: int, height: int, cells: float, seed: int) -> np.ndarray:
    """Value noise with ``cells`` lattice cells across the frame height (square cells), the
    lattice values ``hash01(i, j, seed)`` blended with smoothstep weights."""
    aspect = width / height
    px = (np.arange(width, dtype=np.float64) + 0.5) / width * aspect * cells
    py = (np.arange(height, dtype=np.float64) + 0.5) / height * cells
    ix = np.floor(px).astype(np.int64)
    iy = np.floor(py).astype(np.int64)
    fx = (px - ix).astype(np.float32)
    fy = (py - iy).astype(np.float32)
    sx = fx * fx * (3.0 - 2.0 * fx)
    sy = fy * fy * (3.0 - 2.0 * fy)
    x0, x1 = int(ix.min()), int(ix.max()) + 1
    y0, y1 = int(iy.min()), int(iy.max()) + 1
    lx = np.arange(x0, x1 + 1, dtype=np.int64)
    ly = np.arange(y0, y1 + 1, dtype=np.int64)
    lattice = hash01(lx[None, :], ly[:, None], seed)  # (rows, cols)
    cx = ix - x0
    rows = lattice[:, cx] * (1.0 - sx)[None, :] + lattice[:, cx + 1] * sx[None, :]  # (rows, W)
    cy = iy - y0
    top = rows[cy]
    bottom = rows[cy + 1]
    return (top * (1.0 - sy)[:, None] + bottom * sy[:, None]).astype(np.float32)


@lru_cache(maxsize=2)
def fbm(width: int, height: int, cells: float = 3.0, octaves: int = 4, seed: int = 0) -> np.ndarray:
    """Fractal value noise in ``[0, 1]``: octave ``o`` has ``cells * 2^o`` cells, weight
    ``0.5^o``, seed ``seed + 1013 o``; the sum is divided by the sum of the weights."""
    total = np.zeros((height, width), dtype=np.float32)
    weight_sum = 0.0
    for octave in range(max(1, octaves)):
        weight = 0.5**octave
        total += weight * _value_noise(width, height, cells * 2**octave, seed + 1013 * octave)
        weight_sum += weight
    total *= np.float32(1.0 / weight_sum)
    total.setflags(write=False)
    return total
