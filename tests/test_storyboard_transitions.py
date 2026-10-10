"""Transitions v2 in the storyboard stage (docs/TRANSITIONS-V2-CONTRACT.md section 4): the
deterministic fix-ups after the AI's draft, pack filtering, old xfade names, reviewer picks,
the variety histogram and gate, and the stage on the mock with each pack."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from cashcow_studio.config import Settings
from cashcow_studio.llm import MockLLMClient
from cashcow_studio.llm.config import transition_catalog
from cashcow_studio.models.channel import Channel
from cashcow_studio.models.project import Project, ProjectSource
from cashcow_studio.models.script import ScriptDoc
from cashcow_studio.models.storyboard import (
    Scene,
    SceneLocks,
    SceneMotion,
    ScenePopup,
    SceneTransition,
    StoryboardDoc,
)
from cashcow_studio.pipeline.stages.base import StageContext
from cashcow_studio.pipeline.stages.storyboard import (
    MAX_PER_WINDOW,
    WINDOW_CUTS,
    Rules,
    SentenceInfo,
    StoryboardStage,
    fix_timings,
    fix_transitions,
    gate_context,
    reviewer_picks,
    transition_histogram,
    variety_of,
)
from cashcow_studio.policy import evaluate

CATALOG = transition_catalog()


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def rules(pack: str = "cinematic", fmt: str = "long", *, enforce_pack: bool = True,
          pinned: frozenset[str] = frozenset()) -> Rules:
    band = (3.0, 4.0) if fmt == "shorts" else (8.0, 12.0)
    return Rules(min_s=band[0], max_s=band[1], fmt=fmt, catalog=CATALOG, style_guide="Style",
                 negative_rules="", popup_style="Box", pack=pack, enforce_pack=enforce_pack,
                 pinned_transitions=pinned)


def sentences(count: int, seconds: float = 10.0, sections: list[int] | None = None
              ) -> list[SentenceInfo]:
    """``count`` sentences of ``seconds`` each; ``sections[i]`` is sentence i's section number."""
    return [
        SentenceInfo(f"s-{i + 1:03d}", f"Sentence number {i + 1}.", seconds,
                     f"sec-{(sections[i] if sections else 1):02d}")
        for i in range(count)
    ]


def scenes_for(infos: list[SentenceInfo], types: list[str], locked: set[int] | None = None,
               seconds: float = 0.6) -> list[Scene]:
    """One scene per sentence with the given transition names, timed. ``seconds`` 0 asks the
    fix-ups for the default length (a length the reviewer typed is kept)."""
    result = [
        Scene(index=i, sentence_ids=[s.id], narration=s.text, image_prompt=f"Picture {i}",
              popup=ScenePopup(text=None), motion=SceneMotion(preset="zoom_in"),
              transition_out=SceneTransition(type=t, duration_s=seconds),
              locked=SceneLocks(transition_out=i in (locked or set())))
        for i, (s, t) in enumerate(zip(infos, types, strict=True))
    ]
    fix_timings(result, infos)
    return result


def types_of(scenes: list[Scene]) -> list[str]:
    return [s.transition_out.type for s in scenes]


def assert_rules_hold(scenes: list[Scene], pack: str | None = None) -> None:
    """The contract's rules on unlocked output (sections from the scene notes are not needed)."""
    ids = [CATALOG.resolve(t) or t for t in types_of(scenes)]
    for i in range(1, len(ids)):
        assert ids[i] != ids[i - 1], (i, ids)
        assert not (CATALOG.energy_of(ids[i]) == "high" and CATALOG.energy_of(ids[i - 1]) == "high")
    for start in range(max(1, len(ids) - WINDOW_CUTS + 1)):
        window = ids[start:start + WINDOW_CUTS]
        assert max(window.count(i) for i in set(window)) <= MAX_PER_WINDOW, window
    signature = [(s.est_end_s, CATALOG.resolve(s.transition_out.type)) for s in scenes
                 if CATALOG.get(s.transition_out.type) is not None
                 and CATALOG.get(s.transition_out.type).min_gap_s > 0]
    for (t1, _), (t2, _) in zip(signature, signature[1:], strict=False):
        assert t2 - t1 >= 60 - 1e-6, signature
    if pack is not None:
        assert ids[-1] == CATALOG.closer_for(pack)
        for scene, tid in zip(scenes, ids, strict=True):
            if not scene.locked.transition_out:
                assert CATALOG.get(tid).in_pack(pack), (tid, pack)


