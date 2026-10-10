"""The transition engine's table: every v2 id, the function that draws it, and its defaults.

The defaults here mirror ``config/transitions.yaml`` (the catalogue, read through
``llm.config.transition_catalog()``), which wins for ``easing`` and ``params`` when it has the
entry; the timeline gives the duration. Old xfade names resolve like the timeline does
(``models.timeline.normalize_transition_id``).

Frame ``k`` of an ``n``-frame transition is drawn at linear progress ``(k + 0.5) / n`` (the
middle of its time slot), eased by the transition's curve; the first and last frames are
therefore already a little into the change, and the frames just outside the transition are
the plain pictures. See ``docs/design/TRANSITION-PARITY.md``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ...models.timeline import TRANSITION_IDS, normalize_transition_id
from . import blur, cube, dips, dissolve, glitch, leaks, luma_wipe, push_whip, spin, warps, zoom
from .common import FrameContext, as_frame, mix, solid
from .easing import ease, known_easing

log = logging.getLogger(__name__)

Render = Callable[[np.ndarray, np.ndarray, FrameContext], np.ndarray]


@dataclass(frozen=True)
class TransitionSpec:
    id: str
    label: str
    family: str
    render: Render
    easing: str
    duration_s: float
    energy: str
    packs: tuple[str, ...]
    params: Mapping[str, Any] = field(default_factory=dict)
    seeded: bool = False
    """Whether the frames depend on the cut's seed (glitch slices, leak side, burn edge)."""


ALL_PACKS = ("cinematic", "clean", "dynamic")


def _spec(id_: str, label: str, family: str, render: Render, easing: str, duration: float,
          energy: str, packs: tuple[str, ...], *, seeded: bool = False,
          **params: Any) -> TransitionSpec:
    return TransitionSpec(id_, label, family, render, easing, duration, energy, packs, params,
                          seeded)


_DIRS = ("left", "right", "up", "down")
_SPECS: tuple[TransitionSpec, ...] = (
    _spec("film_dissolve", "Film dissolve", "dissolve", dissolve.film_dissolve,
          "ease_in_out_cubic", 0.7, "calm", ALL_PACKS, luma_lift=0.03),
    _spec("dip_black", "Dip to black", "dips", dips.dip_black, "ease_in_out_cubic", 0.8,
          "calm", ALL_PACKS, color=[0, 0, 0], hold=0.08),
    _spec("dip_white", "Dip to white", "dips", dips.dip_white, "ease_in_out_cubic", 0.8,
          "calm", ALL_PACKS, color=[255, 255, 255], hold=0.08),
    _spec("blur_dissolve", "Blur dissolve", "blur", blur.blur_dissolve, "ease_in_out_cubic",
          0.7, "calm", ("cinematic", "clean"), max_blur_px=24),
    _spec("zoom_through", "Zoom through", "zoom", zoom.zoom_through, "ease_in_out_cubic", 0.7,
          "medium", ("cinematic", "dynamic"), a_scale_end=1.25, b_scale_start=1.15,
          crossfade_window=0.4, radial_blur=True),
    *(
        _spec(f"push_{d}", f"Push {d}", "push_whip", getattr(push_whip, f"push_{d}"),
              "ease_out_quart", 0.5, "medium", ("clean", "dynamic"), direction=d,
              overshoot=0.02, motion_blur=push_whip.PUSH_MOTION_BLUR)
        for d in _DIRS
    ),
    *(
        _spec(f"whip_{d}", f"Whip {d}", "push_whip", getattr(push_whip, f"whip_{d}"),
              "ease_in_out_cubic", 0.35, "high", ("dynamic",), direction=d, motion_blur=1.0)
        for d in _DIRS
    ),
    *(
        _spec(f"luma_wipe_{d}", "Diagonal wipe" if d == "diag" else f"Wipe {d}", "luma_wipe",
              getattr(luma_wipe, f"luma_wipe_{d}"), "ease_in_out_cubic", 0.6, "medium",
              ("cinematic", "clean"), direction=d, feather=0.12)
        for d in (*_DIRS, "diag")
    ),
    _spec("crosswarp", "Cross warp", "warps", warps.crosswarp, "ease_in_out_cubic", 0.7,
          "medium", ("cinematic", "dynamic")),
    _spec("directional_warp", "Directional warp", "warps", warps.directional_warp,
          "ease_in_out_cubic", 0.45, "medium", ("dynamic",), direction=[-1.0, 1.0],
          smoothness=0.5),
    _spec("light_leak_warm", "Warm light leak", "leaks", leaks.light_leak_warm, "linear", 0.8,
          "medium", ("cinematic",), seeded=True, peak_t=0.5, color=[255, 176, 96],
          strength=0.85),
    _spec("film_burn", "Film burn", "leaks", leaks.film_burn, "linear", 0.8, "high",
          ("cinematic",), seeded=True, color=[255, 240, 220], edge_color=[255, 140, 40]),
    _spec("glitch_subtle", "Subtle glitch", "glitch", glitch.glitch_subtle, "snap", 0.3,
          "high", ("dynamic",), seeded=True, glitch_frames=[6, 8], max_shift_px=6,
          slices=[3, 5]),
    _spec("spin_zoom", "Spin zoom", "spin", spin.spin_zoom, "ease_in_out_cubic", 0.45, "high",
          ("dynamic",), max_angle_deg=12, a_scale_end=1.4, b_angle_start_deg=-8),
    _spec("morph_blend", "Morph blend", "warps", warps.morph_blend, "ease_in_out_cubic", 0.8,
          "calm", ("cinematic",), max_displacement_px=18),
    _spec("cube_left", "Cube turn left", "cube", cube.cube_left, "ease_in_out_cubic", 0.45,
          "medium", ("dynamic",), direction="left", perspective=0.7),
    _spec("cube_right", "Cube turn right", "cube", cube.cube_right, "ease_in_out_cubic", 0.45,
          "medium", ("dynamic",), direction="right", perspective=0.7),
)
SPECS: dict[str, TransitionSpec] = {spec.id: spec for spec in _SPECS}

