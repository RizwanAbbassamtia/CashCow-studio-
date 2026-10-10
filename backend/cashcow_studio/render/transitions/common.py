"""Shared pieces of the transition engine: the per-frame context and fast image helpers.

Frames are ``uint8`` RGB arrays of shape ``(height, width, 3)``. Everything here is
vectorised (NumPy / OpenCV, which run on all CPU cores); there are no per-pixel Python
loops. Conventions shared with ``docs/design/TRANSITION-PARITY.md``:

* ``uv`` is the pixel centre in ``[0, 1]``: ``u = (x + 0.5) / W``, ``v = (y + 0.5) / H``,
  origin at the top-left, ``v`` growing downwards (a GLSL port flips ``v``).
* Pixel sizes in the parameters are "pixels at 1080p": they are multiplied by
  ``min(W, H) / 1080`` so a 480p proxy and a 4K master get the same look.
* "Linear light" means gamma 2.2: ``lin = c ** 2.2`` on 0-1 values and back with ``1/2.2``.
* Angles are in degrees, positive = clockwise on screen.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import cv2
import numpy as np

from . import easing as _easing

REFERENCE_PX = 1080
GAMMA = 2.2
_LIN_LUT_F64 = (np.arange(256, dtype=np.float64) / 255.0) ** GAMMA
_LIN_LUT = _LIN_LUT_F64.astype(np.float32)
BORDER = cv2.BORDER_REFLECT_101


@dataclass
class FrameContext:
    """What a transition function may know besides the two frames."""

    width: int
    height: int
    t: float
    """Eased progress in [0, 1] (what the formulas use)."""
    t_raw: float = 0.0
    """Linear progress in [0, 1]: frame time / transition duration."""
    duration_s: float = 0.6
    fps: float = 30.0
    easing: str = "linear"
    params: Mapping[str, Any] = field(default_factory=dict)
    seed: int = 0
    cache: dict[str, Any] = field(default_factory=dict)
    """Survives from frame to frame of one transition: static maps live here."""

    @property
    def scale(self) -> float:
        return max(min(self.width, self.height) / REFERENCE_PX, 1e-3)

    def px(self, value_at_1080p: float) -> float:
        return float(value_at_1080p) * self.scale

    def _first(self, names: str | tuple[str, ...]) -> Any:
        for name in (names,) if isinstance(names, str) else names:
            if name in self.params and self.params[name] is not None:
                return self.params[name]
        return None

    def num(self, names: str | tuple[str, ...], default: float) -> float:
        """A number param; ``names`` may list aliases (the catalogue's name first)."""
        value = self._first(names)
        if value is None or isinstance(value, bool):
            return float(default)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return float(default)
        return number if math.isfinite(number) else float(default)

    def flag(self, names: str | tuple[str, ...], default: bool) -> bool:
        value = self._first(names)
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            return value != 0
        if isinstance(value, str):
            return value.strip().lower() not in ("", "0", "false", "no", "off")
        return default

    def span(self, names: str | tuple[str, ...], default: tuple[float, float]
             ) -> tuple[float, float]:
        """A ``[low, high]`` param (a single number gives ``[n, n]``)."""
        value = self._first(names)
        try:
            if isinstance(value, list | tuple) and len(value) == 2:
                low, high = float(value[0]), float(value[1])
            elif value is not None and not isinstance(value, bool):
                low = high = float(value)
            else:
                return default
        except (TypeError, ValueError):
            return default
        if not (math.isfinite(low) and math.isfinite(high)):
            return default
        return (min(low, high), max(low, high))

    def text(self, names: str | tuple[str, ...], default: str) -> str:
        value = self._first(names)
        return str(value) if isinstance(value, str) and value else default

    def colour(self, names: str | tuple[str, ...], default: tuple[float, float, float]
               ) -> tuple[float, float, float]:
        """A 0-1 RGB triple from a param given as ``[r, g, b]`` (0-1 or 0-255) or ``#rrggbb``."""
        value = self._first(names)
        return default if value is None else parse_rgb(value, default)

    @property
    def speed(self) -> float:
        """How fast eased progress moves, per second, at this frame."""
        return _easing.speed(self.easing, self.t_raw) / max(self.duration_s, 1e-3)


def parse_rgb(value: Any, default: tuple[float, float, float]) -> tuple[float, float, float]:
    if isinstance(value, str):
        text = value.strip().lstrip("#")
        if len(text) == 6:
            try:
                return tuple(int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
            except ValueError:
                return default
        return default
    if isinstance(value, list | tuple) and len(value) == 3:
        try:
            numbers = [float(v) for v in value]
        except (TypeError, ValueError):
            return default
        if max(numbers) > 1.0:
            numbers = [n / 255.0 for n in numbers]
        return tuple(min(max(n, 0.0), 1.0) for n in numbers)  # type: ignore[return-value]
    return default


# Frames ----------------------------------------------------------------------------------------


def as_frame(image: np.ndarray) -> np.ndarray:
    """A contiguous ``uint8`` RGB frame (grey and RGBA are converted)."""
    frame = np.asarray(image)
    if frame.dtype != np.uint8:
        frame = np.clip(frame, 0, 255).astype(np.uint8)
    if frame.ndim == 2:
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
    elif frame.shape[2] == 4:
        frame = np.ascontiguousarray(frame[:, :, :3])
    return np.ascontiguousarray(frame)


def solid(height: int, width: int, rgb: tuple[float, float, float]) -> np.ndarray:
    frame = np.empty((height, width, 3), dtype=np.uint8)
    frame[:] = [int(round(min(max(c, 0.0), 1.0) * 255)) for c in rgb]
    return frame


def to_linear(frame: np.ndarray) -> np.ndarray:
    """``uint8`` display values -> ``float32`` linear light (gamma 2.2) in [0, 1]."""
    return cv2.LUT(frame, _LIN_LUT)


def from_linear(light: np.ndarray, gain: float = 1.0) -> np.ndarray:
    """``float32`` linear light -> ``uint8`` display values, times ``gain`` (clipped)."""
    encoded = cv2.pow(light, 1.0 / GAMMA)
    return cv2.convertScaleAbs(encoded, alpha=255.0 * gain)


def to_unit(frame: np.ndarray) -> np.ndarray:
    """``uint8`` -> ``float32`` display values in [0, 1]."""
    out = frame.astype(np.float32)
    out *= 1.0 / 255.0
    return out


def from_unit(values: np.ndarray) -> np.ndarray:
    """``float32`` display values in [0, 1] -> ``uint8`` (rounded, clipped)."""
    return cv2.convertScaleAbs(cv2.max(values, 0.0), alpha=255.0)


def mix(a: np.ndarray, b: np.ndarray, weight: float) -> np.ndarray:
    """``a * (1 - w) + b * w`` in display space (rounded)."""
    if weight <= 0.0:
        return a.copy()
    if weight >= 1.0:
        return b.copy()
    return cv2.addWeighted(a, 1.0 - weight, b, weight, 0.0)


def _linear_table(weight: float, gain: float) -> np.ndarray:
    """``T[a * 256 + b] = clamp(gain * ((1 - w) a^2.2 + w b^2.2)^(1/2.2))`` for 8-bit a, b."""
    blend = (1.0 - weight) * _LIN_LUT_F64[:, None] + weight * _LIN_LUT_F64[None, :]
    encoded = np.power(blend, 1.0 / GAMMA) * (255.0 * gain)
    return np.clip(np.rint(encoded), 0, 255).astype(np.uint8).ravel()


def mix_linear(a: np.ndarray, b: np.ndarray, weight: float, gain: float = 1.0) -> np.ndarray:
    """``a * (1 - w) + b * w`` in linear light, then ``gain`` on the display values. Every
    output value depends only on the two input bytes, so one 256 x 256 table per frame
    gives the exact result for all pixels."""
    if weight <= 0.0 and gain == 1.0:
        return a.copy()
    if weight >= 1.0 and gain == 1.0:
        return b.copy()
    table = _linear_table(min(max(weight, 0.0), 1.0), gain)
    index = np.left_shift(a, 8, dtype=np.uint16)
    np.bitwise_or(index, b, out=index)
    return table[index]


def mix_map(a: np.ndarray, b: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Per-pixel ``a * (1 - alpha) + b * alpha`` (``alpha`` is a float32 ``(H, W)`` map)."""
    alpha = np.ascontiguousarray(alpha, dtype=np.float32)
    return cv2.blendLinear(a, b, 1.0 - alpha, alpha)


# Maps ------------------------------------------------------------------------------------------


@lru_cache(maxsize=8)
def uv_grid(width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """``u`` as a ``(1, W)`` row and ``v`` as an ``(H, 1)`` column of pixel centres."""
    u = ((np.arange(width, dtype=np.float32) + 0.5) / width).reshape(1, width)
    v = ((np.arange(height, dtype=np.float32) + 0.5) / height).reshape(height, 1)
    u.setflags(write=False)
    v.setflags(write=False)
    return u, v


@lru_cache(maxsize=2)
def pixel_grid(width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """Full ``(H, W)`` float32 maps of the pixel x and y coordinates (for ``cv2.remap``)."""
    xs, ys = np.meshgrid(
        np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32)
    )
    xs.setflags(write=False)
    ys.setflags(write=False)
    return xs, ys


def smoothstep_map(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    """GLSL ``smoothstep`` over a float32 array (a new array)."""
    span = edge1 - edge0
    u = np.asarray(x, dtype=np.float32) - np.float32(edge0)
    u *= np.float32(1.0 / span if span else 1e6)
    np.clip(u, 0.0, 1.0, out=u)
    out = np.square(u)
    u *= -2.0
    u += 3.0
    out *= u
    return out


def taper(t: float, edge: float = 0.08) -> float:
    """1 in the middle, easing to 0 at ``t = 0`` and ``t = 1``: keeps effects that follow
    the motion (blur) away from the first and last frame whatever the easing is."""
    return _easing.smoothstep(0.0, edge, t) * _easing.smoothstep(0.0, edge, 1.0 - t)


# Blur and warps --------------------------------------------------------------------------------


def fast_gaussian(frame: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur with standard deviation ``sigma`` pixels. Large blurs run on a smaller
    copy (area-downscaled, blurred, scaled back up), which is visually the same Gaussian at
    a fraction of the cost."""
    if sigma < 0.35:
        return frame
    if sigma <= 3.0:
        return cv2.GaussianBlur(frame, (0, 0), sigma, borderType=BORDER)
    height, width = frame.shape[:2]
    factor = 2 ** int(math.floor(math.log2(sigma / 1.5)))
    factor = max(1, min(factor, 32))
    small_w = max(1, int(round(width / factor)))
    small_h = max(1, int(round(height / factor)))
    small = cv2.resize(frame, (small_w, small_h), interpolation=cv2.INTER_AREA)
    # The area downscale and the linear upscale already blur by about f^2/12 + f^2/6.
    rest = math.sqrt(max(sigma * sigma - factor * factor / 4.0, (0.5 * factor) ** 2)) / factor
    small = cv2.GaussianBlur(small, (0, 0), rest, borderType=BORDER)
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)


def box_blur(frame: np.ndarray, length: float, horizontal: bool) -> np.ndarray:
    """Motion blur along one axis: a centred box of ``length`` pixels (a shutter that is
    open while the picture travels that far)."""
    size = int(round(length))
    if size < 2:
        return frame
    size |= 1
    kernel = (size, 1) if horizontal else (1, size)
    return cv2.blur(frame, kernel, borderType=BORDER)


def centre(width: int, height: int) -> tuple[float, float]:
    """The frame centre in OpenCV pixel coordinates (pixel centres are integers)."""
    return (width - 1) / 2.0, (height - 1) / 2.0


def zoom_rotate_matrix(width: int, height: int, scale: float, degrees_cw: float) -> np.ndarray:
    """The 2x3 matrix that magnifies by ``scale`` and turns clockwise by ``degrees_cw``
    about the frame centre (OpenCV's own angle is counter-clockwise)."""
    return cv2.getRotationMatrix2D(centre(width, height), -degrees_cw, scale)


def zoom_rotate(
    frame: np.ndarray, scale: float, degrees_cw: float = 0.0, interpolation: int = cv2.INTER_CUBIC
) -> np.ndarray:
    if abs(scale - 1.0) < 1e-6 and abs(degrees_cw) < 1e-6:
        return frame
    height, width = frame.shape[:2]
    matrix = zoom_rotate_matrix(width, height, scale, degrees_cw)
    return cv2.warpAffine(frame, matrix, (width, height), flags=interpolation, borderMode=BORDER)


BLUR_SAMPLE_SPACING_PX = 2.0
"""Radial and rotational blur: the largest gap between neighbouring samples at the frame
corner. Wider gaps show as separate ghost copies of sharp detail instead of a smooth streak."""
MAX_BLUR_SAMPLES = 128


def blur_samples(width: int, height: int, scale_span: float, degrees_span: float) -> int:
    """How many samples ``zoom_rotate_blur`` averages: the smallest power of two that keeps
    the samples at most ``BLUR_SAMPLE_SPACING_PX`` apart where they spread furthest (the frame
    corner), from 2 to ``MAX_BLUR_SAMPLES``; 1 (no blur) when the corner moves less than a
    pixel."""
    radius = 0.5 * math.hypot(width, height)
    travel = radius * abs(scale_span) + radius * math.radians(abs(degrees_span))
    if travel < 1.0:
        return 1
    needed = travel / BLUR_SAMPLE_SPACING_PX + 1.0
    steps = min(max(1, math.ceil(math.log2(needed))), int(math.log2(MAX_BLUR_SAMPLES)))
    return 2**steps


def zoom_rotate_blur(
    frame: np.ndarray, scale_span: float, degrees_span: float = 0.0,
    max_samples: int = MAX_BLUR_SAMPLES,
) -> np.ndarray:
    """Radial (zoom) and rotational motion blur about the frame centre: the mean of N copies
    magnified by ``q^j`` and turned by ``j * d`` (j = 0..N-1, ``q^(N-1) = 1 + scale_span``,
    ``(N-1) d = degrees_span``). N comes from :func:`blur_samples` (enough samples for a
    smooth streak, none for a blur under a pixel) and is computed by repeated halving:
    ``log2(N)`` warps of the running mean, so 64 samples cost six warps."""
    height, width = frame.shape[:2]
    samples = min(blur_samples(width, height, scale_span, degrees_span), max(1, max_samples))
    if samples < 2:
        return frame
    steps = int(math.log2(samples))
    samples = 2**steps
    q = (1.0 + scale_span) ** (1.0 / (samples - 1))
    d = degrees_span / (samples - 1)
    out = frame
    k = 1
    for _ in range(steps):
        matrix = zoom_rotate_matrix(width, height, q**k, d * k)
        moved = cv2.warpAffine(out, matrix, (width, height), flags=cv2.INTER_LINEAR,
                               borderMode=BORDER)
        out = cv2.addWeighted(out, 0.5, moved, 0.5, 0.0)
        k *= 2
    return out