# Old names and unknown names ---------------------------------------------------------------


def test_old_xfade_names_map_to_v2_ids_with_a_note() -> None:
    infos = sentences(4)
    scenes = scenes_for(infos, ["fade", "wipeleft", "slideleft", "fadeblack"])
    warnings: list[str] = []
    fix_transitions(scenes, rules(enforce_pack=False), warnings, infos)
    assert types_of(scenes)[:3] == ["film_dissolve", "luma_wipe_left", "push_left"]
    assert types_of(scenes)[3] == "dip_black"  # the closer
    assert "old name" in scenes[0].notes[0] and "Film dissolve" in scenes[0].notes[0]
    assert scenes[1].transition_out.duration_s == CATALOG.duration_for("luma_wipe_left", "long",
                                                                       "cinematic")
    assert warnings == []


def test_unknown_names_are_replaced_from_the_pack() -> None:
    infos = sentences(3)
    scenes = scenes_for(infos, ["whoosh", "film_dissolve", "x"])
    fix_transitions(scenes, rules("clean"), [], infos)
    first = scenes[0]
    assert CATALOG.get(first.transition_out.type).in_pack("clean")
    assert any("'whoosh' is not in the list; replaced with" in n for n in first.notes)
    assert first.transition_out.duration_s == 0.5  # the clean pack's length


def test_locked_old_names_are_kept_and_count_as_their_v2_look() -> None:
    infos = sentences(3)
    scenes = scenes_for(infos, ["wipeleft", "luma_wipe_left", "film_dissolve"], locked={0})
    fix_transitions(scenes, rules(), [], infos)
    assert scenes[0].transition_out.type == "wipeleft"  # locked: never touched
    assert scenes[1].transition_out.type != "luma_wipe_left"  # the same look twice otherwise
    assert any("Transition changed" in n for n in scenes[1].notes)
    context = gate_context(_doc(scenes), CATALOG)
    results = {r.id: r for r in evaluate("storyboard", context)}
    assert results["storyboard.transition_known"].passed
    assert results["storyboard.transition_no_repeat"].passed


# Packs ---------------------------------------------------------------------------------------


def test_the_ai_draft_is_kept_inside_the_channel_pack() -> None:
    infos = sentences(4)
    scenes = scenes_for(infos, ["whip_left", "glitch_subtle", "light_leak_warm", "fade"])
    fix_transitions(scenes, rules("clean"), [], infos)
    ids = types_of(scenes)
    assert all(CATALOG.get(i).in_pack("clean") for i in ids), ids
    # The whip (a topic jump) became another topic-jump transition of the clean pack.
    assert "topic_jump" in CATALOG.get(ids[0]).beats
    assert "not in the channel's Clean pack" in scenes[0].notes[-1]


def test_a_reviewer_may_pick_from_any_pack() -> None:
    infos = sentences(3)
    scenes = scenes_for(infos, ["whip_left", "film_dissolve", "film_dissolve"])
    fix_transitions(scenes, rules("cinematic", enforce_pack=False), [], infos)
    assert types_of(scenes)[0] == "whip_left"


@pytest.mark.parametrize("pack", ["cinematic", "clean", "dynamic"])
def test_pack_lengths_and_closer(pack: str) -> None:
    infos = sentences(3)
    scenes = scenes_for(infos, ["film_dissolve", "dip_white", "film_dissolve"], seconds=0)
    fix_transitions(scenes, rules(pack), [], infos)
    low, high = CATALOG.pack_info(pack).duration_s
    for scene in scenes:
        assert low - 1e-9 <= scene.transition_out.duration_s <= high + 1e-9
    assert scenes[-1].transition_out.type == CATALOG.closer_for(pack) == "dip_black"
    shorts = scenes_for(infos, ["film_dissolve", "dip_white", "film_dissolve"], seconds=0)
    fix_transitions(shorts, rules(pack, "shorts"), [], infos)
    assert shorts[0].transition_out.duration_s == pytest.approx(
        scenes[0].transition_out.duration_s * 0.6, abs=0.011)
    # A length the reviewer typed is kept.
    typed = scenes_for(infos, ["film_dissolve", "dip_white", "film_dissolve"], seconds=1.2)
    fix_transitions(typed, rules(pack), [], infos)
    assert typed[0].transition_out.duration_s == 1.2


