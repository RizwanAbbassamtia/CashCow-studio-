"""The cinematic transition engine (docs/TRANSITIONS-V2-CONTRACT.md section 1).

Every transition is a pure function of two frames and the eased progress ``t``; the maths of
each one is written out in ``docs/design/TRANSITION-PARITY.md`` so the browser preview can
reproduce it. One module per family; ``registry`` maps ids to functions.

    from cashcow_studio.render.transitions import render_frame, render_frames
    frame = render_frame("zoom_through", a, b, 0.5)   # a, b: uint8 RGB (H, W, 3)
"""

from .registry import (
    ENGINE_VERSION,
    SPECS,
    ResolvedTransition,
    TransitionSpec,
    describe,
    fade_out_colour,
    fade_out_frame,
    frame_time,
    get_spec,
    ids,
    render_frame,
    render_frames,
    resolve,
    resolve_id,
)

__all__ = [
    "ENGINE_VERSION",
    "SPECS",
    "ResolvedTransition",
    "TransitionSpec",
    "describe",
    "fade_out_colour",
    "fade_out_frame",
    "frame_time",
    "get_spec",
    "ids",
    "render_frame",
    "render_frames",
    "resolve",
    "resolve_id",
]
