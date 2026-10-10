"""Storyboard stage: the script's sentences become scenes with a picture, a camera move, a
popup and a transition each. Claude drafts; deterministic rules then fix what the draft got
wrong (scene length, repeated motion or transition, long popups, text share, style prefix).

Reads ``03_script/script.json`` (+ ``speech.json`` for timing), the channel's image style
guide, popup style and transition pack, and ``config/transitions.yaml``; writes
``04_storyboard/storyboard.json``. Approval edits replace the document; the rules run again on
everything that is not locked. A redo reads the previous ``storyboard.json`` first: every
field a reviewer locked (image prompt, popup, motion, transition, the sentence grouping behind
a locked narration) is carried over to the new draft, so a regenerate never throws approved
work away.

Transitions (docs/TRANSITIONS-V2-CONTRACT.md section 4): the AI picks one per cut from the
channel's pack by the story beat (calm, reveal, topic jump, section end); the fix-ups then
map old xfade names to v2 ids and keep the cuts varied: never the same transition twice in a
row, at most two of one id in any ten cuts, signature effects (light leak, film burn) at most
once a minute, never two high-energy cuts in a row, at most one dip per script section, and
the pack's closer on the last scene. A transition the reviewer picked by hand wins over its
unchanged neighbours; locked ones never change.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ...llm.config import TransitionCatalog, speaking_rate_wpm, transition_catalog
from ...llm.prompts import load_prompt
from ...llm.text import count_words, keyword_phrase, normalise
from ...models.project import StageName
from ...models.script import SpeechDoc
from ...models.storyboard import (
    MAX_POPUP_WORDS,
    MOTION_PRESETS,
    POPUP_POSITIONS,
    MotionPreset,
    PopupPosition,
    Scene,
    SceneMotion,
    ScenePopup,
    SceneTransition,
    StoryboardApproveEdits,
    StoryboardDoc,
    StoryboardDraft,
    StoryboardReviewPayload,
    StoryboardVariety,
)
from ...models.transitions import (
    BEAT_LABELS,
    BEATS,
    DEFAULT_TRANSITION_PACK,
    ENERGY_LABELS,
    PACK_LABELS,
    poster_url,
    preview_url,
)
from ...policy import gates
from ...storage.settings_store import atomic_write_text
from .base import GateBlocked, StageContext, StageError, StageResult
from .script import read_model, read_script_doc

log = logging.getLogger(__name__)

STORYBOARD_FILE = "storyboard.json"
TASK = "storyboard"
MIN_SENTENCE_S = 0.6
POPUP_IN_S = 0.4
POPUP_MAX_SHOW_S = 3.5
DEFAULT_STYLE_GUIDE = (
    "Cinematic photo-realism, soft natural light, muted warm palette, shallow depth of field, "
    "no text, no logos, no real public figures"
)
DEFAULT_NEGATIVE = (
    "text, letters, words, captions, subtitles, watermark, logo, real people's faces, "
    "celebrities, deformed hands, extra fingers"
)
AVOID_MARKER = " Avoid: "

# Fractions of the frame [x, y, w, h] at the start and the end of the move.
MOTION_RECTS: dict[str, tuple[list[float], list[float]]] = {
    "zoom_in": ([0.0, 0.0, 1.0, 1.0], [0.1, 0.1, 0.8, 0.8]),
    "zoom_out": ([0.1, 0.1, 0.8, 0.8], [0.0, 0.0, 1.0, 1.0]),
    "pan_left": ([0.15, 0.0, 0.85, 1.0], [0.0, 0.0, 0.85, 1.0]),
    "pan_right": ([0.0, 0.0, 0.85, 1.0], [0.15, 0.0, 0.85, 1.0]),
    "pan_up": ([0.0, 0.15, 1.0, 0.85], [0.0, 0.0, 1.0, 0.85]),
    "pan_down": ([0.0, 0.0, 1.0, 0.85], [0.0, 0.15, 1.0, 0.85]),
    "hold": ([0.0, 0.0, 1.0, 1.0], [0.0, 0.0, 1.0, 1.0]),
    "slow_push": ([0.0, 0.0, 1.0, 1.0], [0.04, 0.04, 0.92, 0.92]),
}


@dataclass(frozen=True)
class SentenceInfo:
    id: str
    text: str
    est_s: float
    section: str = ""
    """The script section the sentence belongs to (``sec-01``...); "" when unknown."""


@dataclass
class Rules:
    """Everything the fix-ups need besides the scenes."""

    min_s: float
    max_s: float
    fmt: str
    catalog: TransitionCatalog
    style_guide: str
    negative_rules: str
    popup_style: str
    max_popup_words: int = MAX_POPUP_WORDS
    min_text_share: float = 0.35
    pack: str = DEFAULT_TRANSITION_PACK
    """The channel's transition pack: the AI picks from it and replacements come from it."""
    enforce_pack: bool = True
    """Replace transitions from other packs (the AI's draft); a reviewer may pick any."""
    pinned_transitions: frozenset[str] = frozenset()
    """First sentence ids of scenes whose transition the reviewer just picked by hand: their
    unchanged neighbours give way to them."""


# Timing -----------------------------------------------------------------------------------------


def estimate_seconds(text: str, wpm: int) -> float:
    return round(max(MIN_SENTENCE_S, count_words(text) / max(60, wpm) * 60.0), 2)


def sentence_sections(script: Any) -> dict[str, str]:
    """``sentence id -> section id`` from the script's sections (empty when it has none)."""
    sections: dict[str, str] = {}
    for section in getattr(script, "sections", None) or []:
        for paragraph in getattr(section, "paragraphs", None) or []:
            for sentence in getattr(paragraph, "sentences", None) or []:
                sections.setdefault(str(sentence.id), str(getattr(section, "id", "") or ""))
    return sections


def section_names(script: Any) -> dict[str, str]:
    return {
        str(getattr(section, "id", "")): str(getattr(section, "name", "") or "")
        for section in getattr(script, "sections", None) or []
    }


def sentence_infos(script: Any, speech: SpeechDoc | None, wpm: int) -> list[SentenceInfo]:
    spoken = {s.id: s.speech_text for s in speech.sentences} if speech else {}
    sections = sentence_sections(script)
    return [
        SentenceInfo(s.id, s.text, estimate_seconds(spoken.get(s.id) or s.text, wpm),
                     sections.get(s.id, ""))
        for s in script.sentences()
    ]


def group_by_duration(items: list[SentenceInfo], max_s: float) -> list[list[SentenceInfo]]:
    """Consecutive chunks totalling at most ``max_s`` (one long sentence stands alone)."""
    chunks: list[list[SentenceInfo]] = []
    current: list[SentenceInfo] = []
    total = 0.0
    for item in items:
        if current and total + item.est_s > max_s + 1e-9:
            chunks.append(current)
            current, total = [], 0.0
        current.append(item)
        total += item.est_s
    if current:
        chunks.append(current)
    return chunks


def group_for_band(
    items: list[SentenceInfo], min_s: float, max_s: float
) -> list[list[SentenceInfo]]:
    """Scenes inside the band wherever possible: flush once the total reaches ``min_s``."""
    chunks: list[list[SentenceInfo]] = []
    current: list[SentenceInfo] = []
    total = 0.0
    for item in items:
        if current and total + item.est_s > max_s + 1e-9:
            chunks.append(current)
            current, total = [], 0.0
        current.append(item)
        total += item.est_s
        if total >= min_s:
            chunks.append(current)
            current, total = [], 0.0
    if current:
        chunks.append(current)
    return chunks


