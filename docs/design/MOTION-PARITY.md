# Motion v2 parity: camera moves and 2.5D parallax

The Python code is the reference (`backend/cashcow_studio/render/motion.py` and
`render/parallax/`); this page writes out the same maths so the M5 browser preview can draw
identical moves. Contract: `docs/TRANSITIONS-V2-CONTRACT.md` section 2. The cut maths is in
`TRANSITION-PARITY.md`.

## 1. Conventions

* A scene's move lives in `timeline.json` as `motion: {preset, start_rect, end_rect, style,
  strength}`. Rectangles are `[x, y, w, h]` in **pixels of the source picture**, always with
  the frame's aspect ratio and inside the *cover region* (the centred part of the picture with
  the frame's aspect, `render/geometry.py`).
* A clip of `n` frames shows frame `k` at progress `t = k / (n - 1)` (`t = 0` when `n = 1`) and
  time `k / fps` seconds. The path spans **every frame of the scene clip, transition overlaps
  included**, so the slow start and end of the easing sit under the cuts.
* Output pixel `u` has its centre at `u + 0.5`. The window `(x, w)` shows source position
  `x + (u + 0.5) * w / W` at output column `u` (the same on the vertical axis).

## 2. Easing (`E = ease(t)`, `t` clamped to `[0, 1]`)

| name | formula |
|---|---|
| `ease_in_out_cubic` (default) | `4t^3` for `t < 0.5`, else `1 - (2 - 2t)^3 / 2` |
| `ease_out_quart` | `1 - (1 - t)^4` |
| `ease_in_out_sine` (drift) | `(1 - cos(pi t)) / 2` |
| `snap` | `16t^5` for `t < 0.5`, else `1 - (2 - 2t)^5 / 2` |
| `linear` | `t` |

`ease_in_out_cubic`, `ease_out_quart`, `snap` and `linear` are the same curves as the
transition engine (`render/transitions/easing.py`).

## 3. The window at progress `t`

```
c0, c1 = centres of start and end rect       w0, w1 / h0, h1 = their sizes
E      = ease(t)
c(E)   = c0 + (c1 - c0) * E                   (straight line between the centres)
w(E)   = w0 * (w1 / w0)^E,  h(E) = h0 * (h1 / h0)^E   (even zoom rate)
rect   = (c.x - w/2, c.y - h/2, w, h), then kept inside the cover region
```

**Handheld** (`preset == "handheld"`) adds, before the clamp, a wobble of
`A * w * (sx, sy)` with `A = 0.004` and, at time `s` seconds,

```
sx = 0.55 sin(2 pi 0.23 s + p0) + 0.30 sin(2 pi 0.57 s + p1) + 0.15 sin(2 pi 1.13 s + p2)
sy = 0.55 sin(2 pi 0.31 s + p3) + 0.30 sin(2 pi 0.67 s + p4) + 0.15 sin(2 pi 1.31 s + p5)
pi_i = 2 pi * frac(0.6180339887498949 * (seed + 1) * (i + 1))      (seed = scene index)
```

## 4. Presets (fractions of the cover region, start -> end)

| id | energy | start | end | easing |
|---|---|---|---|---|
| zoom_in | medium | 0, 0, 1, 1 | 0.1, 0.1, 0.8, 0.8 | cubic |
| zoom_out | medium | 0.1, 0.1, 0.8, 0.8 | 0, 0, 1, 1 | cubic |
| pan_left | medium | 0.15, 0, 0.85, 1 | 0, 0, 0.85, 1 | cubic |
| pan_right | medium | 0, 0, 0.85, 1 | 0.15, 0, 0.85, 1 | cubic |
| pan_up | medium | 0, 0.15, 1, 0.85 | 0, 0, 1, 0.85 | cubic |
| pan_down | medium | 0, 0, 1, 0.85 | 0, 0.15, 1, 0.85 | cubic |
| hold | calm | 0, 0, 1, 1 | 0, 0, 1, 1 | cubic |
| slow_push | calm | 0, 0, 1, 1 | 0.04, 0.04, 0.92, 0.92 | cubic |
| drift | calm | 0, 0.03, 0.94, 0.94 | 0.05, 0.03, 0.94, 0.94 | sine |
| tilt_up | medium | 0.1, 0.2, 0.8, 0.8 | 0.11, 0, 0.78, 0.78 | cubic |
| tilt_down | medium | 0.1, 0, 0.8, 0.8 | 0.11, 0.22, 0.78, 0.78 | cubic |
| dolly_in | medium | 0, 0, 1, 1 | 0.13, 0.07, 0.78, 0.78 | cubic |
| dolly_out | medium | 0.13, 0.07, 0.78, 0.78 | 0, 0, 1, 1 | cubic |
| handheld | medium | 0.02, 0.02, 0.96, 0.96 | same | cubic + wobble |

The timeline builder turns fractions into pixels with `rect_in_pixels` (fit to the frame's
aspect around the same centre, kept inside the cover region), so `timeline.json` already holds
the final rectangles; the preview only interpolates them.

## 5. FFmpeg `zoompan` (same path, whole-pixel window)

With the picture scaled to a canvas `cw` wide and the rectangles in canvas pixels:
`T = min(on/(n-1), 1)`, `z = cw / (w0 * pow(w1/w0, E(T)))`,
`x = cx0 + (cx1 - cx0) * E(T) - iw/zoom/2`, `y = cy0 + (cy1 - cy0) * E(T) - ih/zoom/2`
(+ the wobble with `s = on / fps`). FFmpeg rounds its window to whole pixels; the Python path
is sub-pixel. `tests/test_motion.py` evaluates these expressions with FFmpeg itself and
checks them against the Python path within 0.5 px.

## 6. 2.5D parallax

**Camera terms** relative to the middle of the move (`E = 0.5`, no wobble), for the window at
`t`: `px, py = (c(t) - c(0.5)) / w(0.5)` (frame widths) and `zoom = ln(w(0.5) / w(t))`.

**Displacement** of a pixel on screen, in output pixels, on top of the camera move:

```
gain   = strength * W / 0.18                (strength 0.035 by default)
n      = (p - (W/2, H/2)) / (W/2)           (both axes divided by half the WIDTH)
offset = gain * (depth - mean_depth) * (n * zoom - (px, py))
```

`depth` is 0..1 with 1 = nearest (the depth map), `mean_depth` its mean over the cover
region. A pan right (`px > 0`) slides near things further left than far ones; a move in
(`zoom > 0`) spreads near things outwards. With strength 0.035 a standard pan
(`pan_left`, 0.18 frame widths) moves the nearest point 3.5 % of the width against the
farthest.

**Layers.** Near objects get their own layer so they slide over the background instead of
smearing it. The analysis runs on the depth scaled to a short side of 384 px:

1. *Envelope*: at half that size, the depth opened (erode then dilate) with a square of side
   `2 * round(0.25 * short side) + 1` (the depth padded by point reflection,
   `2 * edge - mirror`), smoothed but never below the opening, scaled back up, never above
   the depth.
2. *Front alpha*: `smoothstep(0.04, 0.12, depth - envelope)` where `depth - envelope > 0.08`
   holds over at least a 5 x 5 patch (no slivers), kept only for connected parts that touch a
   sharp depth edge (max minus min over a square of radius 1 % of the short side above 0.1),
   then snapped to the picture's edges with a grey guided filter.
3. *Back layer*: colours = the picture with the front objects filled by edge-extend
   inpainting (OpenCV TELEA, radius 3); depth = `depth - alpha * (depth - envelope)`.
4. *Front layer*: colours from `picture = a * front + (1 - a) * back` (rims un-mixed); depth =
   the object's own depth inside (max filter of 1.5 % of the short side) carried outward 8 %
   of the short side, so the object and its edge move as one piece.
5. Each frame: each layer's pixels move by `offset` computed with that layer's depth; the
   front is drawn over the back with its alpha. With no sharp near object there is one layer.

The Python reference solves the backward map `q = q0 - k (depth(q) - mean) v` on a grid every
8 px (at 1080p), two iterations, and upsamples it. The **preview** does the forward version
on a `64 x 36` mesh: every vertex of the back mesh moves by `offset` using the back depth at
the vertex, every vertex of the front mesh by `offset` using the front depth, and the front
mesh is drawn over the back with the front alpha texture. The Python renderer also tightens
each window by up to 3 % so nothing is pulled in from outside the picture
(`warp.overscan_for`); the preview may skip that.

**Fallback.** Without a depth map (`06_images/scene_NN.depth.png`, 16-bit, white = near) a
`parallax` scene is drawn as the eased Ken Burns move and the edit review says so.

## 7. Continuity rules (timeline builder)

Run in this order on unlocked scenes (a locked move or transition never changes):

1. A `zoom_through` cut (or the old `zoomin`) leaves a scene that zooms in (`w1 < 0.97 w0`;
   otherwise `zoom_out -> zoom_in`, `dolly_out -> dolly_in`, calm -> `slow_push`, else
   `zoom_in`) and lands on a scene that starts wide (`w0, h0 >= 0.93`; otherwise the zoom-in
   twin or `slow_push` / `zoom_in`).
2. A whip follows the outgoing pan: the window travelling right means `whip_left` (the
   picture keeps sliding left), down means `whip_up`. The whip turns unless locked; then the
   move turns (mirrored, or the matching pan) unless locked. Moves without a 5 % pan are left.
3. Neighbouring moves alternate direction: when two scenes move the same main way
   (x, y or zoom), the later one switches to its opposite (`pan_left <-> pan_right`,
   `tilt_up <-> tilt_down`, `zoom_in <-> zoom_out`, `dolly_in <-> dolly_out`, drift mirrored);
   if it is locked or pinned by rules 1-2 the earlier one switches instead.

## 8. The depth model

Depth Anything V2 **Small** (Apache-2.0), the ONNX export published by onnx-community on
Hugging Face, pinned to revision `4472b7362082ad9968fee890ca0f1e5aca36b93d`, file
`onnx/model.onnx`, 99,060,839 bytes, SHA-256
`afb6a5c28f3b6bf1618c6e43f02073ef9dfdc70e937502d51603e57b0a1df10c` (all in
`config/render.yaml`). Input `pixel_values` `[1, 3, h, w]`: the picture resized (bicubic)
so the side that needs the least scaling becomes 518 and both sides are multiples of 14,
`(rgb / 255 - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]`. Output `predicted_depth`
`[1, h, w]`, relative inverse depth; resized back to the picture and stretched so the 1st
and 99th percentiles become 0 and 1.

**Another model.** Any ONNX depth model with one image input and one depth output (larger =
nearer) works: put it in `<app data>/models/`, set `parallax.depth_model.file`, `url` (or
leave the file in place) and `sha256` (empty to skip the check) in `config/render.yaml`. To
export the official weights yourself: `pip install "optimum[exporters]"` then
`optimum-cli export onnx --model depth-anything/Depth-Anything-V2-Small-hf --task depth-estimation out/`
(only the Small model is Apache-2.0; Base and Large are non-commercial).