def test_a_locked_last_transition_stays() -> None:
    infos = sentences(2)
    scenes = scenes_for(infos, ["film_dissolve", "crosswarp"], locked={1})
    fix_transitions(scenes, rules(), [], infos)
    assert types_of(scenes) == ["film_dissolve", "crosswarp"]


def test_a_hand_picked_ending_stays_and_an_unchanged_one_gets_the_closer() -> None:
    infos = sentences(3)
    saved = scenes_for(infos, ["film_dissolve", "blur_dissolve", "dip_black"])
    edited = scenes_for(infos, ["film_dissolve", "blur_dissolve", "dip_white"])
    picked = reviewer_picks(_doc(edited), _doc(saved))
    fix_transitions(edited, rules(enforce_pack=False, pinned=picked), [], infos)
    assert edited[-1].transition_out.type == "dip_white"
    draft = scenes_for(infos, ["film_dissolve", "blur_dissolve", "dip_white"])
    fix_transitions(draft, rules(), [], infos)
    assert draft[-1].transition_out.type == "dip_black"


# The variety rules ---------------------------------------------------------------------------


def test_no_transition_twice_in_a_row_and_at_most_twice_in_ten_cuts() -> None:
    infos = sentences(24)
    scenes = scenes_for(infos, ["film_dissolve"] * 24)
    warnings: list[str] = []
    fix_transitions(scenes, rules(), warnings, infos)
    assert_rules_hold(scenes, "cinematic")
    assert warnings == []
    assert len(set(types_of(scenes))) >= 6


def test_signature_effects_stay_a_minute_apart() -> None:
    infos = sentences(14, seconds=8.0)  # a cut every 8 s
    draft = ["light_leak_warm", "film_dissolve", "film_burn", "blur_dissolve"] * 3 + \
        ["light_leak_warm", "film_dissolve"]
    scenes = scenes_for(infos, draft)
    fix_transitions(scenes, rules(), [], infos)
    assert_rules_hold(scenes, "cinematic")
    used = [(s.est_end_s, s.transition_out.type) for s in scenes
            if s.transition_out.type in ("light_leak_warm", "film_burn")]
    assert used and used[0] == (8.0, "light_leak_warm")
    assert any("kept at least 60 seconds apart" in n for s in scenes for n in s.notes)


def test_never_two_high_energy_cuts_in_a_row() -> None:
    infos = sentences(8)
    scenes = scenes_for(infos, ["whip_left", "whip_right", "glitch_subtle", "spin_zoom",
                                "whip_up", "whip_down", "spin_zoom", "film_dissolve"])
    fix_transitions(scenes, rules("dynamic"), [], infos)
    assert_rules_hold(scenes, "dynamic")
    assert any("two high-energy cuts in a row" in n for s in scenes for n in s.notes)


def test_dips_close_sections_one_per_section() -> None:
    # Sections of 4 sentences: cuts 3 and 7 are section ends.
    infos = sentences(12, sections=[1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3])
    draft = ["dip_black", "film_dissolve", "dip_white", "dip_black",
             "film_burn", "blur_dissolve", "morph_blend", "film_burn",
             "film_dissolve", "dip_white", "blur_dissolve", "film_dissolve"]
    scenes = scenes_for(infos, draft)
    fix_transitions(scenes, rules(), [], infos)
    ids = types_of(scenes)
    dips = {i for i, t in enumerate(ids[:-1]) if "section" in CATALOG.get(t).beats}
    # dip to black and film burn only where a section ends; dip to white may be a reveal.
    assert ids[0] != "dip_black" and ids[4] != "film_burn"
    assert ids[3] == "dip_black" and ids[7] == "film_burn"
    for section in ({0, 1, 2, 3}, {4, 5, 6, 7}, {8, 9, 10}):
        assert len(dips & section) <= 1, (ids, section)
    assert ids[-1] == "dip_black"
    assert any("end of a script section" in n for n in scenes[0].notes)


