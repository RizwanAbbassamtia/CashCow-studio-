"""Per-frame warps for the scene clips: the eased Ken Burns move and the 2.5D parallax move.

Frames are ``uint8`` RGB arrays ``(height, width, 3)``; everything is vectorised (OpenCV and
NumPy, all CPU cores), nothing loops over pixels in Python.

**Ken Burns** (:func:`kenburns_frame`): one affine warp per frame from the working picture,
sub-pixel exact (Lanczos by default). Output pixel ``u`` (centre ``u + 0.5``) shows the
picture at ``x + (u + 0.5) * w / W``.

**Parallax** (:func:`prepare_layers` once per scene, :func:`parallax_frame` per frame): every
pixel moves with the camera by ``gain * (depth - mean) * (n * zoom - (px, py))``
(:func:`..motion.parallax_offsets`). To keep near things from smearing over what is behind
them, the picture is split into two layers:

* the *front* layer holds near objects that stand out from their surroundings (a white
  top-hat of the depth map, kept only where a sharp depth edge bounds it), with a soft alpha
  snapped to the picture's edges (guided filter) and rim colours un-mixed from the old
  background; its depth is carried out past each object's edge so the object moves as one
  piece;
* the *back* layer is the whole picture with those objects filled in by edge-extend
  inpainting (``cv2.inpaint`` TELEA, radius 3) and their depth replaced by the background's,
  so what a moving object uncovers looks like the background instead of a stretched smear.

With the camera at the middle of its move the two layers give back the picture exactly.

Per frame each layer's displacement is solved on a coarse grid (every 8 px at 1080p) with
two fixed-point steps (``q = q0 - k (depth(q) - mean) v``), upsampled to full size, and the
layers are drawn with ``cv2.remap`` and blended front over back. Without a sharp near object
the back layer is the picture itself and the front layer is skipped.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from ..motion import CameraTerms, MotionConfig, MotionPath, parallax_gain

INTERPOLATIONS: dict[str, int] = {
    "lanczos": cv2.INTER_LANCZOS4,
    "cubic": cv2.INTER_CUBIC,
    "linear": cv2.INTER_LINEAR,
}
BORDER = cv2.BORDER_REFLECT_101
MAX_DOWNSAMPLE = 1.5
"""The widest window of a move is drawn from at most 1.5 working pixels per output pixel."""
LOW_RES_SHORT_SIDE = 384
"""The depth layers are analysed at this short side (the depth model's own detail level)."""
INPAINT_MAX_WIDTH = 640
"""The background behind near objects is filled at this width at most and scaled back."""


def _bbox(mask: np.ndarray, pad: int) -> tuple[slice, slice] | None:
    """The rows and columns holding ``mask``, grown by ``pad``; ``None`` when empty."""
    rows = np.flatnonzero(mask.any(axis=1))
    if rows.size == 0:
        return None
    cols = np.flatnonzero(mask.any(axis=0))
    height, width = mask.shape[:2]
    return (
        slice(max(int(rows[0]) - pad, 0), min(int(rows[-1]) + pad + 1, height)),
        slice(max(int(cols[0]) - pad, 0), min(int(cols[-1]) + pad + 1, width)),
    )


def interpolation_flag(name: str | None) -> int:
    return INTERPOLATIONS.get(str(name or "").lower(), cv2.INTER_CUBIC)


# The working picture --------------------------------------------------------------------------


@dataclass
class WorkingImage:
    """The part of the picture a move can show, at the resolution the move needs.

    ``pixels[j, i]`` is source pixel ``origin + (i + 0.5, j + 0.5) / factor`` (centres)."""

    pixels: np.ndarray
    factor: float
    """Working pixels per source pixel (the same on both axes)."""
    origin: tuple[float, float]
    """Source-pixel position of the working picture's top-left corner."""

    @property
    def size(self) -> tuple[int, int]:
        return int(self.pixels.shape[1]), int(self.pixels.shape[0])

    def path(self, source_path: MotionPath) -> MotionPath:
        """A move given in source pixels, in working pixels (bounds = the working picture)."""
        moved = source_path.scaled(self.factor, self.origin)
        width, height = self.size
        bounds = moved.bounds
        if bounds is not None:
            bx, by, bw, bh = bounds
            x0, y0 = max(bx, 0.0), max(by, 0.0)
            x1, y1 = min(bx + bw, float(width)), min(by + bh, float(height))
            bounds = (x0, y0, max(x1 - x0, 1.0), max(y1 - y0, 1.0))
        else:
            bounds = (0.0, 0.0, float(width), float(height))
        return MotionPath(moved.start, moved.end, moved.easing, moved.handheld, moved.seed,
                          bounds)

    def rect(self, rect_source: Sequence[float]) -> tuple[float, float, float, float]:
        x, y, w, h = (float(v) for v in rect_source)
        ox, oy = self.origin
        f = self.factor
        return ((x - ox) * f, (y - oy) * f, w * f, h * f)

    def crop_like(self, full: np.ndarray, source_size: tuple[int, int],
                  interpolation: int = cv2.INTER_LINEAR) -> np.ndarray:
        """A map of the whole source picture (any resolution, e.g. its depth map) cut and
        scaled to match :attr:`pixels`."""
        src_w, src_h = source_size
        full_h, full_w = full.shape[:2]
        sx, sy = full_w / max(src_w, 1), full_h / max(src_h, 1)
        width, height = self.size
        ox, oy = self.origin
        f = self.factor
        # Working pixel centre i -> source x = ox + (i + 0.5) / f -> map index x * sx - 0.5.
        matrix = np.array(
            [[sx / f, 0.0, (ox + 0.5 / f) * sx - 0.5], [0.0, sy / f, (oy + 0.5 / f) * sy - 0.5]],
            dtype=np.float64,
        )
        source = full
        if sx / f > 1.5:  # shrink first so a big map is averaged, not sampled
            scale = 1.0 / (sx / f)
            source = cv2.resize(full, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            matrix = np.array(
                [[scale * sx / f, 0.0, (ox + 0.5 / f) * sx * scale - 0.5],
                 [0.0, scale * sy / f, (oy + 0.5 / f) * sy * scale - 0.5]],
                dtype=np.float64,
            )
        return cv2.warpAffine(source, matrix, (width, height),
                              flags=interpolation | cv2.WARP_INVERSE_MAP,
                              borderMode=cv2.BORDER_REPLICATE)


def working_image(
    rgb: np.ndarray,
    region: Sequence[float],
    source_path: MotionPath,
    out_size: tuple[int, int],
    *,
    max_downsample: float = MAX_DOWNSAMPLE,
    margin: float = 0.04,
) -> WorkingImage:
    """Cut the cover region (plus a small margin of real picture where there is one) and
    scale it so the narrowest window of the move has about one working pixel per output
    pixel (never upscaled: the warp itself interpolates) and the widest at most
    ``max_downsample`` (shrunk with area averaging, so nothing aliases)."""
    image_h, image_w = rgb.shape[:2]
    out_w = max(int(out_size[0]), 1)
    rx, ry, rw, rh = (float(v) for v in region)
    w_min, w_max = source_path.width_range()
    factor = min(1.0, out_w / max(w_min, 1e-6), max_downsample * out_w / max(w_max, 1e-6))
    pad_x, pad_y = rw * margin, rh * margin
    x0 = max(int(math.floor(rx - pad_x)), 0)
    y0 = max(int(math.floor(ry - pad_y)), 0)
    x1 = min(int(math.ceil(rx + rw + pad_x)), image_w)
    y1 = min(int(math.ceil(ry + rh + pad_y)), image_h)
    crop = rgb[y0:y1, x0:x1]
    if factor < 0.999:
        # Given fx/fy (and no size), OpenCV maps pixels with exactly this factor; only the
        # output size is rounded.
        pixels = cv2.resize(crop, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    else:
        pixels = np.ascontiguousarray(crop)
        factor = 1.0
    return WorkingImage(pixels=pixels, factor=factor, origin=(float(x0), float(y0)))


# Ken Burns -------------------------------------------------------------------------------------


def rect_affine(rect: Sequence[float], out_size: tuple[int, int]) -> np.ndarray:
    """The 2x3 matrix taking output pixel indices to working pixel indices for a window."""
    x, y, w, h = (float(v) for v in rect)
    out_w, out_h = out_size
    sx, sy = w / out_w, h / out_h
    return np.array(
        [[sx, 0.0, x + 0.5 * sx - 0.5], [0.0, sy, y + 0.5 * sy - 0.5]], dtype=np.float64
    )


def kenburns_frame(
    pixels: np.ndarray,
    rect: Sequence[float],
    out_size: tuple[int, int],
    interpolation: str | None = "lanczos",
) -> np.ndarray:
    """The window ``rect`` (working pixels, sub-pixel) scaled to ``out_size``."""
    return cv2.warpAffine(
        pixels,
        rect_affine(rect, out_size),
        (int(out_size[0]), int(out_size[1])),
        flags=interpolation_flag(interpolation) | cv2.WARP_INVERSE_MAP,
        borderMode=BORDER,
    )


# Parallax layers -------------------------------------------------------------------------------


def smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    u = np.clip((x - edge0) / max(edge1 - edge0, 1e-9), 0.0, 1.0)
    return (u * u * (3.0 - 2.0 * u)).astype(np.float32)


def odd_reflect_pad(values: np.ndarray, radius: int) -> np.ndarray:
    """Pad by point reflection (``2 * edge - mirror``) so a straight ramp stays a ramp
    across the border (an opening then leaves sloping ground alone)."""
    r = int(radius)
    mirror = cv2.copyMakeBorder(values, r, r, r, r, cv2.BORDER_REFLECT_101)
    edge = cv2.copyMakeBorder(values, r, r, r, r, cv2.BORDER_REPLICATE)
    return (2.0 * edge - mirror).astype(np.float32)


def _rect_kernel(radius: int) -> np.ndarray:
    size = 2 * max(int(radius), 0) + 1
    return cv2.getStructuringElement(cv2.MORPH_RECT, (size, size))


@dataclass
class LayerAnalysis:
    """The depth analysis behind the two layers, at low resolution (for tests and logs)."""

    low: np.ndarray
    envelope: np.ndarray
    tophat: np.ndarray
    edges: np.ndarray
    alpha: np.ndarray | None


def analyse_depth(depth_low: np.ndarray, config: MotionConfig) -> LayerAnalysis:
    """Find near objects: the white top-hat of the depth (depth minus its opening with a
    square ``object_scale`` of the short side) above ``layer_threshold``, kept only for the
    connected parts that touch a sharp depth edge (local range above ``edge_threshold``)."""
    low = np.clip(depth_low.astype(np.float32), 0.0, 1.0)
    short = min(low.shape[:2])
    # The opening only needs the coarse shape: run it at half size (a quarter of the work).
    half = cv2.resize(low, (max(low.shape[1] // 2, 4), max(low.shape[0] // 2, 4)),
                      interpolation=cv2.INTER_AREA)
    radius = max(2, int(round(config.object_scale * min(half.shape[:2]))))
    padded = odd_reflect_pad(half, radius)
    opened = cv2.morphologyEx(padded, cv2.MORPH_OPEN, _rect_kernel(radius))
    opened = opened[radius:-radius, radius:-radius]
    # Smooth the blocky opening, but never below it: a blur alone would pull a far sky down
    # into the ground just under the horizon and lift a strip of ground as a "near object".
    smooth = cv2.GaussianBlur(opened, (0, 0), max(radius / 4.0, 1.0))
    envelope = cv2.resize(np.maximum(opened, smooth), (low.shape[1], low.shape[0]),
                          interpolation=cv2.INTER_LINEAR)
    envelope = np.minimum(envelope, low)
    tophat = low - envelope
    edge_radius = max(1, int(round(0.01 * short)))
    kernel = _rect_kernel(edge_radius)
    local_range = cv2.dilate(low, kernel) - cv2.erode(low, kernel)
    edges = local_range > config.edge_threshold
    threshold = config.layer_threshold
    candidate = (tophat > threshold).astype(np.uint8)
    # Thin slivers (a pixel or two along a big depth step) are not objects.
    candidate = cv2.morphologyEx(candidate, cv2.MORPH_OPEN, _rect_kernel(2))
    alpha: np.ndarray | None = None
    if candidate.any():
        _count, labels = cv2.connectedComponents(candidate, connectivity=8)
        keep_ids = np.unique(labels[edges & (candidate > 0)])
        keep_ids = keep_ids[keep_ids > 0]
        if keep_ids.size:
            keep = np.isin(labels, keep_ids).astype(np.uint8)
            keep = cv2.dilate(keep, _rect_kernel(edge_radius + 1))
            soft = smoothstep(0.5 * threshold, 1.5 * threshold, tophat)
            alpha = (soft * keep).astype(np.float32)
            if float(alpha.max()) < 0.5:
                alpha = None
    return LayerAnalysis(low=low, envelope=envelope, tophat=tophat, edges=edges, alpha=alpha)


def refine_alpha(
    alpha: np.ndarray, image: np.ndarray, radius: int, eps: float = 1e-3
) -> np.ndarray:
    """Snap a soft alpha (from the blurry depth edge) to the picture's own edges with a grey
    guided filter (He et al.), only near where the alpha already is."""
    region = _bbox(alpha > 0.01, 4 * radius + 2)
    if region is None:
        return np.zeros_like(alpha, dtype=np.float32)
    rows, cols = region
    patch = np.ascontiguousarray(alpha[rows, cols], dtype=np.float32)
    guide = cv2.cvtColor(np.ascontiguousarray(image[rows, cols]),
                         cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    size = (2 * radius + 1, 2 * radius + 1)

    def box(values: np.ndarray) -> np.ndarray:
        return cv2.boxFilter(values, -1, size, normalize=True, borderType=cv2.BORDER_REFLECT)

    mean_i, mean_p = box(guide), box(patch)
    var_i = box(guide * guide) - mean_i * mean_i
    cov_ip = box(guide * patch) - mean_i * mean_p
    a = cov_ip / (var_i + eps)
    b = mean_p - a * mean_i
    refined = np.clip(box(a) * guide + box(b), 0.0, 1.0)
    near = cv2.dilate((patch > 0.01).astype(np.uint8), _rect_kernel(radius))
    out = np.zeros_like(alpha, dtype=np.float32)
    out[rows, cols] = refined * near
    return out


def fill_behind(
    image: np.ndarray,
    alpha: np.ndarray,
    *,
    radius: int = 3,
    max_width: int = INPAINT_MAX_WIDTH,
) -> tuple[np.ndarray, np.ndarray]:
    """The back plate: the picture with the near objects (``alpha``) filled by edge-extend
    inpainting (``cv2.inpaint`` TELEA) from the background around them.

    The inpainting mask is every pixel the objects touch, grown a little, so only clean
    background feeds the fill; the fill replaces the picture wherever an object has any
    real coverage (alpha above about 0.05) and the faint outer edge keeps the picture.
    :func:`unmix_front` then gives the front layer the colours that make front over back
    equal the picture again when the camera is at the middle of its move. Big pictures are
    filled at ``max_width`` and scaled back (the fill is smooth anyway). Returns
    ``(plate, inpaint mask)``."""
    height, width = alpha.shape[:2]
    grow = max(2, int(round(0.004 * min(height, width))))
    grown = cv2.dilate((alpha > 0.02).astype(np.uint8), _rect_kernel(grow))
    plate = image.copy()
    scale = min(1.0, max_width / max(width, 1))
    # Work on the objects' box plus enough picture around it to feed the fill.
    region = _bbox(grown > 0, int(math.ceil((radius + 4) / scale)))
    if region is None:
        return plate, grown
    rows, cols = region
    crop = np.ascontiguousarray(image[rows, cols])
    crop_mask = np.ascontiguousarray(grown[rows, cols])
    crop_h, crop_w = crop_mask.shape[:2]
    if scale < 1.0:
        small = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        small_mask = cv2.resize(crop_mask, (small.shape[1], small.shape[0]),
                                interpolation=cv2.INTER_NEAREST)
        small_mask = cv2.dilate(small_mask, _rect_kernel(1))
    else:
        small, small_mask = crop, crop_mask
    filled = cv2.inpaint(small, (small_mask > 0).astype(np.uint8) * 255, float(radius),
                         cv2.INPAINT_TELEA)
    if scale < 1.0:
        filled = cv2.resize(filled, (crop_w, crop_h), interpolation=cv2.INTER_CUBIC)
    weight = smoothstep(0.02, 0.1, alpha[rows, cols])
    plate[rows, cols] = cv2.blendLinear(filled, crop, weight, 1.0 - weight)
    return plate, grown


def unmix_front(image: np.ndarray, plate: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Front layer colours from the matting equation ``image = a * front + (1 - a) * plate``:
    an object's soft rim gets its own colour instead of a mix with the old background, so
    nothing of that background travels with the object and front over back still gives the
    picture back exactly when the camera has not moved."""
    front = image.copy()
    region = _bbox(alpha > 0.05, 1)
    if region is None:
        return front
    rows, cols = region
    a = alpha[rows, cols, None].astype(np.float32)
    picture = image[rows, cols].astype(np.float32)
    solved = (picture - (1.0 - a) * plate[rows, cols].astype(np.float32)) / np.maximum(a, 0.05)
    mixed = np.where(a > 0.05, solved, picture)
    front[rows, cols] = np.clip(mixed + 0.5, 0, 255).astype(np.uint8)
    return front


FRONT_REACH = 0.08
"""Near objects' depth is carried this share of the short side out past their edge."""


def front_depth(low: np.ndarray, alpha_low: np.ndarray) -> np.ndarray:
    """The front layer's depth: each near object's own depth (its soft edge flattened by a
    small max filter) inside it, and carried outward past its edge by ``FRONT_REACH`` of the
    short side, so the whole object, edge included, moves as one piece however far the
    camera goes (the layer is invisible outside the object anyway)."""
    short = min(low.shape[:2])
    rim = max(1, int(round(0.015 * short)))
    flat = cv2.dilate(low, _rect_kernel(rim))
    inside = alpha_low > 0.5
    carried = cv2.dilate(np.where(inside, flat, 0.0).astype(np.float32),
                         _rect_kernel(max(rim + 1, int(round(FRONT_REACH * short)))))
    return np.where(inside, flat, carried).astype(np.float32)


@dataclass
class ParallaxLayers:
    image: np.ndarray
    """Front layer colours: the working picture, near objects' soft rims un-mixed from the
    background they were photographed against (:func:`unmix_front`)."""
    plate: np.ndarray
    """Back layer colours: the picture with near objects filled in."""
    alpha: np.ndarray | None
    """Front layer coverage 0-1 at working size; ``None`` = one layer only."""
    depth_back: np.ndarray
    depth_front: np.ndarray | None
    low_scale: tuple[float, float]
    """Low-resolution depth pixels per working pixel (x, y)."""
    mean_depth: float
    depth_range: tuple[float, float]
    stats: dict[str, Any] = field(default_factory=dict)
    coverage: np.ndarray | None = None
    """Where the front layer has anything, at low resolution (1 = maybe, 0 = never): each
    frame draws the front layer only inside the box this lands in."""

    @property
    def layered(self) -> bool:
        return self.alpha is not None


def prepare_layers(
    image: np.ndarray,
    depth: np.ndarray,
    config: MotionConfig,
    *,
    inpaint_max_width: int = INPAINT_MAX_WIDTH,
) -> ParallaxLayers:
    """Split a working picture into the parallax layers (once per scene). ``depth`` is 0..1
    (1 = near) at any size; it is matched to the picture."""
    height, width = image.shape[:2]
    depth = np.asarray(depth, dtype=np.float32)
    if depth.shape[:2] != (height, width):
        depth = cv2.resize(depth, (width, height), interpolation=cv2.INTER_LINEAR)
    depth = np.clip(depth, 0.0, 1.0)
    mean = float(depth.mean())
    scale = min(1.0, LOW_RES_SHORT_SIDE / max(min(height, width), 1))
    low_w, low_h = max(8, int(round(width * scale))), max(8, int(round(height * scale)))
    low = cv2.resize(depth, (low_w, low_h), interpolation=cv2.INTER_AREA)
    low_scale = (low_w / width, low_h / height)
    analysis = analyse_depth(low, config)
    stats: dict[str, Any] = {"layers": 1, "front_share": 0.0, "filled_share": 0.0}
    depth_range = (float(depth.min()), float(depth.max()))
    if analysis.alpha is None:
        return ParallaxLayers(
            image=image, plate=image, alpha=None,
            depth_back=cv2.GaussianBlur(low, (0, 0), 1.0), depth_front=None,
            low_scale=low_scale, mean_depth=mean, depth_range=depth_range, stats=stats,
        )
    alpha_low = analysis.alpha
    alpha = cv2.resize(alpha_low, (width, height), interpolation=cv2.INTER_LINEAR)
    alpha = refine_alpha(alpha, image, max(2, int(round(0.006 * min(height, width)))))
    depth_back = low - alpha_low * (low - analysis.envelope)
    depth_back = cv2.GaussianBlur(depth_back, (0, 0), 1.0)
    depth_front = front_depth(low, alpha_low)
    plate, filled = fill_behind(image, alpha, radius=config.inpaint_radius,
                                max_width=inpaint_max_width)
    front = unmix_front(image, plate, alpha)
    stats = {
        "layers": 2,
        "front_share": round(float((alpha > 0.5).mean()), 4),
        "filled_share": round(float((filled > 0).mean()), 4),
    }
    coverage = cv2.dilate((alpha_low > 0.002).astype(np.float32), _rect_kernel(2))
    return ParallaxLayers(
        image=front, plate=plate, alpha=alpha, depth_back=depth_back, depth_front=depth_front,
        low_scale=low_scale, mean_depth=mean, depth_range=depth_range, stats=stats,
        coverage=coverage,
    )


# Per frame ------------------------------------------------------------------------------------


def grid_step_for(out_size: tuple[int, int], step_at_1080: int) -> int:
    """The displacement grid spacing for a frame size (``step_at_1080`` scaled, at least 2)."""
    return max(2, int(round(step_at_1080 * min(out_size) / 1080.0)))


@dataclass(frozen=True)
class CoarseGrid:
    """The output positions the displacement is solved at, and where the camera alone puts
    them in the working picture."""

    uc: np.ndarray
    """Output x of each grid column, ``(j + 0.5) * W / gw`` (so ``cv2.resize`` interpolates
    the grid exactly)."""
    vc: np.ndarray
    q0x: np.ndarray
    q0y: np.ndarray
    scale: float
    """Working pixels per output pixel (``w / W``)."""


def coarse_grid(rect: Sequence[float], out_size: tuple[int, int], step: int) -> CoarseGrid:
    out_w, out_h = int(out_size[0]), int(out_size[1])
    grid_w = max(2, math.ceil(out_w / step))
    grid_h = max(2, math.ceil(out_h / step))
    uc = (np.arange(grid_w, dtype=np.float32) + 0.5) * (out_w / grid_w)
    vc = (np.arange(grid_h, dtype=np.float32) + 0.5) * (out_h / grid_h)
    x, y, w, h = (float(v) for v in rect)
    sx, sy = w / out_w, h / out_h
    q0x = np.broadcast_to((x + uc * sx)[None, :], (grid_h, grid_w)).astype(np.float32)
    q0y = np.broadcast_to((y + vc * sy)[:, None], (grid_h, grid_w)).astype(np.float32)
    return CoarseGrid(uc=uc, vc=vc, q0x=q0x, q0y=q0y, scale=sx)


def solve_displacement(
    depth_low: np.ndarray,
    low_scale: tuple[float, float],
    mean_depth: float,
    rect: Sequence[float],
    camera: CameraTerms,
    out_size: tuple[int, int],
    *,
    gain: float,
    step: int,
    iterations: int = 2,
) -> tuple[np.ndarray, np.ndarray]:
    """The backward displacement (working pixels) on a coarse grid of output positions.

    For an output point ``p`` the camera alone shows ``q0 = window origin + p * w / W``; the
    picture point that moves onto ``p`` solves ``q = q0 - k * (depth(q) - mean) * v(p)`` with
    ``k = gain * w / W`` and ``v = n * zoom - (px, py)``, by ``iterations`` fixed-point
    steps from ``q0``."""
    out_w = int(out_size[0])
    grid = coarse_grid(rect, out_size, step)
    half = out_w / 2.0
    nx = ((grid.uc - out_w / 2.0) / half)[None, :]
    ny = ((grid.vc - int(out_size[1]) / 2.0) / half)[:, None]
    shape = grid.q0x.shape
    vx = np.broadcast_to(nx * camera.zoom - camera.px, shape).astype(np.float32)
    vy = np.broadcast_to(ny * camera.zoom - camera.py, shape).astype(np.float32)
    k = float(gain) * grid.scale
    lsx, lsy = low_scale
    qx, qy = grid.q0x.copy(), grid.q0y.copy()
    for _ in range(max(int(iterations), 1)):
        sample = cv2.remap(
            depth_low, qx * lsx - 0.5, qy * lsy - 0.5, cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        shift = k * (sample - mean_depth)
        qx = grid.q0x - shift * vx
        qy = grid.q0y - shift * vy
    return (qx - grid.q0x).astype(np.float32), (qy - grid.q0y).astype(np.float32)


Box = tuple[int, int, int, int]
"""``(x0, y0, x1, y1)`` in output pixels, end exclusive."""


def full_maps(
    dx: np.ndarray,
    dy: np.ndarray,
    rect: Sequence[float],
    out_size: tuple[int, int],
    box: Box | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """``cv2.remap`` maps: the exact camera mapping plus the upsampled displacement (for the
    whole frame, or only for ``box``)."""
    out_w, out_h = int(out_size[0]), int(out_size[1])
    x0, y0, x1, y1 = box if box is not None else (0, 0, out_w, out_h)
    x, y, w, h = (float(v) for v in rect)
    sx, sy = w / out_w, h / out_h
    base_x = (x + (np.arange(x0, x1, dtype=np.float32) + 0.5) * sx - 0.5)[None, :]
    base_y = (y + (np.arange(y0, y1, dtype=np.float32) + 0.5) * sy - 0.5)[:, None]
    up_x = cv2.resize(dx, (out_w, out_h), interpolation=cv2.INTER_LINEAR)[y0:y1, x0:x1]
    up_y = cv2.resize(dy, (out_w, out_h), interpolation=cv2.INTER_LINEAR)[y0:y1, x0:x1]
    return (up_x + base_x).astype(np.float32), (up_y + base_y).astype(np.float32)


def front_box(
    layers: ParallaxLayers,
    dx: np.ndarray,
    dy: np.ndarray,
    rect: Sequence[float],
    out_size: tuple[int, int],
    step: int,
) -> Box | None:
    """The part of the frame where the front layer can show this frame (``None`` = nowhere):
    its coverage looked up at the displaced grid points, grown by one grid cell."""
    if layers.coverage is None:
        return (0, 0, int(out_size[0]), int(out_size[1]))
    grid = coarse_grid(rect, out_size, step)
    lsx, lsy = layers.low_scale
    seen = cv2.remap(
        layers.coverage, (grid.q0x + dx) * lsx - 0.5, (grid.q0y + dy) * lsy - 0.5,
        cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0.0,
    ) > 0.0
    if not seen.any():
        return None
    rows = np.flatnonzero(seen.any(axis=1))
    cols = np.flatnonzero(seen.any(axis=0))
    out_w, out_h = int(out_size[0]), int(out_size[1])
    cell_w, cell_h = out_w / seen.shape[1], out_h / seen.shape[0]
    x0 = max(int(math.floor((cols[0] - 0.5) * cell_w)), 0)
    x1 = min(int(math.ceil((cols[-1] + 1.5) * cell_w)) + 1, out_w)
    y0 = max(int(math.floor((rows[0] - 0.5) * cell_h)), 0)
    y1 = min(int(math.ceil((rows[-1] + 1.5) * cell_h)) + 1, out_h)
    return (x0, y0, x1, y1)


def parallax_frame(
    layers: ParallaxLayers,
    rect: Sequence[float],
    camera: CameraTerms,
    out_size: tuple[int, int],
    *,
    strength: float,
    interpolation: str | None = "cubic",
    grid_step: int = 8,
    iterations: int = 2,
) -> np.ndarray:
    """One parallax frame: ``rect`` is the camera window in working pixels, ``camera`` the
    terms of :meth:`..motion.MotionPath.camera`, ``grid_step`` already scaled to the size."""
    out_w, out_h = int(out_size[0]), int(out_size[1])
    gain = parallax_gain(strength, out_w)
    flag = interpolation_flag(interpolation)
    common = {
        "low_scale": layers.low_scale,
        "mean_depth": layers.mean_depth,
        "rect": rect,
        "camera": camera,
        "out_size": (out_w, out_h),
        "gain": gain,
        "step": grid_step,
        "iterations": iterations,
    }
    dx, dy = solve_displacement(layers.depth_back, **common)
    map_x, map_y = full_maps(dx, dy, rect, (out_w, out_h))
    back = cv2.remap(layers.plate, map_x, map_y, flag, borderMode=BORDER)
    if layers.alpha is None or layers.depth_front is None:
        return back
    dx, dy = solve_displacement(layers.depth_front, **common)
    box = front_box(layers, dx, dy, rect, (out_w, out_h), grid_step)
    if box is None:
        return back
    x0, y0, x1, y1 = box
    map_x, map_y = full_maps(dx, dy, rect, (out_w, out_h), box)
    front = cv2.remap(layers.image, map_x, map_y, flag, borderMode=BORDER)
    alpha = cv2.remap(layers.alpha, map_x, map_y, cv2.INTER_LINEAR,
                      borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
    under = np.ascontiguousarray(back[y0:y1, x0:x1])
    back[y0:y1, x0:x1] = cv2.blendLinear(front, under, alpha, 1.0 - alpha)
    return back


def overscan_for(
    path: MotionPath,
    layers: ParallaxLayers,
    out_size: tuple[int, int],
    *,
    strength: float,
    frames: int,
    fps: float,
    limit: float,
) -> float:
    """How much to tighten every window (share of its width, per side) so the parallax never
    pulls pixels from outside the picture; at most ``limit``."""
    if limit <= 0 or strength <= 0:
        return 0.0
    out_w, out_h = out_size
    corner = math.hypot(1.0, out_h / max(out_w, 1))
    low, high = layers.depth_range
    spread = max(high - layers.mean_depth, layers.mean_depth - low, 0.0)
    share = parallax_gain(strength, out_w) * spread / max(out_w, 1)
    bx, by, bw, bh = path.bounds if path.bounds is not None else (0.0, 0.0, math.inf, math.inf)
    needed = 0.0
    count = max(int(frames), 2)
    for k in range(0, count, max(1, count // 24)):
        cam = path.frame_camera(k, count, fps)
        reach = share * (corner * abs(cam.zoom) + math.hypot(cam.px, cam.py))
        if reach <= 0:
            continue
        x, y, w, h = path.frame_rect(k, count, fps)
        gap = min(x - bx, y - by, bx + bw - (x + w), by + bh - (y + h)) / max(w, 1e-9)
        needed = max(needed, reach - max(gap, 0.0))
    return float(min(max(needed, 0.0), limit))


def tighten(rect: Sequence[float], share: float) -> tuple[float, float, float, float]:
    """``rect`` shrunk by ``share`` of its width and height on every side (same centre)."""
    x, y, w, h = (float(v) for v in rect)
    if share <= 0:
        return (x, y, w, h)
    return (x + w * share, y + h * share, w * (1.0 - 2.0 * share), h * (1.0 - 2.0 * share))
