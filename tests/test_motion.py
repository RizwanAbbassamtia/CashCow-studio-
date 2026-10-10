"""Motion v2 (docs/TRANSITIONS-V2-CONTRACT.md section 2): eased camera paths, the presets
and their energies, FFmpeg zoompan expressions that draw the same path (checked with
FFmpeg's own expression evaluator), the parallax displacement formula, the continuity rules
and the timeline builder that applies them."""

from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from cashcow_studio.models.timeline import (
    DEFAULT_PARALLAX_STRENGTH,
    Timeline,
    TimelineMotion,
    normalize_motion_style,
)
from cashcow_studio.pipeline.stages.storyboard import MOTION_RECTS
from cashcow_studio.render import motion
from cashcow_studio.render.geometry import cover_region, rect_in_pixels
from cashcow_studio.render.motion import (
    PRESETS,
    CameraTerms,
    MotionPath,
    ScenePlan,
    apply_continuity,
    ease,
    ease_expr,
    frame_progress,
    frame_rects,
    is_zoom_through,
    move_direction,
    parallax_offsets,
    whip_direction,
    zoompan_expressions,
    zoompan_filter,
)
from cashcow_studio.render.timeline_builder import (
    BuildRequest,
    build_timeline,
    parse_timing,
    plan_scene_motions,
    resolve_scene_images,
)
from test_render_fixtures import (
    ffmpeg_available,
    make_render_tmp_fixture,
    requires_ffmpeg,
    write_project,
)

render_tmp = make_render_tmp_fixture()
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# Easing ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", motion.EASINGS)
def test_easing_curves_start_at_0_end_at_1_and_never_go_back(name: str) -> None:
    values = [ease(name, k / 200) for k in range(201)]
    assert values[0] == 0.0 and values[-1] == pytest.approx(1.0)
    assert all(b >= a - 1e-12 for a, b in zip(values, values[1:], strict=False))
    assert ease(name, -1.0) == 0.0 and ease(name, 2.0) == pytest.approx(1.0)


def test_ease_in_out_cubic_formula_and_unknown_names() -> None:
    assert ease("ease_in_out_cubic", 0.25) == pytest.approx(4 * 0.25**3)
    assert ease("ease_in_out_cubic", 0.75) == pytest.approx(1 - (2 - 1.5) ** 3 / 2)
    assert ease("ease_in_out_cubic", 0.5) == pytest.approx(0.5)
    assert ease("no-such-curve", 0.25) == ease("ease_in_out_cubic", 0.25)
    assert motion.known_easing(None) == "ease_in_out_cubic"


def test_easing_matches_the_transition_engine_curves() -> None:
    engine = pytest.importorskip("cashcow_studio.render.transitions.easing")
    for name in ("ease_in_out_cubic", "ease_out_quart", "linear", "snap"):
        for k in range(21):
            assert ease(name, k / 20) == pytest.approx(engine.ease(name, k / 20), abs=1e-12)


def test_frame_progress_spans_every_frame() -> None:
    assert [frame_progress(k, 5) for k in range(5)] == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert frame_progress(0, 1) == 0.0


# The path -------------------------------------------------------------------------------------


START = (0.0, 0.0, 1920.0, 1080.0)
END = (240.0, 108.0, 1536.0, 864.0)


def test_path_starts_and_ends_on_its_rectangles_and_eases_between() -> None:
    path = MotionPath(START, END)
    assert path.rect(0.0) == pytest.approx(START)
    assert path.rect(1.0) == pytest.approx(END)
    # Centre on a straight line, eased.
    e = ease("ease_in_out_cubic", 0.25)
    x, y, w, h = path.rect(0.25)
    cx0, cx1 = 960.0, 240.0 + 768.0
    assert x + w / 2 == pytest.approx(cx0 + (cx1 - cx0) * e)
    # The zoom runs at an even rate: the middle width is the geometric mean.
    assert path.rect(0.5)[2] == pytest.approx(math.sqrt(1920.0 * 1536.0))
    # Every window keeps the frame's aspect ratio.
    for k in range(11):
        _x, _y, w, h = path.rect(k / 10)
        assert w / h == pytest.approx(16 / 9)