def test_reviewer_picks_win_over_unchanged_neighbours() -> None:
    infos = sentences(4)
    saved = scenes_for(infos, ["film_dissolve", "blur_dissolve", "crosswarp", "dip_black"])
    edited = scenes_for(infos, ["film_dissolve", "crosswarp", "crosswarp", "dip_black"])
    doc_before, doc_after = _doc(saved), _doc(edited)
    picked = reviewer_picks(doc_after, doc_before)
    assert picked == frozenset({"s-002"})
    fix_transitions(edited, rules(enforce_pack=False, pinned=picked), [], infos)
    assert edited[1].transition_out.type == "crosswarp"  # the reviewer's pick stays
    assert edited[2].transition_out.type != "crosswarp"  # its unchanged neighbour moves
    assert any("Transition changed" in n for n in edited[2].notes)
    assert reviewer_picks(doc_after, None) == frozenset()


def test_two_clashing_reviewer_picks_fix_the_later_one() -> None:
    infos = sentences(4)
    saved = scenes_for(infos, ["film_dissolve", "blur_dissolve", "morph_blend", "dip_black"])
    edited = scenes_for(infos, ["film_dissolve", "zoom_through", "zoom_through", "dip_black"])
    picked = reviewer_picks(_doc(edited), _doc(saved))
    fix_transitions(edited, rules(enforce_pack=False, pinned=picked), [], infos)
    assert edited[1].transition_out.type == "zoom_through"
    assert edited[2].transition_out.type != "zoom_through"


def test_locked_clashes_are_reported() -> None:
    infos = sentences(3)
    scenes = scenes_for(infos, ["whip_left", "whip_left", "film_dissolve"], locked={0, 1})
    warnings: list[str] = []
    fix_transitions(scenes, rules("dynamic"), warnings, infos)
    assert warnings and "both end with Whip left and are locked" in warnings[0]


def test_the_fix_ups_are_deterministic() -> None:
    infos = sentences(30, sections=[1] * 10 + [2] * 10 + [3] * 10)
    draft = ["zoom_through", "zoom_through", "dip_black", "light_leak_warm"] * 7 + ["fade"] * 2
    first = scenes_for(infos, list(draft))
    second = scenes_for(infos, list(draft))
    fix_transitions(first, rules(), [], infos)
    fix_transitions(second, rules(), [], infos)
    assert types_of(first) == types_of(second)
    assert [s.notes for s in first] == [s.notes for s in second]
    assert_rules_hold(first, "cinematic")


# Variety and the gate --------------------------------------------------------------------------


def test_histogram_counts_v2_ids_most_used_first() -> None:
    infos = sentences(5)
    scenes = scenes_for(infos, ["fade", "blur_dissolve", "film_dissolve", "blur_dissolve",
                                "dip_black"])
    assert transition_histogram(scenes, CATALOG) == {
        "blur_dissolve": 2, "film_dissolve": 2, "dip_black": 1,
    }
    variety = variety_of(scenes, rules(), [])
    assert variety.distinct_transitions == 3
    assert variety.transition_histogram == {"blur_dissolve": 2, "film_dissolve": 2,
                                            "dip_black": 1}


def test_visual_variety_gate() -> None:
    def judge(types: list[str], fmt: str = "long") -> Any:
        infos = sentences(len(types))
        doc = _doc(scenes_for(infos, types), fmt=fmt)
        results = {r.id: r for r in evaluate("storyboard", gate_context(doc, CATALOG))}
        return results["storyboard.visual_variety"]

    varied = ["film_dissolve", "blur_dissolve", "crosswarp", "zoom_through"] * 2 + ["dip_black"]
    assert judge(varied).passed
    plain = ["film_dissolve", "blur_dissolve", "film_dissolve", "blur_dissolve"] * 2 + ["dip_black"]
    result = judge(plain)
    assert not result.passed and result.severity == "warn" and "Only 3" in result.detail
    assert judge(plain, fmt="shorts").passed  # Shorts are not judged
    assert judge(["film_dissolve", "dip_black"]).passed  # too few scenes to judge


