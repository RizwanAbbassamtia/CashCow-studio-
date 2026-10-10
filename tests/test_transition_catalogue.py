"""The transition catalogue (docs/TRANSITIONS-V2-CONTRACT.md section 1): config/transitions.yaml
is valid and matches the contract's library, the runtime loader is forgiving, old xfade names
map to v2 ids, pack lengths, the engine knows every id, and every transition has its committed
preview (frontend/public/transitions/) after generation."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
import yaml
from PIL import Image

from cashcow_studio.llm.config import config_dir, transition_catalog
from cashcow_studio.models.transitions import (
    TRANSITION_PACKS,
    TransitionCatalog,
    TransitionCatalogueFile,
    poster_url,
    preview_url,
)

REPO = Path(__file__).resolve().parents[1]
PREVIEWS = REPO / "frontend" / "public" / "transitions"
TOOL = REPO / "tools" / "make_transition_previews.py"

# The library of the contract's table: id -> (packs, energy).
ALL = ("cinematic", "clean", "dynamic")
CONTRACT: dict[str, tuple[tuple[str, ...], str]] = {
    "film_dissolve": (ALL, "calm"),
    "dip_black": (ALL, "calm"),
    "dip_white": (ALL, "calm"),
    "blur_dissolve": (("cinematic", "clean"), "calm"),
    "zoom_through": (("cinematic", "dynamic"), "medium"),
    **{f"push_{d}": (("clean", "dynamic"), "medium") for d in ("left", "right", "up", "down")},
    **{f"whip_{d}": (("dynamic",), "high") for d in ("left", "right", "up", "down")},
    **{f"luma_wipe_{d}": (("clean", "cinematic"), "medium")
       for d in ("left", "right", "up", "down", "diag")},
    "crosswarp": (("cinematic", "dynamic"), "medium"),
    "directional_warp": (("dynamic",), "medium"),
    "light_leak_warm": (("cinematic",), "medium"),
    "film_burn": (("cinematic",), "high"),
    "glitch_subtle": (("dynamic",), "high"),
    "spin_zoom": (("dynamic",), "high"),
    "morph_blend": (("cinematic",), "calm"),
    "cube_left": (("dynamic",), "medium"),
    "cube_right": (("dynamic",), "medium"),
}
CONTRACT_LEGACY = {
    "fade": "film_dissolve", "wipeleft": "luma_wipe_left", "slideleft": "push_left",
    "zoomin": "zoom_through", "fadeblack": "dip_black", "fadewhite": "dip_white",
}


def raw_yaml() -> dict[str, Any]:
    return yaml.safe_load((config_dir() / "transitions.yaml").read_text(encoding="utf-8"))


def load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_transition_previews", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# The YAML --------------------------------------------------------------------------------------


def test_yaml_is_valid_and_matches_the_contract_library() -> None:
    doc = TransitionCatalogueFile.model_validate(raw_yaml())
    assert doc.version == 2 and doc.default == "film_dissolve"
    assert doc.default_pack == "cinematic" and doc.last_scene == "dip_black"
    assert doc.shorts_duration_factor == 0.6
    entries = {t.id: t for t in doc.transitions}
    assert set(entries) == set(CONTRACT)
    for tid, (packs, energy) in CONTRACT.items():
        entry = entries[tid]
        assert set(entry.pack) == set(packs), tid
        assert entry.energy == energy, tid
        assert entry.label and entry.feel and entry.beats, tid
        assert entry.preview == f"transitions/{tid}.webp"
    assert entries["whip_left"].duration_s == 0.35
    assert {p: i.closer for p, i in doc.packs.items()} == dict.fromkeys(TRANSITION_PACKS,
                                                                         "dip_black")
    assert doc.packs["cinematic"].duration_s == [0.6, 0.8]
    assert doc.packs["clean"].duration_s == [0.5, 0.5]
    assert doc.packs["dynamic"].duration_s == [0.3, 0.45]
    assert {k: doc.legacy[k] for k in CONTRACT_LEGACY} == CONTRACT_LEGACY
    assert entries["light_leak_warm"].min_gap_s == 60 and entries["film_burn"].min_gap_s == 60
    assert entries["push_left"].easing == "ease_out_quart"


def test_strict_validation_catches_mistakes() -> None:
    data = raw_yaml()
    broken = {**data, "transitions": data["transitions"] + [data["transitions"][0]]}
    with pytest.raises(ValueError, match="twice"):
        TransitionCatalogueFile.model_validate(broken)
    with pytest.raises(ValueError, match="not an id"):
        TransitionCatalogueFile.model_validate({**data, "legacy": {"fade": "nope"}})
    with pytest.raises(ValueError):
        TransitionCatalogueFile.model_validate(
            {**data, "packs": {**data["packs"], "clean": {**data["packs"]["clean"],
                                                          "closer": "whip_left"}}})


# The runtime catalogue -------------------------------------------------------------------------


def test_lookups_legacy_names_and_lengths() -> None:
    catalog = transition_catalog()
    assert catalog.types == [t["id"] for t in raw_yaml()["transitions"]]
    for old, new in CONTRACT_LEGACY.items():
        assert catalog.resolve(old) == new
        assert catalog.get(old).type == new
    assert catalog.resolve("wiperight") == "luma_wipe_right"
    assert catalog.resolve("Slide Left") == "push_left"
    assert catalog.resolve(" Film-Dissolve ") == "film_dissolve"
    assert catalog.resolve("circleopen") == "film_dissolve"
    assert catalog.resolve("nothing-like-it") is None
    assert catalog.resolve_or_default("nothing-like-it") == "film_dissolve"
    assert catalog.is_legacy("fade") and not catalog.is_legacy("film_dissolve")
    assert set(catalog.known_names) >= set(CONTRACT) | set(CONTRACT_LEGACY)
    # Pack lengths: the entry's length brought into the pack's range; Shorts x 0.6.
    assert catalog.duration_for("film_dissolve", "long", "cinematic") == 0.7
    assert catalog.duration_for("film_dissolve", "long", "clean") == 0.5
    assert catalog.duration_for("film_dissolve", "long", "dynamic") == 0.45
    assert catalog.duration_for("whip_left", "long", "dynamic") == 0.35
    assert catalog.duration_for("whip_left", "long", "cinematic") == 0.35  # not in that pack
    assert catalog.duration_for("film_dissolve", "shorts", "cinematic") == 0.42
    assert catalog.duration_for("not-a-transition") == catalog.duration_for(catalog.default)
    assert [t.type for t in catalog.for_pack("dynamic")][:3] == ["film_dissolve", "dip_black",
                                                                  "dip_white"]
    assert all(t.in_pack("clean") for t in catalog.for_pack("clean"))
    assert catalog.closer_for("dynamic") == "dip_black"
    assert catalog.pack_or_default("wild") == "cinematic"


def test_runtime_loader_is_forgiving() -> None:
    empty = TransitionCatalog.from_mapping({})
    assert empty.types == ["film_dissolve", "dip_black"] and empty.default == "film_dissolve"
    v1 = TransitionCatalog.from_mapping({
        "default": "fade", "last_scene": "fadeblack",
        "transitions": [{"type": "fade", "label": "Cross fade", "duration_s": 0.6},
                        {"type": "fadeblack", "label": "Fade to black", "duration_s": 0.8}],
    })
    assert v1.types == ["fade", "fadeblack"] and v1.default == "fade"
    assert v1.last_scene == "fadeblack"
    data = raw_yaml()
    damaged = {**data, "transitions": [{"id": "Bad Id!"}, *data["transitions"]],
               "legacy": {**data["legacy"], "zap": "no_such_id"}}
    catalog = TransitionCatalog.from_mapping(damaged)
    assert "Bad Id!" not in catalog.types and len(catalog.types) == len(CONTRACT)
    assert catalog.resolve("zap") is None


def test_catalogue_json_shape() -> None:
    data = transition_catalog().to_json()
    json.dumps(data)
    row = next(r for r in data["transitions"] if r["id"] == "zoom_through")
    assert row["preview_url"] == preview_url("zoom_through") == "/transitions/zoom_through.webp"
    assert row["poster_url"] == poster_url("zoom_through")
    assert row["pack"] == ["cinematic", "dynamic"] and row["energy"] == "medium"
    assert data["packs"]["cinematic"]["closer"] == "dip_black"


def test_the_engine_draws_every_catalogue_id() -> None:
    registry = pytest.importorskip("cashcow_studio.render.transitions.registry")
    catalog = transition_catalog()
    assert set(catalog.types) == set(registry.ids())
    for transition in catalog.transitions:
        resolved = registry.resolve(transition.type)
        assert resolved.id == transition.type
        assert resolved.easing == transition.easing  # the catalogue's easing reaches the engine
    for old in CONTRACT_LEGACY:
        assert registry.resolve_id(old) == catalog.resolve(old)


# Previews --------------------------------------------------------------------------------------


def test_every_transition_has_its_committed_preview() -> None:
    catalog = transition_catalog()
    problems = load_tool().check(PREVIEWS)
    assert problems == [], problems
    data = json.loads((PREVIEWS / "catalogue.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in data["transitions"]] == catalog.types
    for row in data["transitions"]:
        entry = catalog.get(row["id"])
        assert row["label"] == entry.label and row["energy"] == entry.energy
        assert row["pack"] == list(entry.packs) and row["duration_s"] == entry.duration_s
        assert row["preview_url"] == preview_url(row["id"])
        assert row["preview_engine"] in ("engine", "stand-in")
    assert data["legacy"] == dict(catalog.legacy)
    for transition in catalog.transitions:
        with Image.open(PREVIEWS / f"{transition.type}.webp") as image:
            assert image.format == "WEBP" and image.size == (320, 180)
            assert getattr(image, "is_animated", False) and image.n_frames >= 2
        with Image.open(PREVIEWS / f"{transition.type}.poster.webp") as poster:
            assert poster.size == (320, 180)


def test_preview_tool_renders_animated_webp(tmp_path: Path) -> None:
    tool = load_tool()
    engines = tool.make_previews(tmp_path, only=["film_dissolve", "push_left"])
    assert set(engines) >= {"film_dissolve", "push_left"}
    with Image.open(tmp_path / "push_left.webp") as image:
        assert image.size == (320, 180) and image.n_frames >= 10
        image.seek(0)
        first = np.asarray(image.convert("RGB"), dtype=np.int16)
    a = np.asarray(tool.frame_a(), dtype=np.int16)
    assert np.abs(first - a).mean() < 6  # the loop starts on picture A (lossy WebP)
    data = json.loads((tmp_path / "catalogue.json").read_text(encoding="utf-8"))
    assert data["previews"]["frames"] == 24 and len(data["transitions"]) == len(CONTRACT)
    assert tool.check(tmp_path)  # only two previews were made here: the rest are missing


def test_stand_in_dissolve_and_easing_maths() -> None:
    tool = load_tool()
    a = np.asarray(tool.frame_a(), dtype=np.uint8)
    b = np.asarray(tool.frame_b(), dtype=np.uint8)
    assert np.abs(tool.stand_in_film_dissolve(a, b, 0.0).astype(int) - a).max() <= 1
    assert np.abs(tool.stand_in_film_dissolve(a, b, 1.0).astype(int) - b).max() <= 1
    distances = [np.abs(tool.stand_in_film_dissolve(a, b, t).astype(int) - b).mean()
                 for t in (0.2, 0.4, 0.6, 0.8)]
    assert distances == sorted(distances, reverse=True)  # always moving towards B
    for name in ("ease_in_out_cubic", "ease_out_quart", "linear", "snap"):
        assert tool.ease(name, 0.0) == pytest.approx(0.0) and tool.ease(name, 1.0) == 1.0
    steps = tool.progress_steps()
    assert len(steps) == 24 and steps[0] == 0.0 and steps[-1] == 1.0
    assert steps == sorted(steps) and sum(tool.durations_ms()) > 1500
