"""Transitions v2 laptop budget (docs/TRANSITIONS-V2-CONTRACT.md rule 2): a 1080p transition
of 0.6 s (18 frames) computes in under 3 s on the CPU.

The time is measured on every run (best of three, printed with ``-s``); the assertion is
skipped when ``CCS_SLOW_CI`` is set, for build machines much slower than an editor's laptop.
"""

from __future__ import annotations

import itertools
import os
import time

import numpy as np
import pytest

from cashcow_studio.render.transitions import registry

BUDGET_S = 3.0
FRAMES = 18
SLOW_CI = os.environ.get("CCS_SLOW_CI", "").strip().lower() not in ("", "0", "false", "no")


def photo_like(seed: int, width: int = 1920, height: int = 1080) -> np.ndarray:
    """Smooth gradients plus fine grain: what a picture costs to process."""
    rng = np.random.default_rng(seed)
    u = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]
    v = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    img = np.empty((height, width, 3), np.float32)
    img[..., 0] = 0.2 + 0.6 * u * v
    img[..., 1] = 0.25 + 0.5 * (1.0 - u) * v
    img[..., 2] = 0.4 + 0.3 * np.sin(9.0 * u + 5.0 * v + seed)
    grain = rng.normal(0.0, 0.03, (height, width, 1)).astype(np.float32)
    return (np.clip(img + grain, 0.0, 1.0) * 255.0).astype(np.uint8)


def best_time(transition_id: str, a: np.ndarray, b: np.ndarray, runs: int = 3) -> float:
    best = float("inf")
    for _ in range(runs):
        started = time.perf_counter()
        frames = list(registry.render_frames(transition_id, itertools.repeat(a),
                                             itertools.repeat(b), FRAMES, duration_s=0.6))
        best = min(best, time.perf_counter() - started)
        assert len(frames) == FRAMES and frames[0].shape == (1080, 1920, 3)
    return best


def test_zoom_through_at_1080p_is_within_the_laptop_budget() -> None:
    a, b = photo_like(1), photo_like(2)
    seconds = best_time("zoom_through", a, b)
    print(f"\nzoom_through 1080p, {FRAMES} frames: {seconds:.2f} s (budget {BUDGET_S} s)")
    if SLOW_CI:
        pytest.skip(f"CCS_SLOW_CI is set; measured {seconds:.2f} s")
    assert seconds < BUDGET_S, f"zoom_through took {seconds:.2f} s for {FRAMES} 1080p frames"


@pytest.mark.parametrize(
    "transition_id", ["light_leak_warm", "morph_blend", "directional_warp", "spin_zoom"]
)
def test_the_heaviest_transitions_fit_the_budget_too(transition_id: str) -> None:
    a, b = photo_like(3), photo_like(4)
    seconds = best_time(transition_id, a, b, runs=2)
    print(f"\n{transition_id} 1080p, {FRAMES} frames: {seconds:.2f} s")
    if SLOW_CI:
        pytest.skip(f"CCS_SLOW_CI is set; measured {seconds:.2f} s")
    assert seconds < BUDGET_S, f"{transition_id} took {seconds:.2f} s"
