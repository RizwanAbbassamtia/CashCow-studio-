"""One scene's moving picture as an H.264 clip: the eased Ken Burns move or 2.5D parallax.

This is the Python path of docs/TRANSITIONS-V2-CONTRACT.md sections 2 and 3.
:func:`iter_motion_frames` yields the frames of one scene clip (the scene plus its transition
overlaps; the camera path spans every frame, ``t = k / (n - 1)``); the scene-clip code
(``render/scene_clips.py``, its built-in motion frame provider) encodes them.
:func:`render_motion_clip` does the whole job on its own (picture in, H.264 clip out) with the
timeline scene's rectangles (source-image pixels), preset, style and strength.

``style = "parallax"`` needs a depth map (``scene_NN.depth.png`` next to the picture, made by
:func:`.depth.ensure_depth_maps`). Without one the clip is drawn as the eased Ken Burns move
and :attr:`MotionClipResult.fallback_reason` says why in plain English, for the edit review.
The style actually drawn is in :attr:`MotionClipResult.style`; put :func:`depth_fingerprint`
in the clip's cache key so a clip drawn without depth is redrawn once the depth map exists.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ...models.timeline import DEFAULT_PARALLAX_STRENGTH, normalize_motion_style
from ..geometry import cover_region
from ..motion import (
    KEN_BURNS,
    PARALLAX,
    MotionConfig,
    MotionPath,
    MotionStyle,
    load_motion_config,
)
from . import warp
from .depth import depth_map_path, load_rgb, read_depth_map
from .writer import DEFAULT_CRF, DEFAULT_X264_PRESET, ClipWriter

NO_DEPTH = (
    "This scene has no depth map yet (the depth model is not downloaded or could not run), "
    "so it uses the Ken Burns move instead of 2.5D parallax."
)

FrameProgress = Callable[[int, int], None]


@dataclass(frozen=True)
class MotionClipResult:
    path: Path
    frames: int
    width: int
    height: int
    style: MotionStyle
    """What was drawn (``ken_burns`` when parallax fell back)."""
    requested_style: MotionStyle
    fallback_reason: str | None
    seconds: float
    layers: int = 1


def depth_fingerprint(image: Path | str) -> str:
    """A short fingerprint of the picture's depth map ('' when there is none), for clip
    cache keys."""
    path = depth_map_path(image)
    try:
        stat = path.stat()
    except OSError:
        return ""
    blob = f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def load_depth(depth: Path | str | np.ndarray | None, image: Path) -> np.ndarray | None:
    if isinstance(depth, np.ndarray):
        return depth.astype(np.float32)
    path = Path(depth) if depth is not None else depth_map_path(image)
    found = read_depth_map(path)
    return found[0] if found is not None else None


def iter_motion_frames(
    rgb: np.ndarray,
    *,
    size: tuple[int, int],
    fps: float,
    frames: int,
    path: MotionPath,
    style: MotionStyle = KEN_BURNS,
    strength: float = DEFAULT_PARALLAX_STRENGTH,
    depth: np.ndarray | None = None,
    interpolation: str | None = None,
    config: MotionConfig | None = None,
    info: dict[str, object] | None = None,
) -> Iterator[np.ndarray]:
    """Every frame of the move, ``uint8`` RGB of ``size``. ``path`` is in source-image pixels
    with ``bounds`` = the cover region. ``info`` (when given) receives ``layers`` and
    ``overscan``."""
    config = config or load_motion_config()
    out_w, out_h = int(size[0]), int(size[1])
    image_h, image_w = rgb.shape[:2]
    region = path.bounds or cover_region(image_w, image_h, out_w, out_h)
    work = warp.working_image(rgb, region, path, (out_w, out_h))
    moved = work.path(path)
    count = max(int(frames), 1)
    use_parallax = style == PARALLAX and depth is not None and strength > 0
    if not use_parallax:
        flag = interpolation or config.interpolation
        previous: tuple[tuple[float, ...], np.ndarray] | None = None
        for k in range(count):
            rect = moved.frame_rect(k, count, fps)
            key = tuple(round(v, 4) for v in rect)
            if previous is not None and previous[0] == key:
                yield previous[1]
                continue
            frame = warp.kenburns_frame(work.pixels, rect, (out_w, out_h), flag)
            previous = (key, frame)
            yield frame
        if info is not None:
            info.update({"layers": 0, "overscan": 0.0})
        return
    assert depth is not None
    depth_work = work.crop_like(depth, (image_w, image_h))
    layers = warp.prepare_layers(work.pixels, depth_work, config)
    overscan = warp.overscan_for(moved, layers, (out_w, out_h), strength=strength, frames=count,
                                 fps=fps, limit=config.max_overscan)
    if info is not None:
        info.update({"layers": 2 if layers.layered else 1, "overscan": round(overscan, 4),
                     **layers.stats})
    step = warp.grid_step_for((out_w, out_h), config.grid_step_px)
    flag = interpolation or config.parallax_interpolation
    previous_p: tuple[tuple[float, ...], np.ndarray] | None = None
    for k in range(count):
        rect = warp.tighten(moved.frame_rect(k, count, fps), overscan)
        camera = path.frame_camera(k, count, fps)
        key = (*(round(v, 4) for v in rect), round(camera.px, 6), round(camera.py, 6),
               round(camera.zoom, 6))
        if previous_p is not None and previous_p[0] == key:
            yield previous_p[1]
            continue
        frame = warp.parallax_frame(
            layers, rect, camera, (out_w, out_h), strength=strength, interpolation=flag,
            grid_step=step, iterations=config.iterations,
        )
        previous_p = (key, frame)
        yield frame


def render_motion_clip(
    image: Path | str,
    output: Path | str,
    *,
    size: tuple[int, int],
    fps: float,
    frames: int,
    start_rect: Sequence[float],
    end_rect: Sequence[float],
    preset: str = "hold",
    style: str | None = KEN_BURNS,
    strength: float = DEFAULT_PARALLAX_STRENGTH,
    seed: int = 0,
    depth: Path | str | np.ndarray | None = None,
    interpolation: str | None = None,
    crf: int = DEFAULT_CRF,
    x264_preset: str = DEFAULT_X264_PRESET,
    ffmpeg: str | None = None,
    cancel: threading.Event | None = None,
    progress: FrameProgress | None = None,
    config: MotionConfig | None = None,
) -> MotionClipResult:
    """Draw a scene's move to ``output`` (``frames`` frames of ``size`` at ``fps``).

    ``start_rect``/``end_rect`` are the timeline's source-image pixel rectangles; ``seed``
    makes the handheld wobble repeatable (use the scene index). ``depth`` is a depth map
    (path or 0..1 array at the picture's size); ``None`` reads ``scene_NN.depth.png`` next to
    the picture. Raises ``RenderError`` / ``RenderCancelled`` from ``render/ffmpeg.py``."""
    started = time.monotonic()
    config = config or load_motion_config()
    image = Path(image)
    requested = normalize_motion_style(style)
    rgb = load_rgb(image)
    out_w, out_h = int(size[0]), int(size[1])
    region = cover_region(rgb.shape[1], rgb.shape[0], out_w, out_h)
    path = MotionPath.for_preset(preset, start_rect, end_rect, seed=seed, bounds=region,
                                 handheld_amplitude=config.handheld_amplitude)
    depth_map: np.ndarray | None = None
    reason: str | None = None
    if requested == PARALLAX:
        depth_map = load_depth(depth, image)
        if depth_map is None:
            reason = NO_DEPTH
    drawn: MotionStyle = PARALLAX if depth_map is not None else KEN_BURNS
    info: dict[str, object] = {}
    count = max(int(frames), 1)
    writer = ClipWriter(output, out_w, out_h, fps, crf=crf, x264_preset=x264_preset,
                        ffmpeg=ffmpeg, cancel=cancel)
    with writer:
        for number, frame in enumerate(
            iter_motion_frames(
                rgb, size=(out_w, out_h), fps=fps, frames=count, path=path, style=drawn,
                strength=strength, depth=depth_map, interpolation=interpolation,
                config=config, info=info,
            ),
            start=1,
        ):
            writer.write(frame)
            if progress is not None:
                progress(number, count)
    assert writer.info is not None
    layers = info.get("layers")
    return MotionClipResult(
        path=writer.info.path,
        frames=writer.info.frames,
        width=out_w,
        height=out_h,
        style=drawn,
        requested_style=requested,
        fallback_reason=reason,
        seconds=round(time.monotonic() - started, 3),
        layers=int(layers) if isinstance(layers, int) else 1,
    )