def split_balanced(items: list[SentenceInfo], max_s: float) -> list[list[SentenceInfo]]:
    """As few chunks as ``max_s`` allows, each about the same length (no short tail)."""
    total = sum(item.est_s for item in items)
    if not items or total <= max_s + 1e-9:
        return [list(items)] if items else []
    count = max(2, math.ceil(total / max_s - 1e-9))
    prefix = [0.0]
    for item in items:
        prefix.append(prefix[-1] + item.est_s)
    # Cut where the running total passes closest to each even share of the total.
    cuts: list[int] = []
    previous = 0
    for j in range(1, count):
        target = total * j / count
        candidates = range(previous + 1, len(items) - (count - j) + 1)
        if not candidates:
            break
        cut = min(candidates, key=lambda i: (abs(prefix[i] - target), i))
        cuts.append(cut)
        previous = cut
    bounds = [0, *cuts, len(items)]
    chunks = [items[a:b] for a, b in zip(bounds, bounds[1:], strict=False) if b > a]
    if any(sum(s.est_s for s in chunk) > max_s + 1e-9 for chunk in chunks):
        return group_by_duration(items, max_s)
    return chunks


# Building from the draft ------------------------------------------------------------------


def _preset(value: str | None, index: int) -> MotionPreset:
    text = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in MOTION_PRESETS:
        return text  # type: ignore[return-value]
    return MOTION_PRESETS[index % len(MOTION_PRESETS)]


def _position(value: str | None, index: int) -> PopupPosition:
    text = (value or "").strip().lower().replace("_", "-").replace(" ", "-")
    if text in POPUP_POSITIONS:
        return text  # type: ignore[return-value]
    return POPUP_POSITIONS[index % len(POPUP_POSITIONS)]


def motion_for(preset: str) -> SceneMotion:
    start, end = MOTION_RECTS.get(preset, MOTION_RECTS["zoom_in"])
    return SceneMotion(preset=preset, start_rect=list(start), end_rect=list(end))  # type: ignore[arg-type]


def _beat(value: str | None) -> str | None:
    """The draft's beat for a cut, or None when it named none (or something unknown)."""
    text = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"topic": "topic_jump", "jump": "topic_jump", "section_end": "section",
               "boundary": "section", "section_boundary": "section", "end": "section"}
    text = aliases.get(text, text)
    return text if text in BEATS else None


def draft_transition(raw_type: str | None, raw_beat: str | None, rules: Rules) -> SceneTransition:
    """The draft's transition as written (the fix-ups map and check it later)."""
    name = (raw_type or "").strip() or rules.catalog.default
    return SceneTransition(
        type=name,
        duration_s=rules.catalog.duration_for(name, rules.fmt, rules.pack),
        beat=_beat(raw_beat),
    )


def prompt_from_narration(narration: str) -> str:
    words = " ".join(narration.split()[:14]).rstrip(".,;:!?")
    return (
        f"A cinematic, text-free still that captures this moment: {words}. Natural light, "
        "medium shot, calm mood."
    )


def scenes_from_draft(
    draft: StoryboardDraft, sentences: list[SentenceInfo], rules: Rules
) -> list[Scene]:
    """Draft scenes -> Scene objects covering every sentence exactly once, in script order."""
    order = {s.id: i for i, s in enumerate(sentences)}
    by_id = {s.id: s for s in sentences}
    used: set[str] = set()
    scenes: list[Scene] = []
    for index, raw in enumerate(draft.scenes):
        ids = [i for i in raw.sentence_ids if i in by_id and i not in used]
        if not ids:
            continue
        used.update(ids)
        ids.sort(key=lambda i: order[i])
        narration = " ".join(by_id[i].text for i in ids)
        preset = _preset(raw.motion_preset, index)
        scenes.append(
            Scene(
                index=index,
                sentence_ids=ids,
                narration=narration,
                image_prompt=(raw.image_prompt or "").strip() or prompt_from_narration(narration),
                negative_prompt=(raw.negative_prompt or "").strip(),
                popup=ScenePopup(
                    text=(raw.popup_text or "").strip() or None,
                    style=rules.popup_style,
                    position=_position(raw.popup_position, index),
                ),
                motion=motion_for(preset),
                transition_out=draft_transition(raw.transition_type, raw.transition_beat, rules),
                on_screen_text=(raw.on_screen_text or "").strip() or None,
            )
        )
    missing = [s for s in sentences if s.id not in used]
    scenes = _insert_missing(scenes, missing, sentences, rules)
    scenes.sort(key=lambda sc: order[sc.sentence_ids[0]])
    return scenes


def _insert_missing(
    scenes: list[Scene], missing: list[SentenceInfo], sentences: list[SentenceInfo], rules: Rules
) -> list[Scene]:
    """Sentences the draft skipped join the scene of the previous sentence, or form new ones."""
    if not missing:
        return scenes
    if not scenes:
        return [
            _new_scene(chunk, i, rules, note="Added by the app: the draft covered no sentences.")
            for i, chunk in enumerate(group_for_band(missing, rules.min_s, rules.max_s))
        ]
    order = {s.id: i for i, s in enumerate(sentences)}
    by_id = {s.id: s for s in sentences}
    for item in missing:
        position = order[item.id]
        host = None
        for scene in scenes:
            if any(order[i] == position - 1 for i in scene.sentence_ids):
                host = scene
                break
        if host is not None and not host.locked.narration:
            host.sentence_ids.append(item.id)
            host.sentence_ids.sort(key=lambda i: order[i])
            host.narration = " ".join(by_id[i].text for i in host.sentence_ids)
            host.notes.append(f"Sentence {item.id} was missing from the draft and was added here.")
        else:
            scenes.append(_new_scene([item], len(scenes), rules,
                                     note=f"Sentence {item.id} was missing from the draft."))
        order_pos = {sc.sentence_ids[0]: order[sc.sentence_ids[0]] for sc in scenes}
        scenes.sort(key=lambda sc: order_pos[sc.sentence_ids[0]])
    return scenes


def _new_scene(chunk: list[SentenceInfo], index: int, rules: Rules, *, note: str = "",
               like: Scene | None = None) -> Scene:
    narration = " ".join(s.text for s in chunk)
    preset = like.motion.preset if like else MOTION_PRESETS[index % len(MOTION_PRESETS)]
    transition = like.transition_out.type if like else rules.catalog.default
    scene = Scene(
        index=index,
        sentence_ids=[s.id for s in chunk],
        narration=narration,
        image_prompt=prompt_from_narration(narration),
        negative_prompt=like.negative_prompt if like else "",
        popup=ScenePopup(text=None, style=rules.popup_style,
                         position=like.popup.position if like else POPUP_POSITIONS[index % 5]),
        motion=motion_for(preset),
        transition_out=SceneTransition(
            type=transition,
            duration_s=rules.catalog.duration_for(transition, rules.fmt, rules.pack),
        ),
    )
    if note:
        scene.notes.append(note)
    return scene


# Fix-ups ---------------------------------------------------------------------------------------


def _duration(scene: Scene, est: dict[str, float]) -> float:
    return round(sum(est.get(i, 0.0) for i in scene.sentence_ids), 2)