ENGINE_VERSION = "tv2.2"
"""Part of every cached clip's hash: change it when a formula changes (tv2.2: smooth radial
and spin blur, push motion blur)."""

FADE_OUT_COLOURS: dict[str, tuple[float, float, float]] = {"dip_white": (1.0, 1.0, 1.0)}


def ids() -> list[str]:
    return list(SPECS)


def resolve_id(name: str | None) -> str:
    """The engine id for ``name`` (legacy xfade names mapped, unknown -> film_dissolve)."""
    resolved = normalize_transition_id(name)
    return resolved if resolved in SPECS else "film_dissolve"


def get_spec(name: str | None) -> TransitionSpec:
    return SPECS[resolve_id(name)]


def _catalogue_entry(transition_id: str) -> tuple[str | None, Mapping[str, Any]]:
    """``(easing, params)`` of the catalogue's entry, or ``(None, {})``."""
    try:
        from ...llm.config import transition_catalog

        entry = transition_catalog().get(transition_id)
    except Exception as exc:  # noqa: BLE001 - a broken catalogue must not stop a render
        log.warning("The transition catalogue could not be read (%s); using the defaults.", exc)
        return None, {}
    if entry is None or getattr(entry, "type", None) != transition_id:
        return None, {}
    return getattr(entry, "easing", None), dict(getattr(entry, "params", {}) or {})


@dataclass(frozen=True)
class ResolvedTransition:
    """One transition with its easing and parameters settled (what a cut clip is made of)."""

    id: str
    easing: str
    params: Mapping[str, Any]
    spec: TransitionSpec

    def cache_token(self) -> str:
        return json.dumps(
            {"id": self.id, "easing": self.easing, "params": dict(self.params),
             "engine": ENGINE_VERSION},
            sort_keys=True, default=str,
        )

    def seed_key(self, seed: int) -> int | None:
        """The seed as far as the frames depend on it: ``None`` for the transitions that never
        read it, so a cached cut survives scenes being added or removed before it."""
        return int(seed) if self.spec.seeded else None


def resolve(name: str | None, *, params: Mapping[str, Any] | None = None,
            easing: str | None = None, use_catalogue: bool = True) -> ResolvedTransition:
    spec = get_spec(name)
    cat_easing, cat_params = _catalogue_entry(spec.id) if use_catalogue else (None, {})
    merged: dict[str, Any] = {**dict(spec.params), **dict(cat_params), **dict(params or {})}
    chosen = known_easing(easing or cat_easing or spec.easing, spec.easing)
    return ResolvedTransition(spec.id, chosen, merged, spec)


def frame_time(k: int, n: int) -> float:
    """Linear progress of frame ``k`` of ``n``: the middle of its slot, ``(k + 0.5) / n``."""
    return (k + 0.5) / max(n, 1)


