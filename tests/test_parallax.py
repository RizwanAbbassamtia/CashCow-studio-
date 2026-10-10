"""2.5D parallax and the Python frame path (docs/TRANSITIONS-V2-CONTRACT.md section 2): the
fallback to Ken Burns without the depth model, the displacement on a synthetic depth ramp,
the near-object layer and the background filled in behind it, the depth map cache, the
verified model download (mock transport, never the network), a real onnxruntime session on
a tiny hand-made model, and the FFmpeg rawvideo writer."""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest
from PIL import Image

from cashcow_studio.render import motion
from cashcow_studio.render.ffmpeg import RenderCancelled, RenderError
from cashcow_studio.render.motion import CameraTerms, MotionPath
from cashcow_studio.render.parallax import clip as clip_mod
from cashcow_studio.render.parallax import depth as depth_mod
from cashcow_studio.render.parallax import warp
from cashcow_studio.render.parallax.depth import (
    DepthEstimator,
    DepthModelError,
    DepthModelSpec,
    RuntimeCheck,
)
from cashcow_studio.render.parallax.writer import ClipWriter, write_clip
from test_render_fixtures import make_render_tmp_fixture, requires_ffmpeg

render_tmp = make_render_tmp_fixture()
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CONFIG = motion.MotionConfig()


def texture(width: int, height: int, seed: int = 1) -> np.ndarray:
    """A smooth, detailed RGB picture (so shifts can be measured)."""
    rng = np.random.default_rng(seed)
    noise = rng.random((height, width, 3)).astype(np.float32) * 255
    picture = cv2.GaussianBlur(noise, (0, 0), 1.6)
    picture = (picture - picture.mean()) * 3.0 + 128
    return np.clip(picture, 0, 255).astype(np.uint8)


def decode(path: Path, width: int, height: int) -> np.ndarray:
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True, creationflags=NO_WINDOW,
    )
    return np.frombuffer(completed.stdout, np.uint8).reshape(-1, height, width, 3)


def probe_frames(path: Path) -> tuple[int, int, int]:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames,width,height,color_space", "-of", "json",
         str(path)],
        capture_output=True, text=True, check=True, creationflags=NO_WINDOW,
    )
    stream = json.loads(completed.stdout)["streams"][0]
    assert stream.get("color_space") == "bt709"
    return int(stream["nb_read_frames"]), int(stream["width"]), int(stream["height"])


@pytest.fixture
def no_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(depth_mod, "runtime_check",
                        lambda refresh=False: RuntimeCheck(False, None, depth_mod.NOT_INSTALLED))


@pytest.fixture
def runtime_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(depth_mod, "runtime_check",
                        lambda refresh=False: RuntimeCheck(True, "test", ""))


# Fallback without the model -------------------------------------------------------------------


def test_status_without_the_model(tmp_path: Path, runtime_ok: None) -> None:
    status = depth_mod.depth_model_status(tmp_path)
    assert status.state == "not_downloaded" and not status.ready and status.can_download
    assert "Ken Burns" in status.message and "99 MB" in status.message
    assert status.path.endswith("depth_anything_v2_small.onnx")
    assert status.to_dict()["license"] == "Apache-2.0"


def test_status_when_onnxruntime_is_missing(tmp_path: Path, no_runtime: None) -> None:
    status = depth_mod.depth_model_status(tmp_path)
    assert status.state == "unavailable" and not status.can_download
    assert "not installed" in status.message
    # Starting a download then does nothing and says why.
    assert depth_mod.start_depth_model_download(tmp_path).state == "unavailable"


def test_depth_maps_fall_back_without_the_model(tmp_path: Path, runtime_ok: None) -> None:
    picture = tmp_path / "scene_01.png"
    Image.fromarray(texture(96, 54)).save(picture)
    batch = depth_mod.ensure_depth_maps([picture], tmp_path / "app")
    assert batch.maps == {} and batch.made == 0
    assert batch.fallback_reason and "Not downloaded" in batch.fallback_reason
    assert not depth_mod.depth_map_path(picture).exists()