def fix_scene_lengths(
    scenes: list[Scene], sentences: list[SentenceInfo], rules: Rules
) -> list[Scene]:
    """Split scenes over ``max_s`` and merge scenes under ``min_s`` with a neighbour.

    Scenes whose narration is locked keep their sentences. A scene that stays outside the band
    gets a note; the variety summary reports it.
    """
    est = {s.id: s.est_s for s in sentences}
    by_id = {s.id: s for s in sentences}
    # Split.
    result: list[Scene] = []
    for scene in scenes:
        if scene.locked.narration or _duration(scene, est) <= rules.max_s + 1e-9:
            result.append(scene)
            continue
        chunks = split_balanced([by_id[i] for i in scene.sentence_ids], rules.max_s)
        if len(chunks) == 1:
            scene.notes.append(
                f"One sentence alone runs {_duration(scene, est):g} s, "
                f"longer than {rules.max_s:g} s."
            )
            result.append(scene)
            continue
        original_index = scene.index
        for ci, chunk in enumerate(chunks):
            if ci == 0:
                scene.sentence_ids = [s.id for s in chunk]
                scene.narration = " ".join(s.text for s in chunk)
                scene.notes.append(
                    f"Split by the app: the scene ran longer than {rules.max_s:g} s."
                )
                result.append(scene)
            else:
                result.append(_new_scene(
                    chunk, len(result), rules,
                    note=f"Split from scene {original_index + 1} by the app (too long).",
                    like=scene,
                ))
    scenes = result
    # Merge.
    changed, passes = True, 0
    while changed and passes <= len(scenes) + 2:
        changed, passes = False, passes + 1
        for i, scene in enumerate(scenes):
            if scene.locked.narration or _duration(scene, est) >= rules.min_s - 1e-9:
                continue
            nxt = scenes[i + 1] if i + 1 < len(scenes) else None
            prv = scenes[i - 1] if i > 0 else None
            if nxt is not None and not nxt.locked.narration and \
                    _duration(scene, est) + _duration(nxt, est) <= rules.max_s + 1e-9:
                scene.sentence_ids = scene.sentence_ids + nxt.sentence_ids
                scene.narration = " ".join(by_id[x].text for x in scene.sentence_ids if x in by_id)
                scene.notes.append("Merged with the next scene by the app (both were short).")
                scenes.pop(i + 1)
                changed = True
                break
            if prv is not None and not prv.locked.narration and \
                    _duration(prv, est) + _duration(scene, est) <= rules.max_s + 1e-9:
                prv.sentence_ids = prv.sentence_ids + scene.sentence_ids
                prv.narration = " ".join(by_id[x].text for x in prv.sentence_ids if x in by_id)
                prv.notes.append("Merged with the following short scene by the app.")
                scenes.pop(i)
                changed = True
                break
            if prv is not None and _rebalance(prv, scene, by_id, rules):
                changed = True
                break
            if nxt is not None and _rebalance(scene, nxt, by_id, rules):
                changed = True
                break
    for scene in scenes:
        duration = _duration(scene, est)
        if duration < rules.min_s - 1e-9 and not any("shorter than" in n for n in scene.notes):
            scene.notes.append(f"Scene runs {duration:g} s, shorter than {rules.min_s:g} s.")
    return scenes


def _rebalance(
    first: Scene, second: Scene, by_id: dict[str, SentenceInfo], rules: Rules
) -> bool:
    """Move the boundary between two neighbours so both land in the band, if possible."""
    if first.locked.narration or second.locked.narration:
        return False
    union = [by_id[i] for i in first.sentence_ids + second.sentence_ids if i in by_id]
    chunks = split_balanced(union, rules.max_s)
    if len(chunks) != 2:
        return False
    durations = [sum(s.est_s for s in chunk) for chunk in chunks]
    if not all(rules.min_s - 1e-9 <= d <= rules.max_s + 1e-9 for d in durations):
        return False
    new_first = [s.id for s in chunks[0]]
    new_second = [s.id for s in chunks[1]]
    if new_first == first.sentence_ids and new_second == second.sentence_ids:
        return False
    first.sentence_ids, second.sentence_ids = new_first, new_second
    for scene in (first, second):
        scene.narration = " ".join(by_id[i].text for i in scene.sentence_ids)
        scene.notes.append("Scene boundary moved by the app to even out the lengths.")
    return True


def fix_timings(scenes: list[Scene], sentences: list[SentenceInfo]) -> None:
    est = {s.id: s.est_s for s in sentences}
    clock = 0.0
    for index, scene in enumerate(scenes):
        scene.index = index
        duration = _duration(scene, est)
        scene.est_start_s = round(clock, 2)
        scene.est_duration_s = duration
        clock += duration
        scene.est_end_s = round(clock, 2)


def _unlocked_neighbour(i: int, locked_here: bool, locked_before: bool) -> int | None:
    """Which of scenes i and i-1 may change: i, else i-1, else nothing (None)."""
    if not locked_here:
        return i
    return i - 1 if not locked_before else None


def fix_motion(scenes: list[Scene], warnings: list[str]) -> None:
    """No two neighbours share a preset; the unlocked neighbour changes."""
    for scene in scenes:
        if not scene.locked.motion:
            scene.motion = motion_for(scene.motion.preset)
    for i in range(1, len(scenes)):
        if scenes[i].motion.preset != scenes[i - 1].motion.preset:
            continue
        target = _unlocked_neighbour(i, scenes[i].locked.motion, scenes[i - 1].locked.motion)
        if target is None:
            warnings.append(
                f"Scenes {i} and {i + 1} both use {scenes[i].motion.preset} and are locked."
            )
            continue
        avoid = {scenes[j].motion.preset for j in (target - 1, target + 1) if 0 <= j < len(scenes)}
        preset = next(p for p in MOTION_PRESETS if p not in avoid)
        scenes[target].motion = motion_for(preset)
        scenes[target].notes.append(f"Motion changed to {preset} by the app (same as a neighbour).")


WINDOW_CUTS = 10
MAX_PER_WINDOW = 2
"""One transition at most this many times in any ``WINDOW_CUTS`` consecutive cuts."""


def transition_label(catalog: TransitionCatalog, name: str | None) -> str:
    transition = catalog.get(name)
    return transition.label if transition else str(name or "")


def is_dip(catalog: TransitionCatalog, name: str | None) -> bool:
    """A transition that closes a section: dip to black or white, film burn."""
    transition = catalog.get(name)
    return transition is not None and "section" in transition.beats


def is_boundary_only(catalog: TransitionCatalog, name: str | None) -> bool:
    """A dip that reads as "the chapter ends" (dip to black, film burn): kept for the end of a
    script section. Dip to white also suits a reveal, so it may come mid-section."""
    transition = catalog.get(name)
    return (transition is not None and "section" in transition.beats
            and "reveal" not in transition.beats)


def signature_names(catalog: TransitionCatalog) -> str:
    """"Warm light leak and Film burn": the transitions with a minimum gap, for notes."""
    labels = [t.label for t in catalog.transitions if t.min_gap_s > 0]
    if len(labels) <= 1:
        return labels[0] if labels else ""
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def scene_sections(scenes: list[Scene], sentences: list[SentenceInfo] | None) -> list[str]:
    """The script section of every scene (its first sentence's); "" when unknown."""
    by_id = {s.id: s.section for s in sentences or []}
    return [by_id.get(scene.sentence_ids[0], "") if scene.sentence_ids else "" for scene in scenes]