def render_frame(
    name: str | ResolvedTransition,
    a: np.ndarray,
    b: np.ndarray,
    t_raw: float,
    *,
    duration_s: float | None = None,
    fps: float = 30.0,
    params: Mapping[str, Any] | None = None,
    easing: str | None = None,
    seed: int = 0,
    cache: dict[str, Any] | None = None,
) -> np.ndarray:
    """One frame of a transition at linear progress ``t_raw`` (0 = all A, 1 = all B)."""
    resolved = name if isinstance(name, ResolvedTransition) else resolve(
        name, params=params, easing=easing
    )
    first = as_frame(a)
    second = as_frame(b)
    if first.shape != second.shape:
        raise ValueError(f"the two frames differ in size: {first.shape} and {second.shape}")
    height, width = first.shape[:2]
    progress = min(max(float(t_raw), 0.0), 1.0)
    ctx = FrameContext(
        width=width,
        height=height,
        t=ease(resolved.easing, progress),
        t_raw=progress,
        duration_s=float(duration_s if duration_s else resolved.spec.duration_s),
        fps=float(fps),
        easing=resolved.easing,
        params=resolved.params,
        seed=int(seed),
        cache=cache if cache is not None else {},
    )
    out = resolved.spec.render(first, second, ctx)
    if out is first or out is second or out is a or out is b:
        out = out.copy()
    if out.shape != first.shape or out.dtype != np.uint8:
        raise ValueError(f"transition {resolved.id} drew a frame of the wrong shape")
    return np.ascontiguousarray(out)


def render_frames(
    name: str | ResolvedTransition,
    frames_a: Iterable[np.ndarray],
    frames_b: Iterable[np.ndarray],
    count: int,
    *,
    duration_s: float | None = None,
    fps: float = 30.0,
    params: Mapping[str, Any] | None = None,
    easing: str | None = None,
    seed: int = 0,
    stop: Callable[[], bool] | None = None,
) -> Iterator[np.ndarray]:
    """The ``count`` frames of a transition from ``count`` frames of each picture (stills are
    fine: pass ``itertools.repeat(image)``). Static maps are computed once per call."""
    resolved = name if isinstance(name, ResolvedTransition) else resolve(
        name, params=params, easing=easing
    )
    seconds = duration_s if duration_s else count / max(fps, 1e-6)
    cache: dict[str, Any] = {}
    iter_a = iter(frames_a)
    iter_b = iter(frames_b)
    for k in range(count):
        if stop is not None and stop():
            return
        try:
            a = next(iter_a)
            b = next(iter_b)
        except StopIteration:
            raise ValueError(f"the transition needs {count} frames of each picture") from None
        yield render_frame(resolved, a, b, frame_time(k, count), duration_s=seconds, fps=fps,
                           seed=seed, cache=cache)


def fade_out_colour(name: str | None, params: Mapping[str, Any] | None = None
                    ) -> tuple[float, float, float]:
    """The colour the last scene fades to: white for ``dip_white``, else black (or the
    ``color`` param of the dip)."""
    resolved = resolve(name, params=params)
    default = FADE_OUT_COLOURS.get(resolved.id, (0.0, 0.0, 0.0))
    if resolved.id not in ("dip_black", "dip_white"):
        return default
    ctx = FrameContext(1, 1, 0.0, params=resolved.params)
    return ctx.colour(("color", "colour"), default)


def fade_out_frame(a: np.ndarray, colour: tuple[float, float, float], k: int, n: int,
                   cache: dict[str, Any] | None = None) -> np.ndarray:
    """Frame ``k`` of the ``n``-frame fade at the very end of the video: a straight fade
    with weight ``(k + 1) / n``, so the last frame is the plain colour."""
    frame = as_frame(a)
    key = f"fade:{frame.shape}:{colour}"
    plate = cache.get(key) if cache is not None else None
    if plate is None:
        plate = solid(frame.shape[0], frame.shape[1], colour)
        if cache is not None:
            cache[key] = plate
    return np.ascontiguousarray(mix(frame, plate, (k + 1) / max(n, 1)))


def describe() -> list[dict[str, Any]]:
    """Every transition with its engine defaults (for docs, tools and tests)."""
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "family": spec.family,
            "easing": spec.easing,
            "duration_s": spec.duration_s,
            "energy": spec.energy,
            "packs": list(spec.packs),
            "params": dict(spec.params),
            "seeded": spec.seeded,
        }
        for spec in _SPECS
    ]


def check_ids() -> list[str]:
    """Ids the timeline accepts that the engine cannot draw (should be empty)."""
    return [i for i in TRANSITION_IDS if i not in SPECS]
