"""2.5D parallax and the Python frame path of Motion v2 (docs/TRANSITIONS-V2-CONTRACT.md
section 2).

* :mod:`.depth` - Depth Anything V2 Small through onnxruntime (optional; downloaded once to
  ``<app_data_dir>/models/``), depth maps cached next to each picture as
  ``scene_NN.depth.png``; :func:`depth_model_status` and :func:`start_depth_model_download`
  are what Settings > Render calls.
* :mod:`.warp` - the per-frame warps: eased Ken Burns (one sub-pixel affine warp) and the
  two-layer parallax displacement with the background filled in behind near objects.
* :mod:`.writer` - frames to an H.264 clip through an FFmpeg ``rawvideo`` pipe.
* :mod:`.clip` - :func:`render_motion_clip`: one scene's move as a clip, falling back to Ken
  Burns (and saying why) when there is no depth map.

The maths is shared with :mod:`..motion` and written out in ``docs/design/MOTION-PARITY.md``.
"""

from __future__ import annotations

from .clip import (
    NO_DEPTH,
    MotionClipResult,
    depth_fingerprint,
    iter_motion_frames,
    render_motion_clip,
)
from .depth import (
    DepthBatch,
    DepthModelError,
    DepthModelSpec,
    DepthModelStatus,
    cancel_depth_model_download,
    depth_map_path,
    depth_model_spec,
    depth_model_status,
    download_depth_model,
    ensure_depth_maps,
    runtime_check,
    start_depth_model_download,
)
from .writer import ClipInfo, ClipWriter, write_clip

__all__ = [
    "NO_DEPTH",
    "ClipInfo",
    "ClipWriter",
    "DepthBatch",
    "DepthModelError",
    "DepthModelSpec",
    "DepthModelStatus",
    "MotionClipResult",
    "cancel_depth_model_download",
    "depth_fingerprint",
    "depth_map_path",
    "depth_model_spec",
    "depth_model_status",
    "download_depth_model",
    "ensure_depth_maps",
    "iter_motion_frames",
    "render_motion_clip",
    "runtime_check",
    "start_depth_model_download",
    "write_clip",
]