# The stage on the mock -------------------------------------------------------------------------


def write_script(folder: Any, sections: int = 3, per_section: int = 6) -> ScriptDoc:
    words = "the night train carried letters across the frozen plain toward the harbour lights"
    vocabulary = words.split()
    rows = []
    number = 0
    for s in range(sections):
        sentences_in = []
        for _ in range(per_section):
            number += 1
            text = " ".join(vocabulary[(number + k) % len(vocabulary)] for k in range(14))
            sentences_in.append({"id": f"s-{number:03d}", "text": text.capitalize() + "."})
        rows.append({"id": f"sec-{s + 1:02d}", "name": f"Part {s + 1}", "paragraphs": [
            {"id": f"p-{s + 1:02d}-01", "text": " ".join(x["text"] for x in sentences_in),
             "sentences": sentences_in}]})
    doc = ScriptDoc.model_validate({
        "title": "Night Train", "language": "English", "format": "long", "target_words": 300,
        "word_count": 14 * number, "model": "mock", "generated_at": datetime.now(UTC),
        "speaking_rate_wpm": 150, "sections": rows,
    })
    script_dir = folder / "03_script"
    script_dir.mkdir(parents=True, exist_ok=True)
    (script_dir / "script.json").write_text(doc.model_dump_json(), encoding="utf-8")
    return doc


def make_ctx(app_env: Any, pack: str, project_id: str, motion_style: str = "ken_burns"
             ) -> StageContext:
    settings = Settings()
    folder = app_env.projects_dir / project_id
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    project = Project(id=project_id, channel_slug="night-train", topic_slug="train",
                      title="Night Train", format="long", created_at=now, updated_at=now,
                      folder=str(folder),
                      source=ProjectSource(kind="own_topic", topic_text="Night Train"))
    channel = Channel.model_validate({
        "slug": "night-train", "channel": {"name": "Night Train"},
        "images": {"transition_pack": pack, "motion_style": motion_style},
    })
    return StageContext(project=project, channel=channel, settings=settings, folder=folder,
                        providers={"llm": MockLLMClient(app_data_dir=settings.app_data_dir)},
                        notes=[])


@pytest.mark.parametrize("pack", ["cinematic", "clean", "dynamic"])
def test_stage_plans_from_the_channel_pack(app_env: Any, pack: str) -> None:
    ctx = make_ctx(app_env, pack, f"pack-{pack}")
    write_script(ctx.folder)
    result = run(StoryboardStage().run(ctx))
    doc = StoryboardDoc.model_validate_json(
        (ctx.folder / "04_storyboard" / "storyboard.json").read_text(encoding="utf-8"))
    assert doc.transition_pack == pack and doc.motion_style is None
    assert_rules_hold(doc.scenes, pack)
    assert doc.variety.transition_histogram
    assert sum(doc.variety.transition_histogram.values()) == len(doc.scenes)
    payload = result.needs_review_payload
    assert payload["transition_pack"] == pack and payload["channel_motion_style"] == "ken_burns"
    in_pack = [t for t in payload["transitions"] if t["in_pack"]]
    assert in_pack and payload["transitions"][: len(in_pack)] == in_pack  # the pack comes first
    assert {t["id"] for t in payload["transitions"]} == set(CATALOG.types)
    assert all(t["preview_url"].startswith("/transitions/") for t in payload["transitions"])
    blocking = [g for g in result.gate_results if g["severity"] == "block" and not g["passed"]]
    assert blocking == []
    # The model saw the pack (with energy and feel) and the closer.
    variables = ctx.providers["llm"].calls[-1]["variables"]
    assert variables["transition_types"] == [t.type for t in CATALOG.for_pack(pack)]
    assert len(variables["transition_options"]) == len(CATALOG.for_pack(pack))
    assert variables["transition_closer"] == "dip_black (Dip to black)"
    assert variables["transition_pack"].startswith(CATALOG.pack_info(pack).label)