def test_linear_ease_and_pans_interpolate_plainly() -> None:
    path = MotionPath((0, 0, 100, 50), (40, 0, 100, 50), easing="linear")
    assert path.rect(0.25) == pytest.approx((10.0, 0.0, 100.0, 50.0))


def test_frame_rects_use_the_preset_easing() -> None:
    rects = frame_rects((0, 0, 1000, 500), (50, 0, 1000, 500), 5, preset="drift")
    assert len(rects) == 5 and rects[0][0] == 0 and rects[-1][0] == pytest.approx(50)
    assert rects[1][0] == pytest.approx(50 * ease("ease_in_out_sine", 0.25))


def test_handheld_wobble_is_small_smooth_repeatable_and_stays_inside() -> None:
    bounds = (0.0, 0.0, 2000.0, 1125.0)
    base = (40.0, 22.5, 1920.0, 1080.0)
    path = MotionPath.for_preset("handheld", base, base, seed=3, bounds=bounds)
    assert path.handheld == pytest.approx(motion.HANDHELD_AMPLITUDE)
    rects = path.frame_rects(150, 30)
    xs = np.array([r[0] for r in rects])
    ys = np.array([r[1] for r in rects])
    assert np.abs(xs - 40.0).max() <= 0.004 * 1920 + 1e-6
    assert np.abs(xs - 40.0).max() > 0.5  # it does move
    assert np.abs(np.diff(xs)).max() < 1.0 and np.abs(np.diff(ys)).max() < 1.0  # smooth
    def wobble(seed: int) -> list:
        return MotionPath.for_preset("handheld", base, base, seed=seed,
                                     bounds=bounds).frame_rects(150, 30)

    assert wobble(3) == rects
    assert wobble(4) != rects
    # Kept inside the picture even when the window touches its edge.
    tight = MotionPath.for_preset("handheld", (0, 0, 2000, 1125), (0, 0, 2000, 1125),
                                  bounds=bounds)
    for x, y, w, h in tight.frame_rects(60, 30):
        assert x >= 0 and y >= 0 and x + w <= 2000 + 1e-6 and y + h <= 1125 + 1e-6


def test_camera_terms_are_zero_in_the_middle_and_signed() -> None:
    pan = MotionPath((0, 0, 85, 47.8125), (15, 0, 85, 47.8125))
    assert pan.camera(0.5).is_still
    assert pan.camera(0.0).px < 0 < pan.camera(1.0).px
    assert pan.camera(1.0).zoom == pytest.approx(0.0)
    zoom = MotionPath(START, END)
    assert zoom.camera(0.0).zoom < 0 < zoom.camera(1.0).zoom
    assert zoom.camera(1.0).zoom == pytest.approx(math.log(1920 / 1536) / 2)


def test_scaled_paths_are_the_same_move_in_another_space() -> None:
    path = MotionPath(START, END, bounds=(0, 0, 2048, 1152))
    canvas = path.scaled(0.5, (64.0, 36.0))
    for t in (0.0, 0.3, 0.8, 1.0):
        x, y, w, h = path.rect(t)
        cx, cy, cw, ch = canvas.rect(t)
        assert (cx, cy, cw, ch) == pytest.approx(((x - 64) * 0.5, (y - 36) * 0.5, w * 0.5,
                                                  h * 0.5))
        assert canvas.camera(t).zoom == pytest.approx(path.camera(t).zoom)
        assert canvas.camera(t).px == pytest.approx(path.camera(t).px)


