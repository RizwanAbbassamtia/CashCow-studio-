"""Transitions v2 render pipeline (docs/TRANSITIONS-V2-CONTRACT.md section 3): the frame
layout of scene clips and cuts, the clip hashes and their reuse, a real render of a tiny
project at 480p with zoom_through, light_leak_warm and whip_left (frame count and length
from ffprobe, frames at every cut midpoint looked at), cancellation that keeps finished
clips, and the frame-provider hook the motion module plugs into. Media stays tiny."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from cashcow_studio.models.timeline import Timeline
from cashcow_studio.render import assembly
from cashcow_studio.render import ffmpeg as ff
from cashcow_studio.render import scene_clips as sc
from cashcow_studio.render.presets import load_render_config
from test_render_fixtures import (
    TMP_ROOT,
    make_render_tmp_fixture,
    requires_ffmpeg,
    write_project,
)

render_tmp = make_render_tmp_fixture()
FPS = 30
ZOOM_IN = {"preset": "zoom_in", "start_rect": [0, 0, 1024, 576],
           "end_rect": [102.4, 57.6, 819.2, 460.8]}
PAN_RIGHT = {"preset": "pan_right", "start_rect": [0, 0, 870.4, 489.6],
             "end_rect": [153.6, 0, 870.4, 489.6]}
HOLD = {"preset": "hold", "start_rect": [0, 0, 1024, 576], "end_rect": [0, 0, 1024, 576]}


def make_timeline(
    lengths: list[float],
    transitions: list[tuple[str, float]],
    *,
    images: list[str] | None = None,
    motions: list[dict[str, Any]] | None = None,
    gap_after: dict[int, float] | None = None,
) -> Timeline:
    scenes = []
    clock = 0.0
    for index, length in enumerate(lengths):
        start = clock
        end = clock + length
        image = images[index] if images else f"06_images/scene_{index % 3 + 1:02d}.png"
        motion = motions[index] if motions else (ZOOM_IN if index % 2 == 0 else PAN_RIGHT)
        kind, seconds = transitions[index]
        scenes.append({
            "index": index,
            "image": image,
            "start_s": round(start, 3),
            "end_s": round(end, 3),
            "motion": motion,
            "transition_out": {"type": kind, "duration_s": seconds},
        })
        clock = end + (gap_after or {}).get(index, 0.0)
    return Timeline.model_validate({
        "project_id": "p-assembly", "format": "long", "aspect": "16:9", "width": 854,
        "height": 480, "fps": FPS, "duration_s": round(scenes[-1]["end_s"], 3), "scenes": scenes,
    })


def layout_for(files: Any, timeline: Timeline) -> assembly.Layout:
    config = load_render_config()
    return assembly.plan_layout(timeline, config.proxy, folder=files.folder, config=config)


# Frame layout (no FFmpeg) ----------------------------------------------------------------------


def test_cuts_are_centred_on_the_scene_boundaries(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    timeline = make_timeline(
        [2.0, 1.5, 2.5], [("zoom_through", 0.4), ("whip_left", 0.3), ("dip_black", 0.5)]
    )
    layout = layout_for(files, timeline)
    a, b, c = layout.scenes
    # 60 / 45 / 75 frames; cut 1 = 12 frames (6 before the boundary, 6 after), cut 2 = 9
    # (4 before, 5 after), the end fade = 15 frames inside scene 3.
    assert [cut.frames for cut in layout.cuts] == [12, 9, 15]
    assert [cut.label for cut in layout.cuts] == ["zoom_through", "whip_left", "fade to black"]
    assert (a.request.lead_in, a.request.tail_out, a.frames) == (0, 6, 66)
    assert (b.request.lead_in, b.request.tail_out, b.frames) == (6, 5, 56)
    assert (c.request.lead_in, c.request.tail_out, c.frames) == (4, 0, 79)
    # Keyframes where each body starts and where the outgoing overlap starts.
    assert a.keyframes == (54,) and b.keyframes == (12, 47) and c.keyframes == (9, 64)
    # Bodies: 60 - 6 = 54, 45 - 6 - 4 = 35, 75 - 5 - 15 = 55; the cuts fill the rest.
    assert layout.scene_frames == {1: (0, 54), 2: (66, 35), 3: (110, 55)}
    assert layout.total_frames == 180 == round(timeline.duration_s * FPS)
    # Joined: clip 1 = 0-65, cut 1 = 66-77, clip 2 = 78-133, cut 2 = 134-142, clip 3 =
    # 143-221, fade = 222-236; the overlaps the cuts replace are dropped.
    assert layout.drops == [(54, 65), (78, 89), (125, 133), (143, 151), (207, 221)]
    assert layout.select_filter().startswith("select='not(between(n\\,54\\,65)+")
    assert layout.concat_text().splitlines() == [
        "ffconcat version 1.0",
        f"file '{a.file_name}'", f"file '{layout.cuts[0].file_name}'",
        f"file '{b.file_name}'", f"file '{layout.cuts[1].file_name}'",
        f"file '{c.file_name}'", f"file '{layout.cuts[2].file_name}'",
    ]
    # Frames in = frames out + the overlaps the cuts replace.
    joined = sum(job.frames for job in layout.scenes) + sum(cut.frames for cut in layout.cuts)
    assert joined - sum(hi - lo + 1 for lo, hi in layout.drops) == layout.total_frames


def test_short_and_missing_transitions_become_hard_cuts(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    # 0.03 s is one frame (below transitions.min_frames = 2); 0 s is a plain cut; the long
    # one is capped at half the shorter scene; the last scene's whip becomes the end fade.
    timeline = make_timeline(
        [1.0, 1.0, 0.4, 1.0],
        [("film_dissolve", 0.03), ("film_dissolve", 0.0), ("crosswarp", 2.0), ("whip_left", 0.2)],
    )
    layout = layout_for(files, timeline)
    assert [(cut.number, cut.label, cut.frames) for cut in layout.cuts] == [
        (3, "crosswarp", 6), (4, "fade to black", 6),
    ]
    assert layout.total_frames == 102
    timeline = make_timeline([1.0, 1.0], [("film_dissolve", 0.5), ("dip_white", 0.4)])
    assert layout_for(files, timeline).cuts[-1].label == "fade to white"
    assert layout_for(files, timeline).cuts[-1].fade_colour == (1.0, 1.0, 1.0)


def test_gaps_belong_to_the_scene_before_and_tiny_scenes_drop_out(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    timeline = make_timeline([1.0, 1.0, 1.0], [("film_dissolve", 0.2)] * 3,
                             gap_after={0: 0.5})
    layout = layout_for(files, timeline)
    assert layout.scene_frames[1][1] + layout.cuts[0].frames // 2 == 45  # 1.5 s before scene 2
    assert layout.total_frames == 105
    tiny = make_timeline([1.0, 0.01, 1.0], [("film_dissolve", 0.2)] * 3)
    layout = layout_for(files, tiny)
    assert [job.number for job in layout.scenes] == [1, 3]
    assert layout.total_frames == 60


def test_missing_pictures_are_named_plainly(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    timeline = make_timeline([1.0, 1.0], [("film_dissolve", 0.2)] * 2)
    timeline.scenes[1].image = "06_images/nope.png"
    with pytest.raises(ff.RenderError, match="picture for scene 2 is missing"):
        layout_for(files, timeline)


def test_clip_hashes_follow_exactly_what_each_clip_is_made_of(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    base = make_timeline([1.5, 1.5, 1.5], [("zoom_through", 0.4), ("whip_left", 0.3),
                                           ("dip_black", 0.5)])
    first = layout_for(files, base)
    assert [job.key for job in layout_for(files, base).scenes] == [j.key for j in first.scenes]
    assert len({job.key for job in first.scenes}) == 3 and len(first.scenes[0].key) == 16
    assert first.scenes[0].file_name == f"scene_01.{first.scenes[0].key}.mp4"
    assert first.cuts[0].file_name == f"cut_01.{first.cuts[0].key}.mp4"

    # Another transition type: the scenes stay, only that cut changes.
    other = base.model_copy(deep=True)
    other.scenes[0].transition_out.type = "light_leak_warm"
    changed = layout_for(files, other)
    assert [j.key for j in changed.scenes] == [j.key for j in first.scenes]
    assert [c.key == d.key for c, d in zip(changed.cuts, first.cuts, strict=True)] == [
        False, True, True]

    # Another move for scene 3: its clip, the cut into it and the end fade change.
    moved = base.model_copy(deep=True)
    moved.scenes[2].motion = moved.scenes[2].motion.model_validate(HOLD)
    changed = layout_for(files, moved)
    assert [c.key == d.key for c, d in zip(changed.scenes, first.scenes, strict=True)] == [
        True, True, False]
    assert [c.key == d.key for c, d in zip(changed.cuts, first.cuts, strict=True)] == [
        True, False, False]

    # A longer cut 1 changes both clips around it (their overlaps grow).
    longer = base.model_copy(deep=True)
    longer.scenes[0].transition_out.duration_s = 0.6
    changed = layout_for(files, longer)
    assert [c.key == d.key for c, d in zip(changed.scenes, first.scenes, strict=True)] == [
        False, False, True]

    # A scene that moves in time (a new scene inserted before it) keeps its clip as long as
    # its picture, move, length and transitions are the same.
    shifted = make_timeline([1.0, 1.5, 1.5, 1.5], [("film_dissolve", 0.2), ("zoom_through", 0.4),
                                                    ("whip_left", 0.3), ("dip_black", 0.5)],
                            images=["06_images/scene_03.png", "06_images/scene_01.png",
                                    "06_images/scene_02.png", "06_images/scene_03.png"],
                            motions=[HOLD, ZOOM_IN, PAN_RIGHT, ZOOM_IN])
    moved_on = layout_for(files, shifted)
    assert moved_on.scenes[2].key == first.scenes[1].key
    # So do the cuts between unchanged clips: the whip (now cut 3, was cut 2) and the end
    # fade. Only the glitch, the light leak and the film burn depend on the cut's number.
    assert moved_on.cuts[2].key == first.cuts[1].key
    assert moved_on.cuts[3].key == first.cuts[2].key
    glitchy = base.model_copy(deep=True)
    glitchy.scenes[1].transition_out.type = "glitch_subtle"
    glitchy_shifted = shifted.model_copy(deep=True)
    glitchy_shifted.scenes[2].transition_out.type = "glitch_subtle"
    assert layout_for(files, glitchy_shifted).cuts[2].key != layout_for(files, glitchy).cuts[1].key

    # A replaced picture (new modification time) is a new clip; so is another preset.
    picture = files.folder / "06_images" / "scene_02.png"
    stat = picture.stat()
    os.utime(picture, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    assert layout_for(files, base).scenes[1].key != first.scenes[1].key
    config = load_render_config()
    big = assembly.plan_layout(base, config.presets["720p"], folder=files.folder, config=config)
    assert big.scenes[0].key != first.scenes[0].key and big.clips_dir == "clips/720p"


def test_clip_encoding_never_slower_than_the_final(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_render_config()
    assert sc.clip_encoding_for(config.presets["1080p"]) == sc.ClipEncoding("fast", 14)
    assert sc.clip_encoding_for(config.presets["1080p"], "slow").x264_preset == "fast"
    assert sc.clip_encoding_for(config.presets["1080p"], "ultrafast").x264_preset == "ultrafast"
    assert sc.clip_encoding_for(config.proxy, "slow") == sc.ClipEncoding("ultrafast", 18)
    args = sc.ClipEncoding().args()
    for flag in ("-crf", "-pix_fmt", "-video_track_timescale", "-colorspace"):
        assert flag in args
    assert sc.keyframe_args([0, 12, 47, 12]) == ["-force_key_frames", "expr:eq(n,12)+eq(n,47)"]
    monkeypatch.setattr(sc, "_clip_settings", lambda: {"x264_preset": "veryfast", "crf": 16,
                                                       "workers": 1})
    assert sc.clip_encoding_for(config.presets["720p"]) == sc.ClipEncoding("veryfast", 16)
    assert sc.clip_workers() == 1


# A real render -------------------------------------------------------------------------------


def _frames_at(path: Path, numbers: list[int], size: tuple[int, int]) -> np.ndarray:
    """RGB frames ``numbers`` (0-based) of a video, in order."""
    expr = "+".join(f"eq(n\\,{n})" for n in numbers)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf",
         f"select='{expr}',{sc.TO_RGB},format=rgb24", "-fps_mode", "passthrough",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        capture_output=True, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, size[1], size[0], 3)


def _video_stream(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=nb_frames,duration,width,height,color_space", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout
    return json.loads(out)["streams"][0]


@pytest.fixture(scope="module")
def rendered() -> Iterator[dict[str, Any]]:
    """Four pictures, three real cuts (zoom_through, light_leak_warm, whip_left) and the
    closing dip to black, rendered once at 480p for the tests below."""
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("FFmpeg is not installed")
    root = TMP_ROOT / f"asm-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    root.mkdir(parents=True, exist_ok=True)
    try:
        files = write_project(root, music=False, popups=False)
        timeline = make_timeline(
            [1.6, 1.4, 1.4, 1.6],
            [("zoom_through", 0.4), ("light_leak_warm", 0.4), ("whip_left", 0.3),
             ("dip_black", 0.5)],
            images=["06_images/scene_01.png", "06_images/scene_02.png",
                    "06_images/scene_03.png", "06_images/scene_01.png"],
            motions=[ZOOM_IN, PAN_RIGHT, ZOOM_IN, HOLD],
        )
        config = load_render_config()
        plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
        events: list[tuple[str, float]] = []
        result = ff.run_render(
            plan, files.edit_dir / "proxy.mp4", work_dir=files.edit_dir,
            log_path=files.edit_dir / "render.log",
            progress=lambda message, pct: events.append((message, pct)),
        )
        yield {"files": files, "timeline": timeline, "plan": plan, "result": result,
               "events": events, "config": config}
    finally:
        shutil.rmtree(root, ignore_errors=True)


@requires_ffmpeg
def test_render_length_matches_the_timeline_to_the_frame(rendered: dict[str, Any]) -> None:
    result, plan, timeline = rendered["result"], rendered["plan"], rendered["timeline"]
    video = _video_stream(result.path)
    assert int(video["nb_frames"]) == plan.layout.total_frames == round(timeline.duration_s * 30)
    assert abs(float(video["duration"]) - timeline.duration_s) <= 1 / 30
    assert abs(result.duration_s - timeline.duration_s) <= 1 / 30 + 0.03  # audio rounding
    assert (int(video["width"]), int(video["height"])) == (854, 480)
    assert video.get("color_space") == "bt709"
    assert (result.scene_clips_total, result.scene_clips_reused) == (4, 0)
    assert (result.cuts_total, result.cuts_reused) == (4, 0)
    assert set(result.phase_seconds) == {"scenes", "cuts", "assembly"}


@requires_ffmpeg
def test_every_cut_midpoint_shows_its_transition(rendered: dict[str, Any]) -> None:
    layout: assembly.Layout = rendered["plan"].layout
    path = rendered["result"].path
    cuts = layout.cuts[:3]
    numbers: list[int] = []
    for cut in cuts:
        body_start, body_len = layout.scene_frames[cut.a.number]
        start = body_start + body_len  # the cut's first frame
        numbers += [start - 1, start + cut.frames // 2, start + cut.frames]
    frames = _frames_at(path, numbers, (854, 480)).astype(np.float32)
    assert len(frames) == 9
    for i, cut in enumerate(cuts):
        before, middle, after = frames[3 * i: 3 * i + 3]
        assert middle.mean() > 25 and middle.std() > 8, cut.label  # not black, not flat
        assert np.abs(middle - before).mean() > 4, cut.label  # the change is under way ...
        assert np.abs(middle - after).mean() > 4, cut.label  # ... and not finished
    zoom, leak, whip = (frames[1], frames[4], frames[7])
    # light leak: brighter and warmer than both neighbours.
    neighbours = (frames[3] + frames[5]) / 2
    assert leak.mean() > neighbours.mean() + 5
    assert (leak[..., 0] - leak[..., 2]).mean() > (neighbours[..., 0] - neighbours[..., 2]).mean()
    # whip: smeared sideways, not up and down.
    def energy(img: np.ndarray, axis: int) -> float:
        return float(np.abs(np.diff(img.mean(axis=2), axis=axis)).mean())
    assert energy(whip, 1) < energy(frames[6], 1) / 2
    # zoom_through: softer than the picture before it (radial blur and crossfade).
    assert energy(zoom, 1) + energy(zoom, 0) < energy(frames[0], 1) + energy(frames[0], 0)


@requires_ffmpeg
def test_the_render_ends_on_black_and_logs_every_clip(rendered: dict[str, Any]) -> None:
    layout: assembly.Layout = rendered["plan"].layout
    last = _frames_at(rendered["result"].path, [layout.total_frames - 1], (854, 480))
    assert last.mean() < 3
    log = (rendered["files"].edit_dir / "render.log").read_text(encoding="utf-8")
    for number in (1, 2, 3, 4):
        assert f"scene {number:02d}: drawn in" in log
    assert "cut 01 (zoom_through, 12 frames): drawn in" in log
    assert "cut 02 (light_leak_warm, 12 frames): drawn in" in log
    assert "cut 03 (whip_left, 9 frames): drawn in" in log
    assert "cut 04 (fade to black, 15 frames): drawn in" in log
    assert "phases: scene clips" in log and "exit code 0" in log
    clips = rendered["files"].edit_dir / "clips" / "proxy"
    assert not list(clips.glob("*.part.mp4"))
    assert (clips / "concat.txt").read_text(encoding="utf-8").startswith("ffconcat version 1.0")


@requires_ffmpeg
def test_progress_runs_through_three_phases_to_100(rendered: dict[str, Any]) -> None:
    events = rendered["events"]
    assert all(message.startswith("Rendering proxy ") for message, _ in events)
    values = [pct for _, pct in events]
    assert values == sorted(values) and values[0] == 0.0 and values[-1] == 100.0
    assert any(0 < v < 55 for v in values) and 55.0 in values and 70.0 in values
    assert any(70 < v < 100 for v in values)


@requires_ffmpeg
def test_a_second_render_reuses_the_clips_and_an_edit_redraws_only_its_part(
    rendered: dict[str, Any],
) -> None:
    files, timeline, config = rendered["files"], rendered["timeline"], rendered["config"]
    plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
    again = ff.run_render(plan, files.edit_dir / "proxy_again.mp4", work_dir=files.edit_dir,
                          log_path=files.edit_dir / "render.log")
    assert (again.scene_clips_reused, again.cuts_reused) == (4, 4)
    assert again.duration_s == pytest.approx(rendered["result"].duration_s, abs=0.01)

    edited = timeline.model_copy(deep=True)
    edited.scenes[3].motion = edited.scenes[3].motion.model_validate(ZOOM_IN)  # scene 4 only
    edited.scenes[0].transition_out.type = "film_dissolve"  # and cut 1's look
    plan = ff.build_plan(edited, config.proxy, folder=files.folder, config=config)
    third = ff.run_render(plan, files.edit_dir / "proxy_edit.mp4", work_dir=files.edit_dir,
                          log_path=files.edit_dir / "render.log")
    assert (third.scene_clips_total, third.scene_clips_reused) == (4, 3)
    # cut 1 (new look), cut 3 (into the new scene 4) and the end fade are drawn again.
    assert (third.cuts_total, third.cuts_reused) == (4, 1)
    clips = files.edit_dir / "clips" / "proxy"
    assert len(list(clips.glob("scene_*.mp4"))) == 4  # the old scene 4 clip was tidied away
    assert int(_video_stream(third.path)["nb_frames"]) == plan.layout.total_frames


@requires_ffmpeg
def test_cancelling_keeps_the_finished_clips(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    timeline = make_timeline([1.0, 12.0, 12.0], [("film_dissolve", 0.3)] * 3)
    config = load_render_config()
    plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
    clips = files.edit_dir / "clips" / "proxy"
    cancel = threading.Event()

    def finished(pattern: str) -> list[Path]:
        return [p for p in clips.glob(pattern) if not p.name.endswith(".part.mp4")]

    def progress(_message: str, _pct: float) -> None:
        if finished("scene_01.*.mp4"):
            cancel.set()

    with pytest.raises(ff.RenderCancelled, match="proxy render was stopped"):
        ff.run_render(plan, files.edit_dir / "proxy.mp4", work_dir=files.edit_dir,
                      log_path=files.edit_dir / "render.log", progress=progress, cancel=cancel)
    assert not (files.edit_dir / "proxy.mp4").exists()
    assert len(finished("scene_01.*.mp4")) == 1  # finished: kept for next time
    assert not list(clips.glob("*.part.mp4"))  # unfinished: removed
    assert "finished clips are kept" in (files.edit_dir / "render.log").read_text("utf-8")


def _box(kind: bytes, payload: bytes = b"", *, large: bool = False) -> bytes:
    if large:
        return (1).to_bytes(4, "big") + kind + (16 + len(payload)).to_bytes(8, "big") + payload
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def test_only_whole_mp4_clips_count_as_cached(render_tmp: Path) -> None:
    whole = _box(b"ftyp", b"isom\0\0\2\0") + _box(b"moov", b"x" * 40) + _box(b"mdat", b"y" * 99)
    cases = {
        "whole.mp4": (whole, True),
        "large.mp4": (_box(b"ftyp", b"isom") + _box(b"moov", b"x" * 9)
                      + _box(b"mdat", b"y" * 50, large=True), True),
        "to_end.mp4": (_box(b"ftyp", b"isom") + _box(b"moov", b"x") + (0).to_bytes(4, "big")
                       + b"mdat" + b"y" * 70, True),
        "cut_short.mp4": (whole[:-30], False),
        "zeros.mp4": (b"\0" * len(whole), False),
        "no_moov.mp4": (_box(b"ftyp", b"isom") + _box(b"mdat", b"y" * 20), False),
        "not_mp4.mp4": (b"not a video at all, just some text", False),
        "empty.mp4": (b"", False),
    }
    for name, (data, expected) in cases.items():
        path = render_tmp / name
        path.write_bytes(data)
        assert sc.mp4_is_whole(path) is expected, name
    assert sc.mp4_is_whole(render_tmp / "missing.mp4") is False


def test_a_clip_that_cannot_be_read_is_removed_and_named(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    layout = layout_for(files, make_timeline([1.0, 1.0], [("film_dissolve", 0.2)] * 2))
    clips = render_tmp / "clips"
    clips.mkdir()
    cut = layout.cuts[0]
    bad = clips / cut.b.file_name
    bad.write_bytes(b"\0" * 64)
    error = assembly._damaged_clip(cut, clips, sc.ClipReadError(bad, "Invalid data"))
    assert "scene 2 could not be read (Invalid data), so it was removed" in str(error)
    assert not bad.exists()


@requires_ffmpeg
def test_damaged_cached_clips_are_drawn_again(render_tmp: Path) -> None:
    """A clip cut short (a crash) or full of zeros (a power cut just after it was saved) is
    not reused: it is removed and drawn again, and the render log says so."""
    files = write_project(render_tmp, music=False, popups=False)
    # 3 s: FFmpeg's loudnorm turns less than 3 s of the fixture's silent voice into NaN.
    timeline = make_timeline([1.5, 1.5], [("film_dissolve", 0.2)] * 2)
    config = load_render_config()
    clips = files.edit_dir / "clips" / "proxy"

    def render(name: str) -> ff.RenderResult:
        plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
        return ff.run_render(plan, files.edit_dir / name, work_dir=files.edit_dir,
                             log_path=files.edit_dir / "render.log")

    first = render("proxy.mp4")
    assert first.scene_clips_reused == 0
    scene_1, scene_2 = sorted(clips.glob("scene_*.mp4"))
    assert sc.mp4_is_whole(scene_1) and sc.mp4_is_whole(scene_2)
    scene_2.write_bytes(scene_2.read_bytes()[: scene_2.stat().st_size // 2])  # cut short
    scene_1.write_bytes(b"\0" * scene_1.stat().st_size)  # zeroed
    again = render("proxy_again.mp4")
    assert (again.scene_clips_total, again.scene_clips_reused) == (2, 0)
    assert again.cuts_reused == again.cuts_total == 2  # the cuts' own clips were fine
    assert sc.mp4_is_whole(scene_1) and sc.mp4_is_whole(scene_2)  # drawn again, same names
    assert again.duration_s == pytest.approx(first.duration_s, abs=0.01)
    log = (files.edit_dir / "render.log").read_text(encoding="utf-8")
    assert f"{scene_2.name} was damaged and has been removed" in log


class SolidProvider:
    """A frame provider that paints scene 2 (and only scene 2) one flat colour."""

    name = "solid-test"
    colour = (40, 160, 90)

    def __init__(self) -> None:
        self.requests: list[sc.SceneFrameRequest] = []

    def cache_token(self, request: sc.SceneFrameRequest) -> str | None:
        return "v1" if request.index == 1 else None

    def frames(self, request: sc.SceneFrameRequest) -> Iterator[np.ndarray]:
        self.requests.append(request)
        frame = np.empty((request.size[1], request.size[0], 3), np.uint8)
        frame[:] = self.colour
        for _ in range(request.frames - 3):  # a little short: the last frame is held
            yield frame


@requires_ffmpeg
def test_a_frame_provider_draws_the_scenes_it_takes(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False, popups=False)
    timeline = make_timeline([1.0, 1.0, 1.0], [("film_dissolve", 0.2)] * 3)
    provider = SolidProvider()
    sc.register_frame_provider(provider)
    try:
        assert provider in sc.frame_providers()
        config = load_render_config()
        plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
        jobs = plan.layout.scenes
        assert [job.renderer for job in jobs] == ["zoompan", "solid-test:v1", "zoompan"]
        result = ff.run_render(plan, files.edit_dir / "proxy.mp4", work_dir=files.edit_dir,
                               log_path=files.edit_dir / "render.log")
    finally:
        sc.unregister_frame_provider(provider)
    assert sc.frame_providers() == [p for p in sc.frame_providers() if p is not provider]
    request = provider.requests[0]
    assert request.index == 1 and request.size == (854, 480) and request.preset_id == "proxy"
    assert request.frames == jobs[1].frames and request.lead_in == 3 and request.tail_out == 3
    start, length = plan.layout.scene_frames[2]
    middle = _frames_at(result.path, [start + length // 2], (854, 480))[0].astype(int)
    assert np.abs(middle - SolidProvider.colour).max() <= 4
    # Without the provider the scene is drawn by zoompan again (a different clip).
    plan = ff.build_plan(timeline, load_render_config().proxy, folder=files.folder,
                         config=load_render_config())
    assert plan.layout.scenes[1].renderer == "zoompan"
    assert plan.layout.scenes[1].key != jobs[1].key


# The edit stage around it ---------------------------------------------------------------------


@requires_ffmpeg
def test_the_edit_review_says_what_was_reused_and_when_parallax_fell_back(
    render_tmp: Path,
) -> None:
    import asyncio

    from cashcow_studio.pipeline.stages.edit import EditStage, prepare_depth_maps, read_state
    from test_render_fixtures import make_ctx

    files = write_project(render_tmp, music=False, popups=False)
    stage = EditStage()
    first = asyncio.run(stage.run(make_ctx(files, edits={"presets": []})))  # proxy only
    assert first.needs_review_payload["render_notes"] == [
        "Proxy: reused 0 of 3 scene clips and 0 of 3 transitions."
    ]
    timeline = Timeline.model_validate_json(
        (files.edit_dir / "timeline.json").read_text(encoding="utf-8")
    )
    assert prepare_depth_maps(files.folder, timeline, None) == (None, "")

    # The reviewer turns scene 3 into a 2.5D parallax move; this test app has no depth model.
    edited = timeline.model_copy(deep=True)
    edited.scenes[2].motion = edited.scenes[2].motion.model_validate(
        {**HOLD, "style": "parallax"}
    )
    result = asyncio.run(stage.apply_edits(
        make_ctx(files, edits={"timeline": edited.model_dump(mode="json")})
    ))
    assert result is not None
    payload = result.needs_review_payload
    assert payload["render_notes"][0] == (
        "Proxy: reused 2 of 3 scene clips and 1 of 3 transitions."
    )
    assert payload["render_notes"][1] == (
        "2.5D parallax was not available for 1 scene, so they use the Ken Burns move instead."
    )
    proxy = next(row for row in payload["renders"] if row["preset"] == "proxy")
    assert proxy["reuse_note"] == "Reused 2 of 3 scene clips." and proxy["status"] == "done"
    state = read_state(files.edit_dir)["renders"]["proxy"]
    assert state["parallax_fallbacks"] == 1 and set(state["phase_seconds"]) == {
        "scenes", "cuts", "assembly"}
    assert any("Ken Burns" in warning or "depth" in warning.lower()
               for warning in payload["warnings"]), payload["warnings"]