@requires_ffmpeg
def test_parallax_clip_without_depth_is_a_ken_burns_clip(render_tmp: Path) -> None:
    picture = render_tmp / "scene_01.png"
    Image.fromarray(texture(320, 180)).save(picture)
    result = clip_mod.render_motion_clip(
        picture, render_tmp / "clip.mp4", size=(160, 90), fps=30, frames=8,
        start_rect=[0, 0, 320, 180], end_rect=[32, 18, 256, 144], preset="zoom_in",
        style="parallax", x264_preset="ultrafast",
    )
    assert result.requested_style == "parallax" and result.style == "ken_burns"
    assert result.fallback_reason == clip_mod.NO_DEPTH
    assert probe_frames(result.path) == (8, 160, 90)
    assert clip_mod.depth_fingerprint(picture) == ""


# Displacement maths on a synthetic depth ramp --------------------------------------------------


def test_parallax_shift_follows_the_depth_ramp() -> None:
    width, height = 480, 270
    picture = texture(width, height, seed=5)
    ramp = np.repeat(np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None], width, axis=1)
    layers = warp.prepare_layers(picture, ramp, CONFIG)
    assert not layers.layered  # a smooth ramp has no near object: one layer
    rect = (0.0, 0.0, float(width), float(height))
    still = warp.parallax_frame(layers, rect, CameraTerms(), (width, height), strength=0.035)
    flat = warp.kenburns_frame(picture, rect, (width, height), "cubic")
    assert np.abs(still.astype(int) - flat.astype(int)).max() <= 2  # no move: no shift
    camera = CameraTerms(px=0.09)
    moved = warp.parallax_frame(layers, rect, camera, (width, height), strength=0.035,
                                iterations=3)
    gain = motion.parallax_gain(0.035, width)
    grey_a = cv2.cvtColor(flat, cv2.COLOR_RGB2GRAY).astype(np.float32)
    grey_b = cv2.cvtColor(moved, cv2.COLOR_RGB2GRAY).astype(np.float32)
    band = 24
    measured, predicted = [], []
    for top in range(30, height - 30 - band, 40):
        rows = slice(top, top + band)
        (shift_x, shift_y), _ = cv2.phaseCorrelate(grey_a[rows, 40:-40], grey_b[rows, 40:-40])
        depth = float(layers.depth_back[int((top + band / 2) * layers.low_scale[1])].mean())
        expected = motion.parallax_offsets(depth, layers.mean_depth, 0.0, 0.0, camera,
                                           strength=0.035, out_width=width)[0]
        measured.append(shift_x)
        predicted.append(expected)
        assert abs(shift_y) < 0.35
    assert np.allclose(measured, predicted, atol=0.5)
    assert measured[0] > 0 > measured[-1]  # far rows slide right, near rows left
    assert abs(gain) > 0


def test_coarse_displacement_matches_the_formula_for_flat_depth() -> None:
    depth = np.full((40, 60), 0.8, np.float32)
    rect = (10.0, 5.0, 300.0, 168.75)
    camera = CameraTerms(px=0.05, py=-0.02, zoom=0.04)
    dx, dy = warp.solve_displacement(depth, (60 / 320, 40 / 180), 0.3, rect, camera,
                                     (320, 180), gain=100.0, step=8, iterations=2)
    grid_w, grid_h = dx.shape[1], dx.shape[0]
    u = (np.arange(grid_w) + 0.5) * 320 / grid_w
    v = (np.arange(grid_h) + 0.5) * 180 / grid_h
    nx, ny = (u - 160) / 160, (v - 90) / 160
    k = 100.0 * 300 / 320 * (0.8 - 0.3)
    assert np.allclose(dx, -k * (nx[None, :] * 0.04 - 0.05), atol=1e-4)
    assert np.allclose(dy, -k * (ny[:, None] * 0.04 + 0.02), atol=1e-4)


# The near-object layer and the background behind it -------------------------------------------


