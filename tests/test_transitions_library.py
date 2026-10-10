"""Transitions v2: the maths of every transition of the engine (render/transitions/), on
small synthetic frames. Every one must give frames of the right shape, equal the first
picture at t = 0 and the second at t = 1, be deterministic and follow its easing; the
dissolve-type ones must blend monotonically; each family is checked for what it promises
(docs/design/TRANSITION-PARITY.md). No FFmpeg needed."""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from cashcow_studio.render.transitions import common, easing, registry
from cashcow_studio.render.transitions.common import FrameContext
from cashcow_studio.render.transitions.glitch import glitch_window
from cashcow_studio.render.transitions.push_whip import push_position, strip_frame
from cashcow_studio.render.transitions.spin import cover_scale, spin_terms

W, H = 320, 180
IDS = registry.ids()


def picture(seed: int, width: int = W, height: int = H) -> np.ndarray:
    """A colourful frame with shapes and texture (never pure black)."""
    rng = np.random.default_rng(seed)
    u = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]
    v = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    img = np.zeros((height, width, 3), np.float32)
    img[..., 0] = 0.25 + 0.55 * u + 0.0 * v
    img[..., 1] = 0.3 + 0.45 * v + 0.0 * u
    img[..., 2] = 0.45 + 0.3 * np.sin(7.0 * u + 4.0 * v + seed)
    frame = (img * 255).astype(np.uint8)
    cv2.circle(frame, (width // 3 + seed * 23 % (width // 3), height // 2), height // 5,
               (240, 210, 60) if seed % 2 else (40, 80, 220), -1)
    cv2.rectangle(frame, (width // 10, height // 8), (width // 4, height // 3), (250, 250, 245), -1)
    noise = rng.integers(0, 24, (height, width, 3), dtype=np.uint8)
    return cv2.add(frame, noise)


A = picture(1)
B = picture(2)
BLACK = np.zeros((H, W, 3), np.uint8)
WHITE = np.full((H, W, 3), 255, np.uint8)


def frame(transition_id: str, t_raw: float, a: np.ndarray = A, b: np.ndarray = B,
          **kwargs: object) -> np.ndarray:
    kwargs.setdefault("duration_s", 0.6)
    return registry.render_frame(transition_id, a, b, t_raw, **kwargs)  # type: ignore[arg-type]


def diff(x: np.ndarray, y: np.ndarray) -> int:
    return int(np.abs(x.astype(np.int16) - y.astype(np.int16)).max())


# Every transition ---------------------------------------------------------------------------


@pytest.mark.parametrize("transition_id", IDS)
def test_frames_have_the_right_shape_and_exact_ends(transition_id: str) -> None:
    for t in (0.0, 0.1, 0.37, 0.5, 0.81, 1.0):
        out = frame(transition_id, t)
        assert out.shape == (H, W, 3) and out.dtype == np.uint8, (transition_id, t)
        assert out.flags["C_CONTIGUOUS"]
        assert out is not A and out is not B
    assert diff(frame(transition_id, 0.0), A) <= 1, transition_id
    assert diff(frame(transition_id, 1.0), B) <= 1, transition_id
    # The inputs are never changed in place.
    assert np.array_equal(A, picture(1)) and np.array_equal(B, picture(2))


@pytest.mark.parametrize("transition_id", IDS)
def test_frames_are_deterministic_and_change_between_the_ends(transition_id: str) -> None:
    middle = frame(transition_id, 0.5, seed=3)
    assert np.array_equal(middle, frame(transition_id, 0.5, seed=3))
    t_mid = 0.5 if transition_id != "glitch_subtle" else 0.2  # glitch has cut by mid-way
    assert diff(frame(transition_id, t_mid), A) > 10 or diff(frame(transition_id, t_mid), B) > 10


@pytest.mark.parametrize("transition_id", IDS)
def test_frames_follow_the_easing(transition_id: str) -> None:
    """The engine draws at eased progress: the same linear time with another curve gives
    another frame, and the frame equals the transition drawn directly at ``ease(t)``."""
    t_raw = 0.3 if transition_id != "glitch_subtle" else 0.15
    linear = frame(transition_id, t_raw, easing="linear")
    cubic = frame(transition_id, t_raw, easing="ease_in_out_cubic")
    assert not np.array_equal(linear, cubic), transition_id
    resolved = registry.resolve(transition_id, easing="ease_in_out_cubic")
    ctx = FrameContext(W, H, easing.ease_in_out_cubic(t_raw), t_raw, duration_s=0.6,
                       easing="ease_in_out_cubic", params=resolved.params)
    assert np.array_equal(cubic, resolved.spec.render(A, B, ctx))


@pytest.mark.parametrize("transition_id", ["film_dissolve", "blur_dissolve", "morph_blend"])
def test_dissolves_blend_monotonically(transition_id: str) -> None:
    for first, second, rising in ((BLACK, WHITE, True), (WHITE, BLACK, False)):
        means = [float(frame(transition_id, t / 20, first, second, easing="linear").mean())
                 for t in range(21)]
        ordered = means if rising else means[::-1]
        assert all(b >= a - 0.01 for a, b in zip(ordered, ordered[1:], strict=False)), means


@pytest.mark.parametrize("transition_id", [i for i in IDS if i.startswith("luma_wipe")])
def test_wipes_reveal_every_pixel_once(transition_id: str) -> None:
    previous = frame(transition_id, 0.0, BLACK, WHITE).astype(np.int16)
    for step in range(1, 21):
        current = frame(transition_id, step / 20, BLACK, WHITE).astype(np.int16)
        assert (current >= previous - 1).all(), step  # no pixel goes back to A
        previous = current
    assert (previous == 255).all()


# Family by family --------------------------------------------------------------------------


def test_film_dissolve_blends_in_linear_light_with_a_lift() -> None:
    middle = frame("film_dissolve", 0.5, BLACK, WHITE, easing="linear")
    # (0.5)^(1/2.2) * 1.03 * 255 = 191.6, a plain crossfade would give 127.5.
    assert int(middle[0, 0, 0]) in (191, 192)
    plain = frame("film_dissolve", 0.5, BLACK, WHITE, easing="linear", params={"luma_lift": 0})
    assert int(plain[0, 0, 0]) in (186, 187)


def test_dips_pass_through_the_solid_colour() -> None:
    assert (frame("dip_black", 0.5, easing="linear") == 0).all()
    assert (frame("dip_white", 0.5, easing="linear") == 255).all()
    assert (frame("dip_black", 0.5, easing="linear", params={"color": [10, 20, 30]})
            == [10, 20, 30]).all()
    # First half: only A and black; second half: only black and B.
    early = frame("dip_black", 0.25, BLACK, WHITE, easing="linear")
    late = frame("dip_black", 0.75, WHITE, BLACK, easing="linear")
    assert (early == 0).all() and (late == 0).all()
    early_mix = frame("dip_black", 0.25, WHITE, BLACK, easing="linear")
    assert 0 < int(early_mix[0, 0, 0]) < 255


def test_blur_dissolve_softens_both_pictures() -> None:
    edges = cv2.Laplacian(cv2.cvtColor(frame("blur_dissolve", 0.5), cv2.COLOR_RGB2GRAY),
                          cv2.CV_32F).var()
    plain = cv2.Laplacian(cv2.cvtColor(frame("film_dissolve", 0.5), cv2.COLOR_RGB2GRAY),
                          cv2.CV_32F).var()
    assert edges < plain / 4


def test_zoom_through_magnifies_about_the_centre_and_never_shows_the_border() -> None:
    target = np.zeros((H, W, 3), np.uint8)
    target[:] = (200, 60, 40)
    cv2.circle(target, (W // 2, H // 2), 20, (20, 200, 90), -1)
    early = frame("zoom_through", 0.2, target, target)
    assert tuple(early[H // 2, W // 2]) == (20, 200, 90)  # the centre stays put
    for t in (0.1, 0.3, 0.5, 0.7, 0.9):
        out = frame("zoom_through", t, A, B)
        assert int(out.min()) > 0  # no black from outside the picture
    # A grows: the circle covers more of the frame at t = 0.4 than at t = 0.
    def green(img: np.ndarray) -> int:
        return int(((img[..., 1] > 150) & (img[..., 0] < 100)).sum())
    assert green(frame("zoom_through", 0.4, target, BLACK + 1)) > green(target)


def test_push_moves_both_pictures_as_one_strip() -> None:
    assert push_position(0.0, 0.02) == 0.0 and push_position(1.0, 0.02) == pytest.approx(1.0)
    peak = max(push_position(i / 1000, 0.02) for i in range(1001))
    assert peak == pytest.approx(1.02, abs=1e-3)  # the 2% overshoot, then it settles
    assert push_position(0.5, 0.0) == 0.5
    mid = strip_frame(BLACK, WHITE, "left", 0.5)
    assert (mid[:, : W // 2 - 2] == 0).all() and (mid[:, W // 2 + 2:] == 255).all()
    mid = strip_frame(BLACK, WHITE, "right", 0.5)
    assert (mid[:, : W // 2 - 2] == 255).all() and (mid[:, W // 2 + 2:] == 0).all()
    mid = strip_frame(BLACK, WHITE, "up", 0.5)
    assert (mid[: H // 2 - 2] == 0).all() and (mid[H // 2 + 2:] == 255).all()
    mid = strip_frame(BLACK, WHITE, "down", 0.5)
    assert (mid[: H // 2 - 2] == 255).all() and (mid[H // 2 + 2:] == 0).all()
    # push_left: B comes in from the right.
    out = frame("push_left", 0.1, BLACK, WHITE, easing="linear")
    assert (out[:, :10] == 0).all() and (out[:, -10:] == 255).all()


def test_whips_smear_along_the_travel() -> None:
    rows = np.zeros((H, W, 3), np.uint8)
    rows[::8] = 255  # horizontal lines: a sideways smear leaves them sharp
    columns = np.zeros((H, W, 3), np.uint8)
    columns[:, ::8] = 255  # vertical lines: a sideways smear washes them out
    whip_rows = frame("whip_left", 0.5, rows, rows, easing="linear", duration_s=0.35)
    whip_cols = frame("whip_left", 0.5, columns, columns, easing="linear", duration_s=0.35)
    assert np.abs(np.diff(whip_rows.astype(np.int16), axis=0)).mean() > 50
    assert whip_cols.std() < columns.std() / 4
    up = frame("whip_up", 0.5, rows, rows, easing="linear", duration_s=0.35)
    assert up.std() < rows.std() / 4  # an upward whip smears the other way
    plain = strip_frame(A, B, "left", 0.5)
    calm = frame("whip_left", 0.5, easing="linear", duration_s=0.35, params={"motion_blur": 0})
    assert np.array_equal(calm, plain)
    # The smear follows the speed: none at the very start of an S-curve.
    assert diff(frame("whip_left", 0.02, duration_s=0.35), strip_frame(
        A, B, "left", easing.ease_in_out_cubic(0.02))) <= 1


def test_crosswarp_and_directional_warp_follow_their_fronts() -> None:
    # crosswarp: the right side of the frame changes first.
    out = frame("crosswarp", 0.3, BLACK, WHITE, easing="linear")
    assert out[:, -5:].mean() > out[:, :5].mean()
    # directional_warp with the default direction (-1, 1) starts in the bottom-right corner.
    out = frame("directional_warp", 0.3, BLACK, WHITE, easing="linear")
    assert out[-5:, -5:].mean() > 200 and out[:5, :5].mean() < 50


def test_morph_leaves_identical_pictures_alone() -> None:
    for t in (0.2, 0.5, 0.8):
        assert diff(frame("morph_blend", t, A, A), A) <= 2


def test_light_leak_adds_warm_light_in_the_middle() -> None:
    leak = frame("light_leak_warm", 0.5, easing="linear")
    plain = common.mix_linear(A, B, 0.5)
    assert leak.mean() > plain.mean() + 10
    red_gain = leak[..., 0].astype(float).mean() - plain[..., 0].mean()
    blue_gain = leak[..., 2].astype(float).mean() - plain[..., 2].mean()
    assert red_gain > blue_gain  # warm
    early = frame("light_leak_warm", 0.05, easing="linear")
    assert abs(float(early.mean()) - float(common.mix_linear(A, B, 0.05).mean())) < 3


def test_film_burn_burns_to_warm_white_then_shows_the_second_picture() -> None:
    white = frame("film_burn", 0.5, easing="linear")
    assert (white.astype(int) - [255, 240, 220]).__abs__().max() <= 1
    burning = frame("film_burn", 0.2, easing="linear")
    orange = (burning[..., 0] > 200) & (burning[..., 2] < 150)
    assert orange.any()  # the glowing edge
    left, right = burning[:, : W // 4], burning[:, -W // 4:]
    assert left.mean() > right.mean()  # it burns from left to right
    assert diff(frame("film_burn", 0.75, easing="linear"), B) > 0


def test_glitch_breaks_up_a_few_frames_then_cuts_clean() -> None:
    ctx = FrameContext(W, H, 0.0, 0.0, duration_s=0.3, params=registry.resolve("glitch_subtle")
                       .params, seed=4)
    count, total, cut = glitch_window(ctx)
    assert 6 <= count <= 8 and total == pytest.approx(9.0) and cut == pytest.approx(count / 9)
    shown_b = []
    for k in range(9):
        out = frame("glitch_subtle", (k + 0.5) / 9, duration_s=0.3, seed=4)
        shown_b.append(np.array_equal(out, B))
    assert shown_b == [False] * count + [True] * (9 - count)
    glitched = frame("glitch_subtle", (count - 0.5) / 9, duration_s=0.3, seed=4)
    assert diff(glitched, A) > 0 and not np.array_equal(glitched, B)
    # Channel split: red and blue no longer line up with green.
    assert not np.array_equal(glitched[..., 0], A[..., 0])


def test_spin_zoom_keeps_the_frame_covered() -> None:
    assert cover_scale(1920, 1080, 0) == pytest.approx(1.0)
    assert cover_scale(1920, 1080, 12) == pytest.approx(math.cos(math.radians(12))
                                                        + 16 / 9 * math.sin(math.radians(12)))
    for t in (0.05, 0.25, 0.5, 0.75, 0.95):
        assert int(frame("spin_zoom", t).min()) > 0, t


@pytest.mark.parametrize(("width", "height", "clockwise"),
                         [(1920, 1080, True), (1080, 1920, True), (1920, 1080, False)])
def test_spin_zoom_never_samples_outside_the_picture(width: int, height: int,
                                                     clockwise: bool) -> None:
    """Every sample of the spin blur (the picture turned and magnified a little further each
    time) still covers the whole frame: no mirrored edge ever shows in a corner."""
    params = {**registry.resolve("spin_zoom").params, "clockwise": clockwise}
    edges = np.float64([[-0.5, -0.5], [width - 0.5, -0.5], [width - 0.5, height - 0.5],
                        [-0.5, height - 0.5]])
    for step in range(1, 40):
        t = step / 40
        terms = spin_terms(FrameContext(width, height, t, t, params=params))
        samples = common.blur_samples(width, height, terms.blur_scale, terms.blur_degrees)
        for scale, degrees in ((terms.scale_a, terms.degrees_a),
                               (terms.scale_b, terms.degrees_b)):
            for j in range(samples):
                share = j / (samples - 1) if samples > 1 else 0.0
                matrix = common.zoom_rotate_matrix(
                    width, height, scale * (1.0 + terms.blur_scale) ** share,
                    degrees + terms.blur_degrees * share)
                inverse = cv2.invertAffineTransform(matrix)  # output pixel -> picture
                source = edges @ inverse[:, :2].T + inverse[:, 2]
                assert (source[:, 0] >= -0.5 - 1e-6).all(), (t, j)
                assert (source[:, 0] <= width - 0.5 + 1e-6).all(), (t, j)
                assert (source[:, 1] >= -0.5 - 1e-6).all(), (t, j)
                assert (source[:, 1] <= height - 0.5 + 1e-6).all(), (t, j)


def test_radial_and_spin_blur_are_smooth_streaks() -> None:
    """Enough samples that a small bright detail becomes one continuous streak, not a row of
    ghost copies (docs/design/TRANSITION-PARITY.md, ``zoomblur``)."""
    assert common.blur_samples(1920, 1080, 0.12, 0.0) == 128  # zoom_through at its peak
    assert common.blur_samples(1920, 1080, 0.05, 3.0) == 64  # spin_zoom at its peak
    assert common.blur_samples(854, 480, 0.12, 0.0) == 32  # the proxy needs fewer
    assert common.blur_samples(1920, 1080, 0.0005, 0.0) == 1  # under a pixel: no blur
    width, height = 1280, 720
    spot = np.zeros((height, width, 3), np.uint8)
    spot[559:561, 999:1001] = 255
    cx, cy = (width - 1) / 2, (height - 1) / 2
    r0 = math.hypot(999.5 - cx, 559.5 - cy)
    radii = np.linspace(r0 * 1.02, r0 * 1.10, 60)  # the middle of the streak (span 0.12)
    xs = np.float32(cx + (999.5 - cx) / r0 * radii).reshape(1, -1)
    ys = np.float32(cy + (559.5 - cy) / r0 * radii).reshape(1, -1)

    def streak(out: np.ndarray) -> np.ndarray:
        return cv2.remap(out[..., 0].astype(np.float32), xs, ys, cv2.INTER_LINEAR)

    smooth = streak(common.zoom_rotate_blur(spot, 0.12))
    assert smooth.min() >= 1.0 and smooth.min() > 0.25 * smooth.max()
    beaded = streak(common.zoom_rotate_blur(spot, 0.12, max_samples=8))  # the old look
    assert beaded.min() < 0.5


def test_push_blurs_its_fast_start_and_lands_crisp() -> None:
    columns = np.zeros((H, W, 3), np.uint8)
    columns[:, ::8] = 255  # vertical lines: a sideways smear washes them out
    rows = np.zeros((H, W, 3), np.uint8)
    rows[::8] = 255  # horizontal lines: a sideways smear leaves them alone
    crisp = {"motion_blur": 0}
    early = frame("push_left", 0.05, columns, columns, duration_s=0.5)
    assert early.std() < frame("push_left", 0.05, columns, columns, duration_s=0.5,
                               params=crisp).std() / 2
    assert np.array_equal(frame("push_left", 0.05, rows, rows, duration_s=0.5),
                          frame("push_left", 0.05, rows, rows, duration_s=0.5, params=crisp))
    up = frame("push_up", 0.05, rows, rows, duration_s=0.5)  # pushes up smear up and down
    assert up.std() < rows.std() / 2
    # The settle at the end is slow: no blur at all.
    assert np.array_equal(frame("push_left", 0.8, columns, columns, duration_s=0.5),
                          frame("push_left", 0.8, columns, columns, duration_s=0.5,
                                params=crisp))


def test_cube_turns_the_pictures_as_two_faces() -> None:
    mid = frame("cube_left", 0.5)
    assert (mid[:3, :] == 0).all() and (mid[-3:, :] == 0).all()  # pushed back: black above
    left_half, right_half = mid[:, : W // 2], mid[:, W // 2:]
    # cube_left: A turns away to the left, B (the right face) comes round.
    def closeness(img: np.ndarray, ref: np.ndarray) -> float:
        return float(np.abs(img.astype(int) - ref.astype(int)).mean())
    assert frame("cube_left", 0.02).mean() == pytest.approx(A.mean(), abs=3)
    right = frame("cube_right", 0.5)
    assert not np.array_equal(right, mid)
    assert closeness(left_half, A[:, : W // 2]) != closeness(right_half, B[:, W // 2:])


def test_pixel_sizes_scale_with_the_frame() -> None:
    small = FrameContext(854, 480, 0.5)
    big = FrameContext(3840, 2160, 0.5)
    assert small.px(24) == pytest.approx(24 * 480 / 1080)
    assert big.px(24) == pytest.approx(48.0)
    assert FrameContext(1080, 1920, 0.5).px(10) == pytest.approx(10.0)


def test_params_accept_the_catalogue_names_and_the_engine_aliases() -> None:
    ctx = FrameContext(W, H, 0.5, params={"luma_lift": 0.05, "color": "#ff8000",
                                          "glitch_frames": [6, 8], "radial_blur": "off"})
    assert ctx.num(("luma_lift", "lift"), 0.03) == 0.05
    assert ctx.num(("missing", "lift"), 0.03) == 0.03
    assert ctx.colour("color", (0, 0, 0)) == pytest.approx((1.0, 128 / 255, 0.0))
    assert ctx.span("glitch_frames", (1, 2)) == (6.0, 8.0)
    assert ctx.flag("radial_blur", True) is False
    assert common.parse_rgb([255, 176, 96], (0, 0, 0)) == pytest.approx((1.0, 176 / 255, 96 / 255))
    assert common.parse_rgb("nonsense", (0.1, 0.2, 0.3)) == (0.1, 0.2, 0.3)