def test_prompt_lists_the_pack_with_energy_and_feel(app_env: Any) -> None:
    from cashcow_studio.llm.prompts import load_prompt
    from cashcow_studio.pipeline.stages.storyboard import (
        pack_description,
        sentence_lines,
        transition_prompt_lines,
    )

    lines = transition_prompt_lines(rules("dynamic"))
    assert len(lines) == len(CATALOG.for_pack("dynamic"))
    whip = next(line for line in lines if line.startswith("whip_left |"))
    assert "| high |" in whip and "0.35 s" in whip and "jumps to a new idea" in whip
    leak = transition_prompt_lines(rules("cinematic"))
    assert any("light_leak_warm" in line and "once a minute" in line for line in leak)
    infos = sentences(4, sections=[1, 1, 2, 2])
    text = sentence_lines(infos, {"sec-01": "Hook", "sec-02": "Turn"})
    assert text[0] == "[Section 1: Hook]" and text[3] == "[Section 2: Turn]"
    prompt = load_prompt("storyboard")
    assert prompt.placeholders("system") == []
    rendered = prompt.render("user", {
        "format": "long-form", "aspect": "16:9", "min_s": 8, "max_s": 12, "style_guide": "S",
        "negative_rules": "N", "popup_style": "P", "motion_presets": "zoom_in",
        "popup_positions": "center", "transition_pack": pack_description(rules("dynamic")),
        "transition_options": lines, "transition_closer": "dip_black (Dip to black)",
        "sentences": text, "locked_scenes": ["(none)"], "notes": ["(none)"],
    })
    assert "Dynamic. Fast and punchy." in rendered and "whip_left | Whip left" in rendered
    assert "transition_beat" in prompt.system and "section" in prompt.system


def test_approval_edits_keep_reviewer_picks_and_motion_style(app_env: Any) -> None:
    ctx = make_ctx(app_env, "cinematic", "edits", motion_style="ken_burns")
    write_script(ctx.folder)
    run(StoryboardStage().run(ctx))
    path = ctx.folder / "04_storyboard" / "storyboard.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["motion_style"] = "parallax"
    data["scenes"][0]["transition_out"] = {"type": "whip_left", "duration_s": 0.35}
    if len(data["scenes"]) > 2:
        data["scenes"][1]["transition_out"] = {"type": "whip_right", "duration_s": 0.35}
    ctx.edits = {"storyboard": data}
    result = run(StoryboardStage().apply_edits(ctx))
    doc = StoryboardDoc.model_validate_json(path.read_text(encoding="utf-8"))
    assert doc.motion_style == "parallax"
    assert doc.scenes[0].transition_out.type == "whip_left"  # another pack, picked by hand
    if len(doc.scenes) > 2:
        # Two hand-picked high-energy cuts in a row: the later one gives way.
        assert doc.scenes[1].transition_out.type != "whip_right"
        assert CATALOG.energy_of(doc.scenes[1].transition_out.type) != "high"
    assert result.needs_review_payload["channel_motion_style"] == "ken_burns"
    # A regenerate keeps the video's motion style.
    ctx.edits = {}
    run(StoryboardStage().run(ctx))
    redone = StoryboardDoc.model_validate_json(path.read_text(encoding="utf-8"))
    assert redone.motion_style == "parallax"


# Channel fields --------------------------------------------------------------------------------


def test_channel_images_gain_pack_and_motion_style() -> None:
    channel = Channel.model_validate({"slug": "a-b", "channel": {"name": "A"}})
    assert channel.images.transition_pack == "cinematic"
    assert channel.images.motion_style == "ken_burns"
    picked = Channel.model_validate({"slug": "a-b", "channel": {"name": "A"},
                                     "images": {"transition_pack": "dynamic",
                                                "motion_style": "parallax"}})
    assert picked.images.transition_pack == "dynamic" and picked.images.motion_style == "parallax"
    with pytest.raises(ValueError):
        Channel.model_validate({"slug": "a-b", "channel": {"name": "A"},
                                "images": {"transition_pack": "wild"}})


def _doc(scenes: list[Scene], fmt: str = "long") -> StoryboardDoc:
    return StoryboardDoc(project_id="p", format=fmt, aspect="16:9",  # type: ignore[arg-type]
                         generated_at=datetime.now(UTC), model="mock",
                         scenes=[s.model_copy(deep=True) for s in scenes])
