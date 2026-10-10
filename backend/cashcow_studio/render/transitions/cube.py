"""``cube_left`` / ``cube_right``: the two pictures are neighbouring faces of a cube that turns
a quarter turn about its vertical axis, seen through a pinhole camera.

Units are frame heights (frame = ``a x 1`` with ``a = W/H``); the box is ``a`` wide, 1 tall
and ``a`` deep so both faces keep the picture's shape. The camera sits at the origin
looking down +z with focal length ``z0 = distance * max(a, 1)``, so the front face at
depth ``z0`` fills the frame exactly; ``distance = 2.5 - 1.5 * perspective`` (the catalogue's
``perspective`` 0.7 gives 1.45: a stronger perspective is a closer camera).

    phi   = +-(pi/2) t                    (+ for cube_left: A leaves to the left)
    C     = (0, 0, z0 + a/2 + zoom_out * max(a, 1) * sin(pi t))   (cube centre)
    R(phi): (x, z) -> (x cos phi + z sin phi, -x sin phi + z cos phi)
    corner -> C + R(corner);  pixel = (W/2 + z0 x / z * H, H/2 + z0 y / z * H)
    a face is drawn when its normal faces the camera (n . centre < 0), its picture mapped
    onto the projected quad by a homography, darkened to 1 - shade (1 - cos) where cos is
    the angle between the face normal and the view ray to its centre.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from .common import FrameContext

# Face corners (TL, TR, BR, BL) relative to the cube centre, in units of (a, 1, a) halves.
_FRONT = ((-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1))
_RIGHT = ((1, -1, -1), (1, -1, 1), (1, 1, 1), (1, 1, -1))
_LEFT = ((-1, -1, 1), (-1, -1, -1), (-1, 1, -1), (-1, 1, 1))


def _rotate(point: tuple[float, float, float], phi: float) -> tuple[float, float, float]:
    x, y, z = point
    c, s = math.cos(phi), math.sin(phi)
    return (x * c + z * s, y, -x * s + z * c)


def _face(frame: np.ndarray, corners: tuple[tuple[int, int, int], ...],
          normal: tuple[float, float, float], phi: float, centre_z: float, z0: float,
          aspect: float, shade: float) -> np.ndarray | None:
    height, width = frame.shape[:2]
    n = _rotate(normal, phi)
    local = [(cx * aspect / 2.0, cy * 0.5, cz * aspect / 2.0) for cx, cy, cz in corners]
    world = []
    for point in local:
        x, y, z = _rotate(point, phi)
        world.append((x, y, z + centre_z))
    mid = [sum(p[i] for p in world) / 4.0 for i in range(3)]
    facing = n[0] * mid[0] + n[1] * mid[1] + n[2] * mid[2]
    if facing >= -1e-6 or any(p[2] <= 1e-3 for p in world):
        return None
    dst = np.float32(
        [[width / 2.0 + z0 * x / z * height - 0.5, height / 2.0 + z0 * y / z * height - 0.5]
         for x, y, z in world]
    )
    area = 0.5 * abs(
        sum(dst[i][0] * dst[(i + 1) % 4][1] - dst[(i + 1) % 4][0] * dst[i][1] for i in range(4))
    )
    if area < 1.0:
        return None
    src = np.float32([[-0.5, -0.5], [width - 0.5, -0.5], [width - 0.5, height - 0.5],
                      [-0.5, height - 0.5]])
    homography = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(frame, homography, (width, height), flags=cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    distance = math.sqrt(sum(c * c for c in mid))
    cosine = min(1.0, max(0.0, -facing / max(distance, 1e-9)))
    brightness = 1.0 - shade * (1.0 - cosine)
    if brightness < 0.999:
        warped = cv2.convertScaleAbs(warped, alpha=brightness)
    return warped


def cube(a: np.ndarray, b: np.ndarray, ctx: FrameContext, direction: str) -> np.ndarray:
    t = ctx.t
    if t <= 0.0:
        return a.copy()
    if t >= 1.0:
        return b.copy()
    aspect = ctx.width / ctx.height
    size = max(aspect, 1.0)
    if "perspective" in ctx.params:
        distance = 2.5 - 1.5 * min(max(ctx.num("perspective", 0.7), 0.0), 1.0)
    else:
        distance = ctx.num("distance", 1.45)
    z0 = max(distance, 0.5) * size
    lift = max(ctx.num("zoom_out", 0.35), 0.0) * size * math.sin(math.pi * t)
    shade = min(max(ctx.num("shade", 0.45), 0.0), 1.0)
    sign = 1.0 if direction == "left" else -1.0
    phi = sign * (math.pi / 2.0) * t
    centre_z = z0 + aspect / 2.0 + lift
    if direction == "left":
        side, side_normal = _RIGHT, (1.0, 0.0, 0.0)
    else:
        side, side_normal = _LEFT, (-1.0, 0.0, 0.0)
    out = np.zeros_like(a)
    for frame, corners, normal in ((a, _FRONT, (0.0, 0.0, -1.0)), (b, side, side_normal)):
        face = _face(frame, corners, normal, phi, centre_z, z0, aspect, shade)
        if face is not None:
            out = cv2.add(out, face)
    return out


def cube_left(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    return cube(a, b, ctx, "left")


def cube_right(a: np.ndarray, b: np.ndarray, ctx: FrameContext) -> np.ndarray:
    return cube(a, b, ctx, "right")