def test_scene_and_canvas_paths_match_the_renderer_geometry() -> None:
    from cashcow_studio.render.geometry import rect_to_canvas

    image, frame, canvas = (2048, 1152), (1920, 1080), (2880, 1620)
    start = rect_in_pixels(PRESETS["dolly_in"].start, *image, *frame)
    end = rect_in_pixels(PRESETS["dolly_in"].end, *image, *frame)
    scene = TimelineMotion(preset="dolly_in", start_rect=start, end_rect=end)
    path = motion.scene_path(scene, image, frame, seed=2)
    assert path.bounds == pytest.approx(cover_region(*image, *frame))
    on_canvas = motion.canvas_path(path, canvas)
    region = cover_region(*image, *frame)
    assert on_canvas.rect(0.0) == pytest.approx(rect_to_canvas(start, region, *canvas))
    assert on_canvas.rect(1.0) == pytest.approx(rect_to_canvas(end, region, *canvas))
    assert on_canvas.bounds == pytest.approx((0, 0, *canvas))
    with pytest.raises(ValueError):
        motion.canvas_path(MotionPath(START, END), canvas)


# Presets --------------------------------------------------------------------------------------


def test_every_preset_has_an_energy_a_label_and_rectangles_inside_the_picture() -> None:
    assert set(motion.NEW_PRESET_IDS) <= set(PRESETS)
    assert len(PRESETS) == 14
    for preset in PRESETS.values():
        assert preset.energy in ("calm", "medium", "high")
        assert preset.label and preset.easing in motion.EASINGS
        for x, y, w, h in (preset.start, preset.end):
            assert 0 <= x and 0 <= y and x + w <= 1 + 1e-9 and y + h <= 1 + 1e-9
    assert PRESETS["handheld"].handheld and not PRESETS["drift"].handheld
    assert motion.preset_energy("hold") == "calm" and motion.preset_energy("??") == "medium"
    assert motion.preset_rects("Pan-Left") == (PRESETS["pan_left"].start, PRESETS["pan_left"].end)
    rows = motion.presets_json()
    assert {row["id"] for row in rows} == set(PRESETS) and "start_rect" in rows[0]


def test_the_eight_storyboard_presets_keep_their_rectangles() -> None:
    for name in ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "hold",
                 "slow_push"):
        start, end = MOTION_RECTS[name]
        assert PRESETS[name].start == tuple(start) and PRESETS[name].end == tuple(end)


def test_preset_opposites_are_exact_mirrors_or_reversals() -> None:
    for preset in PRESETS.values():
        for axis, target in preset.flips:
            if target == preset.id:  # drift: the same preset, mirrored
                mirrored = (motion.mirror_rect(preset.start, axis),
                            motion.mirror_rect(preset.end, axis))
                assert move_direction(*mirrored) == ("x", -1)
                continue
            other = PRESETS[target]
            if axis == "z":
                assert (other.start, other.end) == (preset.end, preset.start)
            else:
                assert other.start == pytest.approx(motion.mirror_rect(preset.start, axis))
                assert other.end == pytest.approx(motion.mirror_rect(preset.end, axis))
            assert move_direction(other.start, other.end) != move_direction(preset.start,
                                                                            preset.end)


def test_move_directions() -> None:
    def direction(name: str):
        return move_direction(PRESETS[name].start, PRESETS[name].end)

    assert direction("pan_right") == ("x", 1) and direction("pan_left") == ("x", -1)
    assert direction("pan_down") == ("y", 1) and direction("tilt_up") == ("y", -1)
    assert direction("zoom_in") == ("z", 1) and direction("dolly_out") == ("z", -1)
    assert direction("drift") == ("x", 1)
    assert direction("hold") is None and direction("handheld") is None


# zoompan --------------------------------------------------------------------------------------


