"""Transitions v2: the easing curves, the registry of transition ids, the catalogue
(config/transitions.yaml) and the mapping of old xfade names. No FFmpeg needed."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from cashcow_studio.llm.config import transition_catalog
from cashcow_studio.models.timeline import (
    DEFAULT_TRANSITION,
    LEGACY_TRANSITIONS,
    TRANSITION_IDS,
    Timeline,
    TimelineTransition,
    normalize_transition_id,
)
from cashcow_studio.render.transitions import easing, registry

CURVES = sorted(easing.EASINGS)
GRID = [i / 200 for i in range(201)]


# Easing ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", CURVES)
def test_every_curve_runs_from_0_to_1_and_never_goes_back(name: str) -> None:
    curve = easing.get_easing(name)
    values = [curve(x) for x in GRID]
    assert values[0] == 0.0 and values[-1] == 1.0
    assert all(0.0 <= v <= 1.0 for v in values)
    assert all(b >= a - 1e-12 for a, b in itertools.pairwise(values)), name
    assert curve(-0.5) == 0.0 and curve(1.5) == 1.0  # clamped outside [0, 1]


def test_curve_values_match_the_parity_document() -> None:
    assert easing.ease_in_out_cubic(0.25) == pytest.approx(4 * 0.25**3)
    assert easing.ease_in_out_cubic(0.5) == pytest.approx(0.5)
    assert easing.ease_in_out_cubic(0.75) == pytest.approx(1 - 0.5**3 / 2)
    for x in GRID:  # symmetric S-curves
        assert easing.ease_in_out_cubic(x) + easing.ease_in_out_cubic(1 - x) == pytest.approx(1)
        assert easing.snap(x) + easing.snap(1 - x) == pytest.approx(1)
    assert easing.ease_out_quart(0.5) == pytest.approx(0.9375)
    assert easing.snap(0.25) == pytest.approx(16 * 0.25**5)
    assert easing.smoothstep(0.3, 0.7, 0.5) == pytest.approx(0.5)
    assert easing.smoothstep(0.3, 0.7, 0.2) == 0.0 and easing.smoothstep(0.3, 0.7, 0.9) == 1.0
    # Speed (d eased / d linear): the S-curve is still at the ends and 1.5 at the middle.
    assert easing.speed("ease_in_out_cubic", 0.5) == pytest.approx(3.0, rel=1e-2)
    assert easing.speed("ease_in_out_cubic", 0.0) == pytest.approx(0.0, abs=1e-2)
    assert easing.speed("linear", 0.3) == pytest.approx(1.0)
    assert easing.speed("ease_out_quart", 0.0) == pytest.approx(4.0, rel=1e-2)
    assert easing.get_easing("nonsense") is easing.ease_in_out_cubic
    assert easing.known_easing("nonsense", "linear") == "linear"
    assert set(easing.CATALOGUE_EASINGS) <= set(easing.EASINGS)


def test_camera_moves_and_transitions_share_the_same_curves() -> None:
    """render/motion.py writes the same four formulas (parity for the M5 preview)."""
    motion = pytest.importorskip("cashcow_studio.render.motion")
    for name in easing.CATALOGUE_EASINGS:
        for x in GRID:
            assert motion.ease(name, x) == pytest.approx(easing.ease(name, x), abs=1e-12), name


# Registry and catalogue --------------------------------------------------------------------


def test_the_engine_draws_every_id_the_timeline_accepts() -> None:
    assert registry.check_ids() == []
    assert set(registry.ids()) == set(TRANSITION_IDS)
    assert len(TRANSITION_IDS) == 27
    described = {row["id"]: row for row in registry.describe()}
    assert set(described) == set(TRANSITION_IDS)
    assert described["zoom_through"]["packs"] == ["cinematic", "dynamic"]
    assert described["push_left"]["easing"] == "ease_out_quart"
    assert all(row["label"] and row["duration_s"] > 0 for row in described.values())


def test_every_catalogue_transition_has_an_engine_function() -> None:
    catalog = transition_catalog()
    assert set(catalog.types) <= set(registry.ids())
    for old, new in catalog.legacy.items():
        assert new in registry.ids(), old
    assert catalog.default in registry.ids() and catalog.last_scene in registry.ids()


def test_resolve_uses_the_catalogue_easing_and_params() -> None:
    catalog = transition_catalog()
    for transition_id in registry.ids():
        entry = catalog.get(transition_id)
        resolved = registry.resolve(transition_id)
        spec = registry.SPECS[transition_id]
        if entry is not None and entry.type == transition_id:
            assert resolved.easing == entry.easing, transition_id
            for key, value in entry.params.items():
                assert resolved.params[key] == value, (transition_id, key)
        else:
            assert resolved.easing == spec.easing
    # Overrides win over both; the engine defaults fill the gaps.
    custom = registry.resolve("zoom_through", params={"a_scale_end": 1.5}, easing="linear")
    assert custom.easing == "linear" and custom.params["a_scale_end"] == 1.5
    assert "crossfade_window" in custom.params
    bare = registry.resolve("zoom_through", use_catalogue=False)
    assert bare.params == dict(registry.SPECS["zoom_through"].params)
    # A cut clip's hash follows the settings.
    assert custom.cache_token() != registry.resolve("zoom_through").cache_token()


def test_frame_times_sit_in_the_middle_of_each_slot() -> None:
    assert [registry.frame_time(k, 4) for k in range(4)] == [0.125, 0.375, 0.625, 0.875]
    assert registry.frame_time(0, 1) == 0.5


def test_render_frames_from_two_stills() -> None:
    black = np.zeros((36, 64, 3), np.uint8)
    white = np.full((36, 64, 3), 255, np.uint8)
    frames = list(registry.render_frames("film_dissolve", itertools.repeat(black),
                                         itertools.repeat(white), 6, fps=30))
    assert len(frames) == 6 and all(f.shape == (36, 64, 3) for f in frames)
    means = [float(f.mean()) for f in frames]
    assert means == sorted(means) and 0 < means[0] < means[-1] <= 255
    with pytest.raises(ValueError, match="needs 6 frames"):
        list(registry.render_frames("film_dissolve", [black], [white], 6))
    with pytest.raises(ValueError, match="differ in size"):
        registry.render_frame("film_dissolve", black, np.zeros((10, 10, 3), np.uint8), 0.5)


def test_fade_out_colour_of_the_last_scene() -> None:
    assert registry.fade_out_colour("dip_white") == (1.0, 1.0, 1.0)
    assert registry.fade_out_colour("dip_black") == (0.0, 0.0, 0.0)
    assert registry.fade_out_colour("fadewhite") == (1.0, 1.0, 1.0)  # a legacy name
    assert registry.fade_out_colour("zoom_through") == (0.0, 0.0, 0.0)
    frame = np.full((4, 4, 3), 200, np.uint8)
    fades = [registry.fade_out_frame(frame, (0.0, 0.0, 0.0), k, 4) for k in range(4)]
    assert [int(f[0, 0, 0]) for f in fades] == [150, 100, 50, 0]  # the last frame is black


# Old xfade names ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("fade", "film_dissolve"),
        ("wipeleft", "luma_wipe_left"),
        ("slideleft", "push_left"),
        ("zoomin", "zoom_through"),
        ("fadeblack", "dip_black"),
        ("fadewhite", "dip_white"),
        ("pixelize", "film_dissolve"),
        ("no-such-transition", "film_dissolve"),
        ("", "film_dissolve"),
        ("Film-Dissolve", "film_dissolve"),
        ("whip_left", "whip_left"),
        ("cube_right", "cube_right"),
    ],
)
def test_old_xfade_names_map_to_their_v2_look(old: str, new: str) -> None:
    assert normalize_transition_id(old) == new
    assert TimelineTransition(type=old).type == new
    assert registry.resolve_id(old) == new


def test_the_catalogue_extends_the_contract_table() -> None:
    assert LEGACY_TRANSITIONS["fade"] == "film_dissolve"
    assert TimelineTransition().type == DEFAULT_TRANSITION == "film_dissolve"
    catalog = transition_catalog()
    if "wiperight" in catalog.legacy:  # config/transitions.yaml maps the other wipes too
        assert normalize_transition_id("wiperight") == catalog.legacy["wiperight"]


def test_an_old_timeline_json_reads_with_v2_transitions() -> None:
    scenes = []
    for index, old in enumerate(["fade", "wipeleft", "slideleft", "fadeblack"]):
        scenes.append({
            "index": index, "image": f"06_images/scene_{index + 1:02d}.png",
            "start_s": index * 2.0, "end_s": index * 2.0 + 2.0,
            "motion": {"preset": "zoom_in", "start_rect": [0, 0, 1024, 576],
                       "end_rect": [102.4, 57.6, 819.2, 460.8]},
            "transition_out": {"type": old, "duration_s": 0.5},
        })
    timeline = Timeline.model_validate({
        "project_id": "p", "format": "long", "aspect": "16:9", "width": 1280, "height": 720,
        "duration_s": 8.0, "scenes": scenes,
    })
    assert timeline.transition_types == ["film_dissolve", "luma_wipe_left", "push_left",
                                         "dip_black"]
    again = Timeline.model_validate_json(timeline.model_dump_json())
    assert again.transition_types == timeline.transition_types