@dataclass
class _Cuts:
    """Working state of the transition fix-ups: the cut out of every scene, in order.

    ``ids`` holds v2 ids (an unknown locked name stays as written); a cut is ``decided`` once
    it is final: locked, picked by the reviewer, the closer, or already checked.
    """

    catalog: TransitionCatalog
    ids: list[str | None]
    times: list[float]
    sections: list[str]
    decided: list[bool]

    def _other(self, i: int, j: int) -> str | None:
        if j == i or not 0 <= j < len(self.ids) or not self.decided[j]:
            return None
        return self.ids[j]

    def problems(self, i: int, candidate: str, *, strict: bool = True) -> list[str]:
        """Why ``candidate`` may not go on cut ``i`` next to the decided cuts (empty = fine).

        The adjacency and energy rules always count; ``strict`` adds the ten-cut window, the
        minimum gap of signature effects and the one-dip-per-section rule.
        """
        catalog = self.catalog
        count = len(self.ids)
        reasons: list[str] = []
        if self._other(i, i - 1) == candidate:
            reasons.append("the cut before uses it too")
        if self._other(i, i + 1) == candidate:
            reasons.append("the next cut uses it too")
        transition = catalog.get(candidate)
        if transition is not None and transition.energy == "high":
            neighbours = [self._other(i, j) for j in (i - 1, i + 1)]
            if any(n is not None and catalog.energy_of(n) == "high" for n in neighbours):
                reasons.append("two high-energy cuts in a row")
        if not strict:
            return reasons
        last_start = max(0, count - WINDOW_CUTS)
        for start in range(max(0, i - WINDOW_CUTS + 1), min(i, last_start) + 1):
            window = range(start, min(count, start + WINDOW_CUTS))
            uses = 1 + sum(1 for j in window if self._other(i, j) == candidate)
            if uses > MAX_PER_WINDOW:
                reasons.append(f"it would be used more than {MAX_PER_WINDOW} times in "
                               f"{WINDOW_CUTS} cuts")
                break
        gap = transition.min_gap_s if transition is not None else 0.0
        if gap > 0:
            # Signature effects (light leak, film burn) keep their distance from each other.
            for j in range(count):
                other = catalog.get(self._other(i, j))
                if other is None or other.min_gap_s <= 0:
                    continue
                limit = max(gap, other.min_gap_s)
                if abs(self.times[j] - self.times[i]) < limit:
                    reasons.append(f"{signature_names(catalog)} are kept at least {limit:g} "
                                   "seconds apart")
                    break
        inside = i < count - 1 and bool(self.sections[i])
        if inside and is_boundary_only(catalog, candidate) and \
                self.sections[i + 1] == self.sections[i]:
            reasons.append("dips are kept for the end of a script section")
        if inside and is_dip(catalog, candidate):
            same_section = [
                j for j in range(count - 1)
                if self.sections[j] == self.sections[i] and is_dip(catalog, self._other(i, j))
            ]
            if same_section:
                reasons.append("a script section gets at most one dip")
        return reasons

    def usage(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for j, name in enumerate(self.ids):
            if name and self.decided[j]:
                counts[name] = counts.get(name, 0) + 1
        return counts


def _choose_transition(
    cuts: _Cuts, i: int, rules: Rules, *, original: str | None, beat: str | None,
    boundary: bool, may_keep: bool,
) -> str:
    """The best transition for cut ``i`` from the channel's pack.

    Fits every rule if anything can; prefers the story beat of the draft (or of the
    transition it replaces, or "section" at a section boundary), then the same energy, then
    whatever is used least so far. Dips are kept for section boundaries. ``may_keep`` lets
    the original stay when nothing fits the strict rules but it fits the basic ones.
    """
    catalog = rules.catalog
    pool = catalog.for_pack(rules.pack) or list(catalog.transitions)
    replaced = catalog.get(original)
    want_energy = replaced.energy if replaced is not None else "calm"
    want_beat = beat or (replaced.beats[0] if replaced is not None and replaced.beats else None)
    if want_beat is None and boundary:
        want_beat = "section"
    usage = cuts.usage()
    order = {t.type: k for k, t in enumerate(catalog.transitions)}

    def rank(t: Any) -> tuple[int, int, int, int, int]:
        dip_mid_section = 1 if ("section" in t.beats and not boundary) else 0
        beat_miss = 0 if want_beat is None or want_beat in t.beats else 1
        energy_miss = 0 if t.energy == want_energy else (1 if t.energy != "high" else 2)
        return (dip_mid_section, beat_miss, energy_miss, usage.get(t.type, 0), order[t.type])

    for strict in (True, False):
        if not strict and may_keep and original and not cuts.problems(i, original, strict=False):
            return original
        options = [t for t in pool if t.type != original
                   and not cuts.problems(i, t.type, strict=strict)]
        if options:
            return min(options, key=rank).type
    options = [t for t in catalog.transitions if not cuts.problems(i, t.type, strict=False)]
    return min(options, key=rank).type if options else catalog.default


def _set_transition(scene: Scene, name: str, rules: Rules) -> None:
    scene.transition_out = SceneTransition(
        type=name,
        duration_s=rules.catalog.duration_for(name, rules.fmt, rules.pack),
        beat=scene.transition_out.beat,
    )


def fix_transitions(
    scenes: list[Scene], rules: Rules, warnings: list[str],
    sentences: list[SentenceInfo] | None = None,
) -> None:
    """The transition rules of docs/TRANSITIONS-V2-CONTRACT.md section 4, in place.

    1. Old xfade names become their v2 ids; unknown names (and, for the AI's draft, ids
       outside the channel's pack) are replaced from the pack.
    2. The last scene ends with the pack's closer (``dip_black``).
    3. Left to right, every unlocked cut must differ from its neighbours, never be the second
       high-energy cut in a row, appear at most twice in any ten cuts, keep light leaks and
       film burns a minute apart and give each script section at most one dip.
    4. A cut the reviewer just picked wins over unchanged neighbours (step 3 works around
       it) and only changes when it clashes with another hand-picked or locked cut.

    Locked cuts never change; clashes between them are reported in ``warnings``.
    """
    count = len(scenes)
    if count == 0:
        return
    catalog = rules.catalog
    closer = catalog.closer_for(rules.pack)
    sections = scene_sections(scenes, sentences)
    pinned = [bool(s.sentence_ids) and s.sentence_ids[0] in rules.pinned_transitions
              for s in scenes]
    cuts = _Cuts(catalog, ids=[None] * count, times=[s.est_end_s for s in scenes],
                 sections=sections, decided=[False] * count)
    replace_reason: dict[int, str] = {}

    # 1. Names.
    for i, scene in enumerate(scenes):
        current = scene.transition_out
        if scene.locked.transition_out:
            cuts.ids[i] = catalog.resolve(current.type) or current.type
            cuts.decided[i] = True
            continue
        if i == count - 1:
            continue
        resolved = catalog.resolve(current.type)
        if resolved is None:
            replace_reason[i] = f"Transition '{current.type}' is not in the list"
            continue
        if resolved != current.type:
            scene.notes.append(
                f"Transition '{current.type}' is an old name; it is now "
                f"{transition_label(catalog, resolved)}."
            )
            _set_transition(scene, resolved, rules)
        elif current.duration_s <= 0:
            current.duration_s = catalog.duration_for(resolved, rules.fmt, rules.pack)
        cuts.ids[i] = resolved
        entry = catalog.get(resolved)
        if pinned[i]:
            cuts.decided[i] = True
        elif rules.enforce_pack and entry is not None and not entry.in_pack(rules.pack):
            pack_label = PACK_LABELS.get(rules.pack, rules.pack)
            replace_reason[i] = f"{entry.label} is not in the channel's {pack_label} pack"

    # 2. The closer (unless the reviewer just picked another ending by hand).
    last = scenes[-1]
    picked_ending = catalog.resolve(last.transition_out.type) if pinned[-1] else None
    if not last.locked.transition_out and picked_ending:
        if picked_ending != last.transition_out.type:
            _set_transition(last, picked_ending, rules)
        cuts.ids[-1] = picked_ending
        cuts.decided[-1] = True
    elif not last.locked.transition_out:
        if last.transition_out.type != closer or last.transition_out.duration_s <= 0:
            last.transition_out = SceneTransition(
                type=closer, duration_s=catalog.duration_for(closer, rules.fmt, rules.pack),
                beat=last.transition_out.beat or "section",
            )
        cuts.ids[-1] = closer
        cuts.decided[-1] = True

    # 3. The AI's (and unchanged) cuts: section ends first (their dips win), then the rest,
    #    each left to right.
    def is_boundary(i: int) -> bool:
        return bool(sections[i]) and sections[i + 1] != sections[i]

    order = [i for i in range(count - 1) if is_boundary(i)] + \
        [i for i in range(count - 1) if not is_boundary(i)]
    for i in order:
        if cuts.decided[i]:
            continue
        scene = scenes[i]
        current_id = cuts.ids[i]
        boundary = is_boundary(i)
        if i in replace_reason:
            new = _choose_transition(cuts, i, rules, original=current_id,
                                     beat=scene.transition_out.beat, boundary=boundary,
                                     may_keep=False)
            reason = replace_reason[i]
            if reason.startswith("Transition '"):
                scene.notes.append(f"{reason}; replaced with {transition_label(catalog, new)}.")
            else:
                scene.notes.append(f"Transition changed to {transition_label(catalog, new)} "
                                   f"by the app ({reason}).")
            _set_transition(scene, new, rules)
            cuts.ids[i] = new
        elif current_id is not None:
            problems = cuts.problems(i, current_id)
            if problems:
                new = _choose_transition(cuts, i, rules, original=current_id,
                                         beat=scene.transition_out.beat, boundary=boundary,
                                         may_keep=True)
                if new != current_id:
                    scene.notes.append(f"Transition changed to {transition_label(catalog, new)} "
                                       f"by the app ({problems[0]}).")
                    _set_transition(scene, new, rules)
                    cuts.ids[i] = new
        cuts.decided[i] = True

    # 4. Hand-picked cuts that clash with each other or with a locked cut: left to right, so
    #    of two clashing picks the later one gives way.
    hand_picked = [i for i in range(count - 1) if pinned[i] and not scenes[i].locked.transition_out
                   and cuts.ids[i] is not None]
    for i in hand_picked:
        cuts.decided[i] = False
    for i in hand_picked:
        problems = cuts.problems(i, cuts.ids[i] or "", strict=False)
        if problems:
            new = _choose_transition(cuts, i, rules, original=cuts.ids[i],
                                     beat=scenes[i].transition_out.beat,
                                     boundary=is_boundary(i), may_keep=False)
            scenes[i].notes.append(f"Transition changed to {transition_label(catalog, new)} by "
                                   f"the app ({problems[0]}).")
            _set_transition(scenes[i], new, rules)
            cuts.ids[i] = new
        cuts.decided[i] = True

    # 5. What could not be fixed.
    for i in range(1, count):
        before, after = cuts.ids[i - 1], cuts.ids[i]
        if before is not None and before == after:
            warnings.append(
                f"Scenes {i} and {i + 1} both end with {transition_label(catalog, after)} "
                "and are locked."
            )
        elif (before is not None and after is not None
              and catalog.get(before) is not None and catalog.get(after) is not None
              and catalog.energy_of(before) == "high" and catalog.energy_of(after) == "high"):
            warnings.append(
                f"Scenes {i} and {i + 1} both end with a high-energy transition and are locked."
            )


def _popup_offsets(scene: Scene) -> tuple[float, float]:
    duration = scene.est_duration_s or 1.0
    start = min(POPUP_IN_S, max(0.0, duration / 2))
    end = max(start + 0.5, min(duration - 0.2, start + POPUP_MAX_SHOW_S))
    return round(start, 2), round(end, 2)


def fix_popups(
    scenes: list[Scene], sentences: list[SentenceInfo], rules: Rules, warnings: list[str]
) -> None:
    """At most six words, never the narration itself, the channel's style, enough text."""
    by_id = {s.id: s for s in sentences}
    for scene in scenes:
        if scene.locked.popup:
            continue
        text = " ".join((scene.popup.text or "").split())
        if text:
            sentence_texts = {normalise(by_id[i].text) for i in scene.sentence_ids if i in by_id}
            if normalise(text) == normalise(scene.narration) or normalise(text) in sentence_texts:
                text = keyword_phrase(scene.narration)
                scene.notes.append("Popup replaced by the app: it repeated the narration.")
            words = text.split()
            if len(words) > rules.max_popup_words:
                text = " ".join(words[: rules.max_popup_words]).rstrip(",;:")
                scene.notes.append(f"Popup shortened to {rules.max_popup_words} words by the app.")
        scene.popup.text = text or None
        scene.popup.style = scene.popup.style or rules.popup_style
        scene.popup.in_offset_s, scene.popup.out_offset_s = _popup_offsets(scene)
        if scene.on_screen_text is not None and not scene.on_screen_text.strip():
            scene.on_screen_text = None
    if not scenes:
        return
    need = math.ceil(rules.min_text_share * len(scenes) - 1e-9)
    have = sum(1 for s in scenes if s.has_text)
    if have >= need:
        return
    candidates = [i for i, s in enumerate(scenes) if not s.has_text and not s.locked.popup]
    missing = need - have
    if len(candidates) < missing:
        warnings.append(
            f"Only {have + len(candidates)} of {len(scenes)} scenes can carry text; "
            f"{need} are needed for {rules.min_text_share:.0%}."
        )
    if not candidates:
        return
    step = len(candidates) / max(1, missing)
    chosen = sorted({candidates[min(len(candidates) - 1, int(k * step))] for k in range(missing)})
    extra = [i for i in candidates if i not in chosen]
    while len(chosen) < min(missing, len(candidates)) and extra:
        chosen.append(extra.pop(0))
    for n, i in enumerate(sorted(chosen)):
        scene = scenes[i]
        phrase = keyword_phrase(scene.narration)
        if not phrase:
            continue
        scene.popup.text = phrase
        scene.popup.position = POPUP_POSITIONS[(i + n) % len(POPUP_POSITIONS)]
        scene.popup.style = scene.popup.style or rules.popup_style
        scene.popup.in_offset_s, scene.popup.out_offset_s = _popup_offsets(scene)
        scene.notes.append("Popup added by the app so enough scenes carry text.")


def merge_negatives(*parts: str) -> str:
    seen: list[str] = []
    for part in parts:
        for item in (part or "").replace(";", ",").split(","):
            clean = " ".join(item.split()).strip(" .")
            if clean and clean.lower() not in (s.lower() for s in seen):
                seen.append(clean)
    return ", ".join(seen)


def strip_prompt_wrapping(prompt: str, style_guide: str) -> str:
    """The bare picture description: without the style prefix and the 'Avoid:' suffix."""
    body = " ".join((prompt or "").split())
    guide = " ".join((style_guide or "").split()).rstrip(".")
    if guide and body.lower().startswith(guide.lower()):
        body = body[len(guide):].lstrip(" .,;:")
    if AVOID_MARKER.strip() in body:
        head, _, _ = body.partition(AVOID_MARKER.strip())
        body = head.rstrip()
    return body.strip().rstrip(".").strip()


def fix_prompts(scenes: list[Scene], rules: Rules) -> None:
    """Style guide in front, negative rules at the back, nothing applied twice."""
    guide = " ".join(rules.style_guide.split()).rstrip(".")
    for scene in scenes:
        negative = merge_negatives(scene.negative_prompt, rules.negative_rules, DEFAULT_NEGATIVE)
        scene.negative_prompt = negative
        if scene.locked.image_prompt:
            continue
        body = strip_prompt_wrapping(scene.image_prompt, rules.style_guide)
        if not body:
            body = prompt_from_narration(scene.narration)
        body = body.rstrip(".")
        prefix = f"{guide}. " if guide else ""
        scene.image_prompt = f"{prefix}{body}.{AVOID_MARKER}{negative}."


def variety_of(scenes: list[Scene], rules: Rules, warnings: list[str]) -> StoryboardVariety:
    if not scenes:
        return StoryboardVariety(warnings=warnings + ["The storyboard has no scenes."])
    out_of_band = [s.index + 1 for s in scenes
                   if not (rules.min_s - 1e-9 <= s.est_duration_s <= rules.max_s + 1e-9)]
    if out_of_band:
        warnings.append(
            f"{len(out_of_band)} scene(s) outside {rules.min_s:g}-{rules.max_s:g} s: "
            + ", ".join(f"#{i}" for i in out_of_band[:12])
            + (" ..." if len(out_of_band) > 12 else "")
        )
    prompts = [normalise(strip_prompt_wrapping(s.image_prompt, rules.style_guide)) for s in scenes]
    repeated = [
        i + 1 for i in range(1, len(prompts)) if prompts[i] and prompts[i] == prompts[i - 1]
    ]
    if repeated:
        warnings.append(
            "Same picture as the previous scene: " + ", ".join(f"#{i}" for i in repeated)
        )
    histogram = transition_histogram(scenes, rules.catalog)
    return StoryboardVariety(
        scenes=len(scenes),
        avg_scene_s=round(sum(s.est_duration_s for s in scenes) / len(scenes), 2),
        popup_share=round(sum(1 for s in scenes if s.has_text) / len(scenes), 3),
        distinct_transitions=len(histogram),
        distinct_motions=len({s.motion.preset for s in scenes}),
        warnings=warnings,
        transition_histogram=histogram,
    )


def transition_histogram(scenes: list[Scene], catalog: TransitionCatalog) -> dict[str, int]:
    """``{transition id: uses}``, most used first (old names counted as their v2 id)."""
    counts: dict[str, int] = {}
    for scene in scenes:
        name = catalog.resolve(scene.transition_out.type) or scene.transition_out.type
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _linearise(scenes: list[Scene], sentences: list[SentenceInfo], rules: Rules) -> list[Scene]:
    """Walk the script in order; every scene becomes one contiguous run of sentences.

    A scene whose sentences were out of order is reordered; one whose sentences were
    interleaved with another scene's is split, the later run becoming a new scene.
    """
    owner: dict[str, int] = {}
    for index, scene in enumerate(scenes):
        for sentence_id in scene.sentence_ids:
            owner.setdefault(sentence_id, index)
    result: list[Scene] = []
    placed: set[int] = set()
    current: int | None = None
    for sentence in sentences:
        index = owner.get(sentence.id)
        if index is None:
            current = None
            continue
        if result and index == current:
            result[-1].sentence_ids.append(sentence.id)
            continue
        if index in placed:
            result.append(_new_scene(
                [sentence], len(result), rules,
                note="Split by the app: the draft's scene was not one continuous run.",
                like=scenes[index],
            ))
        else:
            scene = scenes[index]
            scene.sentence_ids = [sentence.id]
            result.append(scene)
            placed.add(index)
        current = index
    return result


def locked_scene_lines(previous: StoryboardDoc | None) -> list[str]:
    """The locked fields of the previous storyboard, one plain line per scene, for the prompt."""
    lines: list[str] = []
    for scene in (previous.scenes if previous else []):
        locks = scene.locked
        if not locks.any:
            continue
        kept: list[str] = []
        if locks.narration:
            kept.append("sentence group (keep these sentences together in one scene)")
        if locks.image_prompt:
            kept.append(f"image prompt: {strip_prompt_wrapping(scene.image_prompt, '')}")
        if locks.popup:
            kept.append(f"popup: {scene.popup.text or '(none)'} ({scene.popup.position})")
        if locks.motion:
            kept.append(f"motion: {scene.motion.preset}")
        if locks.transition_out:
            kept.append(f"transition: {scene.transition_out.type}")
        lines.append(f"- sentences {', '.join(scene.sentence_ids)}: locked " + "; ".join(kept))
    return lines


def carry_over_locks(scenes: list[Scene], previous: StoryboardDoc | None) -> list[Scene]:
    """Copy every locked field of the previous storyboard onto the new draft's scenes.

    A new scene inherits from the previous scene it shares the most sentences with. A locked
    narration also pins the sentence grouping: the new scene takes exactly those sentences
    and the other scenes give them up (``fix_up`` then re-homes anything left over).
    """
    if previous is None:
        return scenes
    locked_prev = [s for s in previous.scenes if s.locked.any]
    if not locked_prev or not scenes:
        return scenes
    taken: set[int] = set()
    for scene in scenes:
        best_i, best_overlap = -1, 0
        ids = set(scene.sentence_ids)
        for i, prev in enumerate(locked_prev):
            if i in taken:
                continue
            overlap = len(ids & set(prev.sentence_ids))
            if overlap > best_overlap:
                best_i, best_overlap = i, overlap
        if best_i < 0:
            continue
        taken.add(best_i)
        prev = locked_prev[best_i]
        kept: list[str] = []
        if prev.locked.image_prompt:
            scene.image_prompt = prev.image_prompt
            scene.negative_prompt = prev.negative_prompt
            scene.locked.image_prompt = True
            kept.append("image prompt")
        if prev.locked.popup:
            scene.popup = prev.popup.model_copy(deep=True)
            scene.on_screen_text = prev.on_screen_text
            scene.locked.popup = True
            kept.append("popup")
        if prev.locked.motion:
            scene.motion = prev.motion.model_copy(deep=True)
            scene.locked.motion = True
            kept.append("motion")
        if prev.locked.transition_out:
            scene.transition_out = prev.transition_out.model_copy(deep=True)
            scene.locked.transition_out = True
            kept.append("transition")
        if prev.locked.narration:
            scene.sentence_ids = list(prev.sentence_ids)
            scene.narration = prev.narration
            scene.locked.narration = True
            kept.append("narration")
        if prev.image.path:
            scene.image = prev.image.model_copy(deep=True)
        scene.notes.append("Kept from the previous storyboard (locked): " + ", ".join(kept) + ".")
    claimed = {i for s in scenes if s.locked.narration for i in s.sentence_ids}
    for scene in scenes:
        if not scene.locked.narration:
            scene.sentence_ids = [i for i in scene.sentence_ids if i not in claimed]
    return [s for s in scenes if s.sentence_ids]


def fix_up(doc: StoryboardDoc, sentences: list[SentenceInfo], rules: Rules) -> StoryboardDoc:
    """Run every rule on the document; locked fields are left alone. Returns ``doc``."""
    warnings: list[str] = []
    known = {s.id for s in sentences}
    scenes = [s for s in doc.scenes if any(i in known for i in s.sentence_ids)]
    for scene in scenes:
        scene.sentence_ids = [i for i in scene.sentence_ids if i in known]
    used: set[str] = set()
    for scene in scenes:
        scene.sentence_ids = [i for i in scene.sentence_ids if not (i in used or used.add(i))]
    scenes = _linearise([s for s in scenes if s.sentence_ids], sentences, rules)
    missing = [s for s in sentences if s.id not in used]
    scenes = _insert_missing(scenes, missing, sentences, rules)
    by_id = {s.id: s for s in sentences}
    for scene in scenes:
        scene.narration = " ".join(by_id[i].text for i in scene.sentence_ids)
    scenes = fix_scene_lengths(scenes, sentences, rules)
    fix_timings(scenes, sentences)
    fix_motion(scenes, warnings)
    fix_transitions(scenes, rules, warnings, sentences)
    fix_popups(scenes, sentences, rules, warnings)
    fix_prompts(scenes, rules)
    fix_timings(scenes, sentences)
    doc.scenes = scenes
    doc.variety = variety_of(scenes, rules, warnings)
    return doc


def gate_context(doc: StoryboardDoc, catalog: TransitionCatalog) -> dict[str, Any]:
    """The storyboard's measurements for the policy gates. Transitions are judged by their
    v2 id, so an old name next to its new equivalent counts as a repeat."""
    return {
        "format": doc.format,
        "known_transitions": catalog.known_names,
        "transition_histogram": transition_histogram(doc.scenes, catalog),
        "scenes": [
            {
                "est_duration_s": s.est_duration_s,
                "motion_preset": s.motion.preset,
                "transition_type": catalog.resolve(s.transition_out.type) or s.transition_out.type,
                "popup_text": s.popup.text,
                "on_screen_text": s.on_screen_text,
                "narration": s.narration,
                "image_prompt": strip_prompt_wrapping(s.image_prompt, doc.style_guide),
            }
            for s in doc.scenes
        ],
    }


# Files -------------------------------------------------------------------------------------------


def read_storyboard(stage_dir: Path) -> StoryboardDoc | None:
    path = stage_dir / STORYBOARD_FILE
    if not path.is_file():
        return None
    try:
        return StoryboardDoc.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise StageError(f"The file {path} could not be read: {exc}") from exc


def write_storyboard(stage_dir: Path, doc: StoryboardDoc) -> Path:
    path = stage_dir / STORYBOARD_FILE
    atomic_write_text(path, doc.model_dump_json(indent=2) + "\n")
    return path


def transition_options(rules: Rules) -> list[dict[str, Any]]:
    """Every transition for the pickers, the channel's pack first, with its default length
    for this video (pack range and Shorts factor applied) and its preview URLs."""
    catalog = rules.catalog
    ordered = sorted(catalog.transitions, key=lambda t: 0 if t.in_pack(rules.pack) else 1)
    return [
        {
            "type": t.type,
            "id": t.type,
            "label": t.label,
            "duration_s": catalog.duration_for(t.type, rules.fmt, rules.pack),
            "energy": t.energy,
            "packs": list(t.packs),
            "in_pack": t.in_pack(rules.pack),
            "feel": t.feel,
            "beats": list(t.beats),
            "preview_url": preview_url(t.type),
            "poster_url": poster_url(t.type),
        }
        for t in ordered
    ]


def review_payload(
    doc: StoryboardDoc, rules: Rules, channel_motion_style: str | None = None
) -> dict[str, Any]:
    return StoryboardReviewPayload(
        storyboard=doc,
        transitions=transition_options(rules),
        scene_band_s=[rules.min_s, rules.max_s],
        transition_pack=rules.pack,
        channel_motion_style=channel_motion_style,
    ).model_dump(mode="json")


def rules_for(ctx: StageContext) -> Rules:
    fmt = ctx.project.format
    min_s, max_s = gates.scene_band(fmt)
    images = ctx.channel.images
    catalog = transition_catalog()
    return Rules(
        min_s=min_s,
        max_s=max_s,
        fmt=fmt,
        catalog=catalog,
        style_guide=" ".join((images.style_guide or DEFAULT_STYLE_GUIDE).split()),
        negative_rules=images.negative_rules or "",
        popup_style=images.popup_style or "Rounded box, brand colour, bold sans-serif",
        max_popup_words=int(
            gates.params("storyboard.popup_words").get("max_words", MAX_POPUP_WORDS)
        ),
        min_text_share=float(gates.params("storyboard.popup_share").get("min_share", 0.35)),
        pack=catalog.pack_or_default(getattr(images, "transition_pack", None)),
    )


def channel_motion_style(ctx: StageContext) -> str | None:
    return getattr(ctx.channel.images, "motion_style", None)


def reviewer_picks(doc: StoryboardDoc, previous: StoryboardDoc | None) -> frozenset[str]:
    """First sentence ids of the scenes whose transition differs from the saved storyboard:
    the reviewer picked those by hand, so the fix-ups change their neighbours instead."""
    if previous is None:
        return frozenset()
    before = {s.sentence_ids[0]: s.transition_out.type for s in previous.scenes if s.sentence_ids}
    return frozenset(
        scene.sentence_ids[0] for scene in doc.scenes
        if scene.sentence_ids and scene.sentence_ids[0] in before
        and before[scene.sentence_ids[0]] != scene.transition_out.type
    )


# The prompt ------------------------------------------------------------------------------------


def transition_prompt_lines(rules: Rules) -> list[str]:
    """``id | name | energy | default length | feel`` for every transition of the pack."""
    catalog = rules.catalog
    lines: list[str] = []
    for t in catalog.for_pack(rules.pack):
        suits = ", ".join(BEAT_LABELS[b] for b in t.beats if b in BEAT_LABELS)
        extra = f" Suits {suits}." if suits else ""
        if t.min_gap_s >= 60:
            extra += " At most once a minute."
        elif t.min_gap_s > 0:
            extra += f" At most once every {t.min_gap_s:g} s."
        lines.append(
            f"{t.type} | {t.label} | {ENERGY_LABELS.get(t.energy, t.energy).lower()} | "
            f"{catalog.duration_for(t.type, rules.fmt, rules.pack):g} s | {t.feel}{extra}"
        )
    return lines


def pack_description(rules: Rules) -> str:
    info = rules.catalog.pack_info(rules.pack)
    label = PACK_LABELS.get(rules.pack, rules.pack)
    return f"{label}. {info.description}" if info and info.description else label


def sentence_lines(sentences: list[SentenceInfo], names: dict[str, str]) -> list[str]:
    """The sentences for the prompt, with a header line where each script section starts."""
    lines: list[str] = []
    current: str | None = None
    number = 0
    for sentence in sentences:
        if sentence.section and sentence.section != current:
            number += 1
            current = sentence.section
            name = names.get(sentence.section, "").strip()
            lines.append(f"[Section {number}{': ' + name if name else ''}]")
        lines.append(f"{sentence.id} ({sentence.est_s:g} s): {sentence.text}")
    return lines


def aspect_for(ctx: StageContext) -> str:
    images = ctx.channel.images
    aspect = images.aspect_shorts if ctx.project.format == "shorts" else images.aspect_long
    if aspect not in ("16:9", "9:16"):
        aspect = "9:16" if ctx.project.format == "shorts" else "16:9"
    return aspect


def load_sentences(ctx: StageContext) -> tuple[Any, list[SentenceInfo], int]:
    script_dir = ctx.folder / "03_script"
    script = read_script_doc(script_dir)
    if script is None:
        raise StageError("The script step has not produced a script yet. Finish it first.")
    speech = read_model(script_dir / "speech.json", SpeechDoc)
    override = getattr(getattr(ctx.settings, "voice", None), "speaking_rate_wpm", None)
    wpm = script.speaking_rate_wpm or speaking_rate_wpm(
        ctx.project.language, int(override) if isinstance(override, int | float) else None
    )
    sentences = sentence_infos(script, speech, wpm)
    if not sentences:
        raise StageError("The script has no sentences to storyboard.")
    return script, sentences, wpm


def _llm(ctx: StageContext) -> Any:
    client = ctx.providers.get("llm")
    if client is None:
        raise StageError("No writing model is set up. Check Settings > Models and providers.")
    return client


# The stage ---------------------------------------------------------------------------------


class StoryboardStage:
    name = StageName.storyboard

    async def run(self, ctx: StageContext) -> StageResult:
        llm = _llm(ctx)
        project = ctx.project
        stage_dir = ctx.stage_dir(StageName.storyboard)
        await ctx.report("Reading the script", 5)
        script, sentences, wpm = load_sentences(ctx)
        rules = rules_for(ctx)
        aspect = aspect_for(ctx)
        prompt = load_prompt(TASK)
        notes = [n for n in ctx.notes if n.strip()]
        previous = read_storyboard(stage_dir)
        locked_lines = locked_scene_lines(previous)
        variables: dict[str, Any] = {
            "format": "Shorts (vertical)" if project.format == "shorts" else "long-form",
            "aspect": aspect,
            "min_s": rules.min_s,
            "max_s": rules.max_s,
            "style_guide": rules.style_guide,
            "negative_rules": rules.negative_rules or "(none beyond the defaults)",
            "popup_style": rules.popup_style,
            "transition_pack": pack_description(rules),
            "transition_options": transition_prompt_lines(rules),
            "transition_closer": (
                f"{rules.catalog.closer_for(rules.pack)} "
                f"({transition_label(rules.catalog, rules.catalog.closer_for(rules.pack))})"
            ),
            "transition_types": ", ".join(t.type for t in rules.catalog.for_pack(rules.pack)),
            "motion_presets": ", ".join(MOTION_PRESETS),
            "popup_positions": ", ".join(POPUP_POSITIONS),
            "sentences": sentence_lines(sentences, section_names(script)),
            "locked_scenes": locked_lines or ["(none)"],
            "notes": notes or ["(none)"],
        }
        mock_variables = {
            **variables,
            "sentences": [{"id": s.id, "text": s.text, "est_s": s.est_s} for s in sentences],
            "transition_types": [t.type for t in rules.catalog.for_pack(rules.pack)],
            "motion_presets": list(MOTION_PRESETS),
            "popup_positions": list(POPUP_POSITIONS),
        }
        await ctx.report(f"Drafting scenes for {len(sentences)} sentences", 20)
        draft, usage = await llm.complete(
            TASK, [f"Image style guide for this channel: {rules.style_guide}",
                   prompt.render("system", variables)],
            prompt.render("user", variables), StoryboardDraft, variables=mock_variables,
            project_id=project.id, stage=StageName.storyboard.value,
        )
        await ctx.report("Applying the scene rules", 70)
        doc = StoryboardDoc(
            project_id=project.id,
            format=project.format,
            aspect=aspect,  # type: ignore[arg-type]
            style_guide=rules.style_guide,
            negative_rules=rules.negative_rules,
            popup_style=rules.popup_style,
            transition_pack=rules.pack,  # type: ignore[arg-type]
            motion_style=previous.motion_style if previous else None,
            generated_at=datetime.now(UTC),
            model=usage.model,
            speaking_rate_wpm=wpm,
            scenes=carry_over_locks(scenes_from_draft(draft, sentences, rules), previous),
            notes=notes,
        )
        fix_up(doc, sentences, rules)
        results = gates.evaluate("storyboard", gate_context(doc, rules.catalog))
        doc.gate_results = gates.to_dicts(results)
        path = write_storyboard(stage_dir, doc)
        blocking = gates.blocking_reasons(results)
        if blocking:
            raise GateBlocked(blocking, cost_usd=round(usage.cost_usd, 6))
        await ctx.report("Storyboard ready for review", 100)
        return StageResult(
            outputs=[path],
            summary=f"{len(doc.scenes)} scenes, {doc.total_s:g} s estimated, "
            f"{doc.variety.popup_share:.0%} with text, "
            f"{doc.variety.distinct_transitions} transitions.",
            cost_usd=round(usage.cost_usd, 6),
            needs_review_payload=review_payload(doc, rules, channel_motion_style(ctx)),
            gate_results=doc.gate_results,
        )

    async def apply_edits(self, ctx: StageContext) -> StageResult | None:
        """Approval edits ``{storyboard}``: replace the document and re-run the rules."""
        if not ctx.edits or "storyboard" not in ctx.edits:
            return None
        try:
            edits = StoryboardApproveEdits.model_validate(ctx.edits)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first.get("loc", ()))
            raise StageError(
                f"The storyboard edits are not valid ({where}): {first['msg']}"
            ) from exc
        stage_dir = ctx.stage_dir(StageName.storyboard)
        _script, sentences, wpm = load_sentences(ctx)
        rules = rules_for(ctx)
        doc = edits.storyboard
        doc.project_id = ctx.project.id
        doc.format = ctx.project.format
        doc.speaking_rate_wpm = wpm
        doc.style_guide = doc.style_guide or rules.style_guide
        rules.style_guide = doc.style_guide
        if not doc.scenes:
            raise StageError("The storyboard must keep at least one scene.")
        # The reviewer may pick any transition (the picker shows every pack); the ones they
        # changed by hand win over their unchanged neighbours.
        try:
            saved = read_storyboard(stage_dir)
        except StageError:
            saved = None
        rules.enforce_pack = False
        rules.pinned_transitions = reviewer_picks(doc, saved)
        doc.transition_pack = rules.pack  # type: ignore[assignment]
        fix_up(doc, sentences, rules)
        results = gates.evaluate("storyboard", gate_context(doc, rules.catalog))
        doc.gate_results = gates.to_dicts(results)
        path = write_storyboard(stage_dir, doc)
        return StageResult(
            outputs=[path],
            summary=f"Storyboard edited by the reviewer: {len(doc.scenes)} scenes, "
            f"{doc.total_s:g} s estimated.",
            needs_review_payload=review_payload(doc, rules, channel_motion_style(ctx)),
            gate_results=doc.gate_results,
        )


def load_review_payload(folder: Path, fmt: str = "long") -> dict[str, Any]:
    """The review payload rebuilt from the files (for a server restart)."""
    doc = read_storyboard(folder / "04_storyboard")
    if doc is None:
        return {}
    min_s, max_s = gates.scene_band(doc.format)
    catalog = transition_catalog()
    rules = Rules(
        min_s=min_s, max_s=max_s, fmt=doc.format, catalog=catalog,
        style_guide=doc.style_guide, negative_rules=doc.negative_rules, popup_style=doc.popup_style,
        pack=catalog.pack_or_default(doc.transition_pack),
    )
    return review_payload(doc, rules)