def near_square_scene(width: int = 320, height: int = 180):
    picture = np.zeros((height, width, 3), np.uint8)
    picture[:] = (40, 90, 200)  # blue background
    yy, xx = np.mgrid[0:height, 0:width]
    picture[((xx // 8 + yy // 8) % 2) == 0] = (60, 120, 230)  # with a little pattern
    box = (slice(50, 130), slice(120, 220))
    picture[box] = (230, 120, 30)  # an orange near object
    depth = np.full((height, width), 0.2, np.float32)
    depth[box] = 0.9
    depth = cv2.GaussianBlur(depth, (0, 0), 1.5)
    return picture, depth, box


def test_near_objects_get_their_own_layer_and_the_background_is_filled_behind() -> None:
    picture, depth, box = near_square_scene()
    analysis = warp.analyse_depth(cv2.resize(depth, (160, 90), interpolation=cv2.INTER_AREA),
                                  CONFIG)
    assert analysis.alpha is not None and analysis.edges.any()
    layers = warp.prepare_layers(picture, depth, CONFIG)
    assert layers.layered and layers.stats["layers"] == 2
    alpha = layers.alpha
    assert alpha is not None
    assert alpha[box].mean() > 0.95 and alpha[:30].max() < 0.05  # the square, not the sky
    inner = (slice(box[0].start + 12, box[0].stop - 12), slice(box[1].start + 12,
                                                              box[1].stop - 12))
    filled = layers.plate[inner].reshape(-1, 3).mean(axis=0)
    assert filled[2] > filled[0]  # blue background, not the orange square, behind it
    assert layers.stats["filled_share"] >= layers.stats["front_share"] > 0.1
    # The back layer's depth under the square is the background's.
    low_box = layers.depth_back[int(90 * layers.low_scale[1]), int(170 * layers.low_scale[0])]
    assert low_box < 0.4
    # At the middle of the move the frame is the picture itself.
    rect = (0.0, 0.0, 320.0, 180.0)
    still = warp.parallax_frame(layers, rect, CameraTerms(), (320, 180), strength=0.035)
    flat = warp.kenburns_frame(picture, rect, (320, 180), "cubic")
    assert np.abs(still.astype(int) - flat.astype(int)).mean() < 1.0
    # Moving the camera slides the square over the filled background without smearing it.
    moved = warp.parallax_frame(layers, rect, CameraTerms(px=0.12), (320, 180), strength=0.06)
    shift = motion.parallax_offsets(0.9, layers.mean_depth, 0.0, 0.0, CameraTerms(px=0.12),
                                    strength=0.06, out_width=320)[0]
    assert shift < -4
    column = int(box[1].stop + shift / 2)  # where the square's right edge was: now uncovered
    uncovered = moved[box[0].start + 10:box[0].stop - 10, column]
    assert uncovered[:, 2].mean() > uncovered[:, 0].mean()  # background colour shows


def test_front_layer_drawn_in_its_box_equals_the_whole_frame_blend() -> None:
    picture, depth, _box = near_square_scene()
    layers = warp.prepare_layers(picture, depth, CONFIG)
    rect = (8.0, 4.5, 300.0, 168.75)
    camera = CameraTerms(px=-0.07, zoom=0.05)
    size = (320, 180)
    fast = warp.parallax_frame(layers, rect, camera, size, strength=0.05, grid_step=8)
    common = {"low_scale": layers.low_scale, "mean_depth": layers.mean_depth, "rect": rect,
              "camera": camera, "out_size": size, "gain": motion.parallax_gain(0.05, 320),
              "step": 8, "iterations": 2}
    back = cv2.remap(layers.plate, *warp.full_maps(*warp.solve_displacement(
        layers.depth_back, **common), rect, size), cv2.INTER_CUBIC, borderMode=warp.BORDER)
    maps = warp.full_maps(*warp.solve_displacement(layers.depth_front, **common), rect, size)
    front = cv2.remap(layers.image, *maps, cv2.INTER_CUBIC, borderMode=warp.BORDER)
    alpha = cv2.remap(layers.alpha, *maps, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                      borderValue=0.0)
    reference = cv2.blendLinear(front, back, alpha, 1.0 - alpha)
    assert np.abs(fast.astype(int) - reference.astype(int)).max() <= 1
    box = warp.front_box(layers, *warp.solve_displacement(layers.depth_front, **common), rect,
                         size, 8)
    assert box is not None and (box[2] - box[0]) * (box[3] - box[1]) < 0.6 * 320 * 180
    gone = warp.front_box(layers, np.full((23, 40), 5000.0, np.float32),
                          np.zeros((23, 40), np.float32), rect, size, 8)
    assert gone is None  # the object pushed off the picture: no front layer this frame


def test_inpaint_mask_covers_the_object_and_its_rim() -> None:
    picture, depth, box = near_square_scene()
    alpha = np.zeros(depth.shape, np.float32)
    alpha[box] = 1.0
    plate, mask = warp.fill_behind(picture, alpha, radius=3)
    assert mask[box].all()
    grown = mask.sum() - alpha.sum()
    assert grown > 0  # grown a little past the object
    assert not mask[:20].any() and not mask[:, :60].any()  # but not everywhere
    assert np.array_equal(plate[:20], picture[:20])  # untouched outside the mask
    empty_plate, empty_mask = warp.fill_behind(picture, np.zeros_like(alpha))
    assert not empty_mask.any() and np.array_equal(empty_plate, picture)


def test_odd_padding_keeps_ramps_straight() -> None:
    ramp = np.tile(np.linspace(0, 1, 50, dtype=np.float32), (20, 1))
    padded = warp.odd_reflect_pad(ramp, 5)
    row = padded[10]
    assert np.allclose(np.diff(row), np.diff(row)[0], atol=1e-5)


def test_overscan_keeps_parallax_inside_the_picture() -> None:
    picture, depth, _box = near_square_scene()
    layers = warp.prepare_layers(picture, depth, CONFIG)
    path = MotionPath((0, 0, 320, 180), (32, 18, 256, 144), bounds=(0, 0, 320, 180))
    share = warp.overscan_for(path, layers, (320, 180), strength=0.035, frames=30, fps=30,
                              limit=0.03)
    assert 0 < share <= 0.03
    assert warp.overscan_for(path, layers, (320, 180), strength=0.0, frames=30, fps=30,
                             limit=0.03) == 0
    tight = warp.tighten((0, 0, 100, 50), 0.1)
    assert tight == pytest.approx((10, 5, 80, 40))


# Working picture and Ken Burns frames ----------------------------------------------------------


def test_working_picture_matches_the_tightest_window() -> None:
    picture = texture(2000, 1125)
    region = (0.0, 0.0, 2000.0, 1125.0)
    path = MotionPath((0, 0, 2000, 1125), (200, 112.5, 1600, 900), bounds=region)
    work = warp.working_image(picture, region, path, (854, 480))
    # The tightest window (1600 px) needs 854 working px: shrunk, never more than 1.5x.
    assert work.factor == pytest.approx(854 / 1600, rel=0.01)
    assert work.size[0] == pytest.approx(2000 * work.factor, abs=1)
    small = warp.working_image(texture(320, 180), (0, 0, 320, 180),
                               MotionPath((0, 0, 320, 180), (0, 0, 320, 180)), (640, 360))
    assert small.factor == 1.0  # never upscaled: the warp interpolates
    rect = work.rect((200, 112.5, 1600, 900))
    assert rect == pytest.approx((200 * work.factor, 112.5 * work.factor, 1600 * work.factor,
                                  900 * work.factor))


def test_ken_burns_frame_is_sub_pixel_exact() -> None:
    picture = texture(200, 100)
    whole = warp.kenburns_frame(picture, (0, 0, 200, 100), (200, 100), "lanczos")
    assert np.array_equal(whole, picture)  # identity
    shifted = warp.kenburns_frame(picture, (0.5, 0, 200, 100), (200, 100), "linear")
    expected = (picture[:, 10:-10].astype(np.float32) + picture[:, 11:-9]) / 2
    assert np.abs(shifted[:, 10:-10] - expected).max() <= 1.0


def test_motion_frames_hold_still_and_move() -> None:
    picture = texture(320, 180)
    region = (0.0, 0.0, 320.0, 180.0)
    hold = MotionPath((0, 0, 320, 180), (0, 0, 320, 180), bounds=region)
    frames = list(clip_mod.iter_motion_frames(picture, size=(160, 90), fps=30, frames=4,
                                              path=hold, config=CONFIG))
    assert len(frames) == 4 and all(f is frames[0] for f in frames)  # drawn once
    info: dict[str, object] = {}
    _picture, depth, _box = near_square_scene()
    moving = MotionPath((0, 0, 320, 180), (24, 0, 296, 166.5), bounds=region)
    drawn = list(clip_mod.iter_motion_frames(_picture, size=(160, 90), fps=30, frames=5,
                                             path=moving, style="parallax", depth=depth,
                                             config=CONFIG, info=info))
    assert len(drawn) == 5 and info["layers"] == 2
    assert not np.array_equal(drawn[0], drawn[-1])


# Depth maps on disk ----------------------------------------------------------------------------


class FakeSession:
    """Stands in for an onnxruntime session: depth = brightness."""

    def __init__(self, fixed: tuple[int, int] | None = None) -> None:
        self.fixed = fixed
        self.calls: list[tuple[int, ...]] = []

    def get_inputs(self):
        shape = [1, 3, *(self.fixed or ("height", "width"))]
        return [type("Input", (), {"name": "pixel_values", "shape": shape})()]

    def get_outputs(self):
        return [type("Output", (), {"name": "predicted_depth"})()]

    def run(self, names, feeds):
        tensor = feeds["pixel_values"]
        self.calls.append(tensor.shape)
        return [tensor.mean(axis=1)]


def test_depth_estimator_resizes_like_depth_anything_and_normalises() -> None:
    assert depth_mod.model_input_size(1080, 1920) == (518, 924)
    assert depth_mod.model_input_size(1920, 1080) == (924, 518)
    assert depth_mod.model_input_size(518, 518) == (518, 518)
    session = FakeSession()
    estimator = DepthEstimator("unused", session=session)
    picture = np.zeros((90, 160, 3), np.uint8)
    picture[45:] = 255  # bright bottom = "near" for the fake model
    depth = estimator.predict(picture)
    assert depth.shape == (90, 160) and depth.dtype == np.float32
    # A small picture is scaled as little as possible: its width goes to 518.
    assert session.calls[0] == (1, 3, *depth_mod.model_input_size(90, 160)) == (1, 3, 294, 518)
    assert depth[80].mean() > 0.9 and depth[5].mean() < 0.1
    fixed = FakeSession(fixed=(518, 518))
    DepthEstimator("unused", session=fixed).predict(picture)
    assert fixed.calls[0] == (1, 3, 518, 518)
    assert np.allclose(depth_mod.normalize_depth(np.ones((4, 4))), 0.5)


def test_depth_maps_are_made_once_and_remade_when_the_picture_changes(
    tmp_path: Path,
) -> None:
    first, second = tmp_path / "scene_01.png", tmp_path / "scene_02.png"
    Image.fromarray(texture(64, 36, seed=1)).save(first)
    Image.fromarray(texture(64, 36, seed=2)).save(second)
    estimator = DepthEstimator("unused", session=FakeSession())
    batch = depth_mod.ensure_depth_maps([first, second, first], tmp_path, estimator=estimator)
    assert batch.made == 2 and batch.reused == 0 and batch.fallback_reason is None
    saved = depth_mod.depth_map_path(first)
    assert saved.name == "scene_01.depth.png"
    with Image.open(saved) as image:
        assert image.mode.startswith("I;16") and image.size == (64, 36)
        meta = json.loads(image.info["ccs:depth"])
    assert meta["source"] == depth_mod.image_fingerprint(first)
    found = depth_mod.read_depth_map(saved)
    assert found is not None and found[0].shape == (36, 64)
    again = depth_mod.ensure_depth_maps([first, second], tmp_path, estimator=estimator)
    assert again.reused == 2 and again.made == 0
    Image.fromarray(texture(64, 36, seed=9)).save(second)  # a new picture
    third = depth_mod.ensure_depth_maps([first, second], tmp_path, estimator=estimator)
    assert third.reused == 1 and third.made == 1


def test_a_depth_map_put_there_by_a_person_is_used_as_is(tmp_path: Path) -> None:
    picture = tmp_path / "scene_04.png"
    Image.fromarray(texture(40, 30)).save(picture)
    painted = np.tile(np.linspace(0, 255, 40, dtype=np.uint8), (30, 1))
    Image.fromarray(painted).save(depth_mod.depth_map_path(picture))  # 8-bit, no metadata
    batch = depth_mod.ensure_depth_maps([picture], tmp_path / "app")
    assert batch.reused == 1 and batch.fallback_reason is None
    depth = depth_mod.depth_for_image(picture)
    assert depth is not None and depth[0, 0] == 0.0 and depth[0, -1] == pytest.approx(1.0)


# The model download (mock transport) ---------------------------------------------------------


def fake_model(size: int = 5000) -> bytes:
    return bytes(range(256)) * (size // 256) + b"x" * (size % 256)


def spec_for(blob: bytes, **changes) -> DepthModelSpec:
    values = {"url": "https://models.test/depth.onnx", "sha256": hashlib.sha256(blob).hexdigest(),
              "size_bytes": len(blob), "file": "depth.onnx"}
    values.update(changes)
    return DepthModelSpec(**values)


def client_for(blob: bytes, status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "models.test"
        return httpx.Response(status, content=blob, headers={"content-length": str(len(blob))})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_checks_the_fingerprint(tmp_path: Path, runtime_ok: None) -> None:
    blob = fake_model()
    spec = spec_for(blob)
    seen: list[tuple[int, int]] = []
    path = depth_mod.download_depth_model(tmp_path, spec=spec, client=client_for(blob),
                                          progress=lambda done, total: seen.append((done, total)))
    assert path == tmp_path / "models" / "depth.onnx" and path.read_bytes() == blob
    assert seen[-1] == (len(blob), len(blob))
    assert (tmp_path / "models" / "depth.onnx.verified.json").is_file()
    status = depth_mod.depth_model_status(tmp_path, spec=spec)
    assert status.state == "ready" and status.ready and status.size_bytes == len(blob)


def test_a_wrong_download_is_deleted(tmp_path: Path, runtime_ok: None) -> None:
    blob = fake_model()
    spec = spec_for(blob, sha256="0" * 64)
    with pytest.raises(DepthModelError, match="fingerprint"):
        depth_mod.download_depth_model(tmp_path, spec=spec, client=client_for(blob))
    assert not list((tmp_path / "models").glob("*"))
    with pytest.raises(DepthModelError, match="404"):
        depth_mod.download_depth_model(tmp_path, spec=spec_for(blob), client=client_for(b"", 404))
    (tmp_path / "models" / "depth.onnx").write_bytes(b"not a model at all")
    status = depth_mod.depth_model_status(tmp_path, spec=spec_for(blob))
    assert status.state == "invalid" and status.can_download


def test_background_download_reports_progress_and_finishes(
    tmp_path: Path, runtime_ok: None
) -> None:
    blob = fake_model(20000)
    spec = spec_for(blob)
    release = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        release.wait(5)
        return httpx.Response(200, content=blob)

    status = depth_mod.start_depth_model_download(
        tmp_path, spec=spec, client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handler)),
    )
    assert status.state == "downloading"
    assert depth_mod.start_depth_model_download(tmp_path, spec=spec).state == "downloading"
    release.set()
    final = depth_mod.wait_for_depth_model_download(tmp_path, 10, spec=spec)
    assert final.state == "ready" and final.ready


def test_a_failed_background_download_says_why(tmp_path: Path, runtime_ok: None) -> None:
    blob = fake_model()
    spec = spec_for(blob, sha256="1" * 64)
    depth_mod.start_depth_model_download(
        tmp_path, spec=spec, client_factory=lambda: client_for(blob)
    )
    final = depth_mod.wait_for_depth_model_download(tmp_path, 10, spec=spec)
    assert final.state == "failed" and "fingerprint" in final.message and final.can_download


def test_the_configured_model_is_pinned_and_checked() -> None:
    spec = depth_mod.depth_model_spec()
    assert spec.url.startswith("https://huggingface.co/onnx-community/depth-anything-v2-small/")
    assert "/resolve/main/" not in spec.url  # pinned to one revision
    assert len(spec.sha256) == 64 and spec.size_bytes > 50_000_000
    assert spec.license == "Apache-2.0" and spec.input_size % 14 == 0
    custom = depth_mod.depth_model_spec({"parallax": {"depth_model": {
        "file": "../../evil.onnx", "input_size": 9999, "sha256": "ABC"}}})
    assert custom.file == "evil.onnx" and custom.input_size == 2048 and custom.sha256 == "abc"


# onnxruntime on a tiny hand-made model ---------------------------------------------------------


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field(number: int, wire: int, payload: bytes | int) -> bytes:
    key = _varint((number << 3) | wire)
    if wire == 0:
        return key + _varint(int(payload))
    data = payload if isinstance(payload, bytes) else str(payload).encode()
    return key + _varint(len(data)) + data


def _text(number: int, value: str) -> bytes:
    return _field(number, 2, value.encode())


def _tensor_type(dims: list[int | str]) -> bytes:
    shape = b"".join(
        _field(1, 2, _field(1, 0, d) if isinstance(d, int) else _text(2, d)) for d in dims
    )
    tensor = _field(1, 0, 1) + _field(2, 2, shape)  # elem_type FLOAT, shape
    return _field(1, 2, tensor)  # TypeProto.tensor_type


def tiny_depth_model() -> bytes:
    """An ONNX model: pixel_values [1, 3, H, W] -> ReduceMean over channels ->
    predicted_depth [1, H, W] (opset 13), written as raw protobuf."""
    axes = _text(1, "axes") + _field(8, 0, 1) + _field(20, 0, 7)  # ints [1], type INTS
    keep = _text(1, "keepdims") + _field(3, 0, 0) + _field(20, 0, 2)  # i 0, type INT
    node = (_text(1, "pixel_values") + _text(2, "predicted_depth") + _text(3, "mean")
            + _text(4, "ReduceMean") + _field(5, 2, axes) + _field(5, 2, keep))
    graph = (
        _field(1, 2, node) + _text(2, "tiny")
        + _field(11, 2, _text(1, "pixel_values") + _field(2, 2,
                 _tensor_type([1, 3, "height", "width"])))
        + _field(12, 2, _text(1, "predicted_depth") + _field(2, 2,
                 _tensor_type([1, "height", "width"])))
    )
    opset = _text(1, "") + _field(2, 0, 13)
    return _field(1, 0, 8) + _text(2, "ccs-test") + _field(7, 2, graph) + _field(8, 2, opset)


def test_onnxruntime_runs_a_real_session(tmp_path: Path) -> None:
    check = depth_mod.runtime_check()
    if not check.ok:
        pytest.skip(check.message)
    model = tmp_path / "tiny.onnx"
    model.write_bytes(tiny_depth_model())
    picture = np.zeros((72, 128, 3), np.uint8)
    picture[:, 64:] = 255
    with DepthEstimator(model, threads=1, input_size=28) as estimator:
        assert estimator.fixed_size is None and estimator.input_name == "pixel_values"
        depth = estimator.predict(picture)
    assert depth.shape == (72, 128)
    assert depth[:, 100:].mean() > 0.9 and depth[:, :30].mean() < 0.1


# The writer -----------------------------------------------------------------------------------


@requires_ffmpeg
def test_writer_produces_a_decodable_bt709_clip(render_tmp: Path) -> None:
    frames = [np.full((48, 64, 3), (40 + 20 * k, 120, 200 - 10 * k), np.uint8) for k in range(6)]
    info = write_clip(frames, render_tmp / "c.mp4", 64, 48, 30, x264_preset="ultrafast")
    assert info.frames == 6 and info.path.is_file() and info.size_bytes > 0
    assert not (render_tmp / "c.mp4.part").exists()
    assert probe_frames(info.path) == (6, 64, 48)
    decoded = decode(info.path, 64, 48)
    assert decoded.shape[0] == 6
    for k in (0, 5):
        error = np.abs(decoded[k].astype(int) - frames[k].astype(int)).mean()
        assert error < 4.0  # colours survive the BT.709 round trip


@requires_ffmpeg
def test_writer_cancel_and_errors_leave_no_clip(render_tmp: Path) -> None:
    cancel = threading.Event()
    writer = ClipWriter(render_tmp / "c.mp4", 32, 24, 30, cancel=cancel, x264_preset="ultrafast")
    with pytest.raises(RenderCancelled):
        with writer:
            writer.write(np.zeros((24, 32, 3), np.uint8))
            cancel.set()
            writer.write(np.zeros((24, 32, 3), np.uint8))
    assert not (render_tmp / "c.mp4").exists() and not (render_tmp / "c.mp4.part").exists()
    with pytest.raises(ValueError):
        with ClipWriter(render_tmp / "d.mp4", 32, 24, 30) as bad:
            bad.write(np.zeros((10, 10, 3), np.uint8))
    assert not (render_tmp / "d.mp4.part").exists()
    with pytest.raises(RenderError):
        ClipWriter(render_tmp / "e.mp4", 32, 24, 30).close()


@requires_ffmpeg
def test_parallax_clip_with_a_depth_map(render_tmp: Path) -> None:
    picture, depth, _box = near_square_scene()
    image = render_tmp / "scene_02.png"
    Image.fromarray(picture).save(image)
    depth_mod.write_depth_map(depth_mod.depth_map_path(image), depth, {"source": "x"})
    seen: list[int] = []
    result = clip_mod.render_motion_clip(
        image, render_tmp / "p.mp4", size=(160, 90), fps=30, frames=10,
        start_rect=[0, 0, 320, 180], end_rect=[24, 0, 296, 166.5], preset="pan_right",
        style="parallax", strength=0.05, x264_preset="ultrafast",
        progress=lambda done, total: seen.append(done),
    )
    assert result.style == "parallax" and result.fallback_reason is None and result.layers == 2
    assert seen == list(range(1, 11))
    assert probe_frames(result.path)[0] == 10
    assert clip_mod.depth_fingerprint(image) != ""