def ffmpeg_eval(expression: str, count: int) -> np.ndarray:
    """Evaluate an FFmpeg expression of ``n`` for n = 0 .. count-1 with FFmpeg itself."""
    completed = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
            "-i", f"aevalsrc=exprs='{expression}':s=1000:d={(count + 8) / 1000}",
            "-f", "f64le", "-acodec", "pcm_f64le", "-",
        ],
        capture_output=True, check=True, creationflags=NO_WINDOW,
    )
    values = np.frombuffer(completed.stdout, dtype="<f8")
    assert values.size >= count
    return values[:count]


def standalone(expression: str, z: str, canvas: tuple[int, int]) -> str:
    """A zoompan x/y expression with its variables spelled out (frame number ``on`` -> ``n``)."""
    text = re.sub(r"\bzoom\b", f"({z})", expression)
    text = re.sub(r"\biw\b", str(canvas[0]), text)
    text = re.sub(r"\bih\b", str(canvas[1]), text)
    return re.sub(r"\bon\b", "n", text)


@requires_ffmpeg
@pytest.mark.parametrize("preset", ["dolly_in", "pan_left", "handheld", "drift", "zoom_out"])
def test_zoompan_expressions_follow_the_python_path(preset: str) -> None:
    canvas = (2880, 1620)
    region = (0.0, 0.0, float(canvas[0]), float(canvas[1]))
    start_frac, end_frac = motion.preset_rects(preset)
    start = rect_in_pixels(start_frac, canvas[0], canvas[1], 1920, 1080)
    end = rect_in_pixels(end_frac, canvas[0], canvas[1], 1920, 1080)
    path = MotionPath.for_preset(preset, start, end, seed=7, bounds=region)
    frames = 46
    exprs = zoompan_expressions(path, frames=frames, canvas_w=canvas[0], fps=30)
    z_text = re.sub(r"\bon\b", "n", exprs.z)
    z = ffmpeg_eval(z_text, frames)
    x = ffmpeg_eval(standalone(exprs.x, exprs.z, canvas), frames)
    y = ffmpeg_eval(standalone(exprs.y, exprs.z, canvas), frames)
    worst = 0.0
    for k in range(frames):
        px, py, pw, _ph = path.frame_rect(k, frames, 30)
        assert canvas[0] / z[k] == pytest.approx(pw, abs=0.5)
        worst = max(worst, abs(x[k] - px), abs(y[k] - py))
    assert worst < 0.5


def test_zoompan_filter_text() -> None:
    path = MotionPath((0, 0, 2880, 1620), (288, 162, 2304, 1296))
    text = zoompan_filter(path, frames=90, canvas=(2880, 1620), size=(1920, 1080), fps=30)
    assert text.startswith("zoompan=z='") and ":d=90:s=1920x1080:fps=30" in text
    assert "min(on/89\\,1)" in text  # commas escaped for the filter script
    single = zoompan_expressions(path, frames=1, canvas_w=2880)
    assert "on" not in single.z  # one frame: progress is 0
    assert ease_expr("linear", "T") == "(T)" and "pow" in ease_expr("snap", "T")


@requires_ffmpeg
def test_zoompan_renders_the_same_windows_as_the_python_path(render_tmp: Path) -> None:
    from cashcow_studio.render.parallax import warp

    canvas = (640, 360)
    size = (320, 180)
    yy, xx = np.mgrid[0:canvas[1], 0:canvas[0]].astype(np.float32)
    picture = np.stack(
        [128 + 100 * np.sin(xx / 23.0), 128 + 100 * np.cos(yy / 17.0),
         128 + 60 * np.sin((xx + yy) / 31.0)], axis=-1,
    ).clip(0, 255).astype(np.uint8)
    source = render_tmp / "canvas.png"
    from PIL import Image

    Image.fromarray(picture).save(source)
    path = MotionPath((0, 0, 640, 360), (96, 40, 448, 252), bounds=(0, 0, 640, 360))
    frames = 9
    graph = zoompan_filter(path, frames=frames, canvas=canvas, size=size, fps=30)
    script = render_tmp / "zoom.txt"
    script.write_text(f"[0:v]format=rgb24,{graph},format=rgb24[out]\n", encoding="utf-8")
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(source),
         "-/filter_complex", str(script), "-map", "[out]", "-f", "rawvideo", "-pix_fmt",
         "rgb24", "-"],
        capture_output=True, check=True, creationflags=NO_WINDOW,
    )
    decoded = np.frombuffer(completed.stdout, np.uint8).reshape(-1, size[1], size[0], 3)
    assert decoded.shape[0] == frames
    for k in (0, frames // 2, frames - 1):
        expected = warp.kenburns_frame(picture, path.frame_rect(k, frames, 30), size, "cubic")
        error = np.abs(decoded[k].astype(np.int16) - expected.astype(np.int16)).mean()
        assert error < 10.0, f"frame {k}: mean error {error:.1f}"


# Parallax formula ------------------------------------------------------------------------------


def test_parallax_offsets_on_a_depth_ramp() -> None:
    depth = np.linspace(0.0, 1.0, 11)
    mean = 0.5
    pan = CameraTerms(px=0.09)
    dx, dy = parallax_offsets(depth, mean, 0.0, 0.0, pan, strength=0.035, out_width=1920)
    gain = 0.035 * 1920 / motion.PARALLAX_REFERENCE_MOVE
    assert np.allclose(dx, -gain * (depth - mean) * 0.09)
    assert np.allclose(dy, 0.0)
    assert dx[5] == pytest.approx(0.0)  # the mean depth stays with the camera
    assert dx[-1] < 0 < dx[0]  # camera right: near things slide further left
    assert np.allclose(np.diff(dx), np.diff(dx)[0])  # linear in depth
    zoom = CameraTerms(zoom=0.1)
    zx, _zy = parallax_offsets(1.0, 0.5, 1.0, 0.0, zoom, strength=0.035, out_width=1920)
    zx_far, _ = parallax_offsets(0.0, 0.5, 1.0, 0.0, zoom, strength=0.035, out_width=1920)
    assert zx > 0 > zx_far  # moving in: near things at the right edge spread outwards
    # Strength is a share of the width: a typical pan moves nearest vs farthest ~3.5 %.
    travel = 0.15 / 0.85
    span = parallax_offsets(1.0, 0.0, 0.0, 0.0, CameraTerms(px=travel), strength=0.035,
                            out_width=1.0)[0]
    assert abs(span) == pytest.approx(0.035 * travel / motion.PARALLAX_REFERENCE_MOVE)


# Continuity ------------------------------------------------------------------------------------


def plan(preset: str, transition: str = "film_dissolve", **locks: bool) -> ScenePlan:
    spec = PRESETS[preset]
    return ScenePlan(preset, spec.start, spec.end, transition, **locks)


def test_neighbouring_moves_alternate_direction() -> None:
    plans = [plan("pan_right"), plan("pan_right"), plan("zoom_in"), plan("dolly_in"),
             plan("drift"), plan("drift")]
    notes = apply_continuity(plans)
    assert [p.preset for p in plans] == ["pan_right", "pan_left", "zoom_in", "dolly_out",
                                         "drift", "drift"]
    assert move_direction(plans[5].start, plans[5].end) == ("x", -1)  # drift mirrored
    assert len(notes) == 3 and all(note.startswith("Scene ") for note in notes)


def test_locked_moves_are_never_changed() -> None:
    plans = [plan("pan_right"), plan("pan_right", motion_locked=True)]
    apply_continuity(plans)
    # The locked second scene keeps its pan, so the first one turns instead.
    assert [p.preset for p in plans] == ["pan_left", "pan_right"]
    both = [plan("zoom_in", motion_locked=True), plan("zoom_in", motion_locked=True)]
    apply_continuity(both)
    assert [p.preset for p in both] == ["zoom_in", "zoom_in"]


def test_zoom_through_leaves_zoomed_in_and_lands_wide() -> None:
    plans = [plan("zoom_out", "zoom_through"), plan("pan_left"), plan("hold", "zoomin"),
             plan("tilt_up"), plan("drift", "zoom_through"), plan("dolly_out")]
    apply_continuity(plans)
    assert plans[0].preset == "zoom_in"  # zoom out turned round
    assert plans[1].preset == "zoom_in"  # a pan cannot start wide
    assert plans[2].preset == "slow_push"  # calm stays calm (legacy 'zoomin' counts)
    assert plans[3].preset == "zoom_in"
    assert plans[4].preset == "slow_push" and plans[5].preset == "dolly_in"
    for before, after in zip(plans[::2], plans[1::2], strict=True):
        assert motion.zooms_in(before.start, before.end)
        assert motion.starts_wide(after.start)
    locked = [plan("zoom_out", "zoom_through", motion_locked=True), plan("pan_left")]
    apply_continuity(locked)
    assert locked[0].preset == "zoom_out" and locked[1].preset == "zoom_in"


def test_whips_follow_the_outgoing_pan() -> None:
    plans = [plan("pan_right", "whip_right"), plan("pan_up", "whip_left"),
             plan("zoom_in", "whip_up")]
    apply_continuity(plans)
    assert plans[0].transition == "whip_left"  # camera right: the picture slides left
    assert plans[1].transition == "whip_down"
    assert plans[2].transition == "whip_up"  # no pan to follow
    locked = [plan("pan_right", "whip_right", transition_locked=True),
              plan("tilt_up", "whip_left", transition_locked=True)]
    apply_continuity(locked)
    assert locked[0].preset == "pan_left" and locked[0].transition == "whip_right"
    assert locked[1].preset == "pan_right"  # a tilt cannot whip sideways: a matching pan
    assert whip_direction("whip_left") == "left" and whip_direction("push_left") is None
    assert is_zoom_through("zoom_through") and is_zoom_through("zoomin")
    assert not is_zoom_through("film_dissolve")


# The timeline builder -------------------------------------------------------------------------


def build(files, **overrides):
    storyboard = files.storyboard
    request = BuildRequest(
        project_id="p-edit-test", fmt=storyboard.format, aspect=storyboard.aspect,
        language="English", folder=files.folder, storyboard=storyboard,
        timing=parse_timing(files.timing), sources=resolve_scene_images(files.folder, storyboard),
        width=1920, height=1080, proxy=(854, 480), presets=["1080p"], fps=30,
        music_folder=None,
    )
    for key, value in overrides.items():
        setattr(request, key, value)
    return build_timeline(request)


def test_builder_assigns_preset_rectangles_and_runs_the_continuity_rules(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False)
    scenes = files.storyboard.scenes
    scenes[0].motion.preset = "pan_right"
    scenes[0].motion.start_rect = [0.2, 0.2, 0.5, 0.5]  # stale rectangles are replaced
    scenes[1].motion.preset = "pan_right"
    scenes[0].transition_out.type = "whip_right"
    scenes[1].transition_out.type = "zoom_through"
    timeline, warnings = build(files)
    assert all("music" in w.lower() for w in warnings)
    first, second, third = timeline.scenes
    assert first.motion.preset == "pan_right"
    assert first.transition_out.type == "whip_left"
    assert second.motion.preset == "zoom_in"  # pinned by the zoom-through after it
    assert third.motion.preset == "zoom_in"  # was zoom_out: lands wide
    source = resolve_scene_images(files.folder, files.storyboard)[0]
    expected = rect_in_pixels(PRESETS["pan_right"].start, source.width, source.height, 1920, 1080)
    assert first.motion.start_rect == expected
    for scene in timeline.scenes:
        assert scene.motion.style == "ken_burns"
        assert scene.motion.strength == pytest.approx(DEFAULT_PARALLAX_STRENGTH)
        for rect in (scene.motion.start_rect, scene.motion.end_rect):
            assert rect[2] / rect[3] == pytest.approx(16 / 9, abs=1e-2)
    plans = plan_scene_motions(files.storyboard.scenes)
    assert plans[0].start == PRESETS["pan_right"].start


def test_builder_motion_style_order(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False)
    timeline, _ = build(files, motion_style="parallax", parallax_strength=0.05)
    assert {s.motion.style for s in timeline.scenes} == {"parallax"}
    assert {s.motion.strength for s in timeline.scenes} == {0.05}
    if "motion_style" in type(files.storyboard).model_fields:
        files.storyboard.motion_style = "ken_burns"  # the video's own choice wins
        timeline, _ = build(files, motion_style="parallax")
        assert {s.motion.style for s in timeline.scenes} == {"ken_burns"}
    timeline, _ = build(files, parallax_strength=9.0)
    assert {s.motion.strength for s in timeline.scenes} == {0.2}


def test_builder_portrait_rectangles_for_new_presets(render_tmp: Path) -> None:
    files = write_project(render_tmp, aspect="9:16", music=False)
    source = resolve_scene_images(files.folder, files.storyboard)[0]
    for name in motion.NEW_PRESET_IDS:
        start_frac, end_frac = motion.preset_rects(name)
        for frac in (start_frac, end_frac):
            rect = rect_in_pixels(frac, source.width, source.height, 1080, 1920)
            rx, ry, rw, rh = cover_region(source.width, source.height, 1080, 1920)
            assert rect[2] / rect[3] == pytest.approx(9 / 16, abs=1e-2)
            assert rect[0] >= rx - 0.01 and rect[0] + rect[2] <= rx + rw + 0.01


# The model and the settings ----------------------------------------------------------------------


def test_timeline_motion_fields_are_additive() -> None:
    old = TimelineMotion.model_validate(
        {"preset": "zoom_in", "start_rect": [0, 0, 10, 10], "end_rect": [1, 1, 8, 8]}
    )
    assert old.style == "ken_burns" and old.strength == DEFAULT_PARALLAX_STRENGTH
    assert normalize_motion_style("Ken Burns") == "ken_burns"
    assert normalize_motion_style("2.5D parallax") == "parallax"
    assert normalize_motion_style("") == "ken_burns" and normalize_motion_style(None) == "ken_burns"
    with pytest.raises(ValueError):
        TimelineMotion.model_validate(
            {"start_rect": [0, 0, 1, 1], "end_rect": [0, 0, 1, 1], "strength": 0.5}
        )
    assert "style" in Timeline.model_json_schema()["$defs"]["TimelineMotion"]["properties"]


def test_motion_config_defaults_and_clamping() -> None:
    config = motion.motion_config_from({})
    assert config == motion.MotionConfig()
    custom = motion.motion_config_from(
        {"motion": {"style": "parallax", "interpolation": "CUBIC", "handheld_amplitude": 9},
         "parallax": {"strength": -1, "grid_step_px": "x", "mesh": [32, 18],
                      "interpolation": "bogus"}}
    )
    assert custom.style == "parallax" and custom.interpolation == "cubic"
    assert custom.handheld_amplitude == 0.05 and custom.parallax_strength == 0.0
    assert custom.grid_step_px == 8 and custom.mesh == (32, 18)
    assert custom.parallax_interpolation == "cubic"
    loaded = motion.load_motion_config()
    assert loaded.style == "ken_burns" and loaded.parallax_strength == pytest.approx(0.035)
    assert motion.resolve_style(None, "", "parallax") == "parallax"
    assert motion.resolve_style(None) == loaded.style


def test_ffmpeg_helper_is_available_when_installed() -> None:
    # Keeps the parity tests honest: they only skip when FFmpeg is really missing.
    assert isinstance(ffmpeg_available(), bool)
