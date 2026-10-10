# Transitions v2 parity: the maths of every cut

The Python code is the reference: `backend/cashcow_studio/render/transitions/` (one module per
family, `registry.py` maps ids to functions) and `render/assembly.py` (where each cut sits in
the video). This page writes out the same maths so the M5 browser preview can draw identical
cuts in a shader. Contract: `docs/TRANSITIONS-V2-CONTRACT.md` sections 1 and 3. Camera moves
and parallax are in `MOTION-PARITY.md`.

Every transition is a pure function of the two frames `A` (the outgoing picture) and `B` (the
incoming one) at the same moment, the eased progress `t`, the linear progress `t_raw`, the
cut's duration `D` in seconds, its parameters and a seed. Nothing else is read.

## 1. Conventions

* Colours are display values (what the video holds, sRGB-like) in `[0, 1]`; the reference
  works on 8-bit RGB and rounds to the nearest code value.
* `uv` is the **pixel centre** in `[0, 1]`, origin at the **top-left**, `v` growing **down**:
  `u = (x + 0.5) / W`, `v = (y + 0.5) / H`. A GLSL port whose `gl_FragCoord` origin is the
  bottom-left uses `v = 1 - gl_FragCoord.y / H` (and samples textures uploaded top row first).
  Pixel positions below are `p = uv * (W, H)` (pixel centres, measured from the top-left
  edge of the frame) and `c = (W/2, H/2)` is the frame centre.
* `s = min(W, H) / 1080`: every parameter given in pixels is "pixels at 1080p" and is
  multiplied by `s`, so the 480p proxy, the 320x180 preview and a 4K master look the same.
* Linear light: `lin(c) = c^2.2`, `enc(l) = l^(1/2.2)`.
* `mix(a, b, w) = a (1 - w) + b w`; `smoothstep(e0, e1, x)` is GLSL's
  (`u = clamp((x - e0) / (e1 - e0), 0, 1); u * u * (3 - 2u)`); `clamp` is to `[0, 1]`.
* `sample(X, q)` reads frame `X` at pixel position `q` (bicubic in the reference for the
  main geometric warps, bilinear for blur passes, `crosswarp` and `directional_warp`). Outside
  the frame the picture is **mirrored at its edge** (`GL_MIRRORED_REPEAT`; OpenCV
  `BORDER_REFLECT_101`). Every transition is designed so that this only shows in the 2%
  overshoot of a push and in blur tails.
* Angles are degrees, **positive = clockwise on screen**. "Zoom by `k`" magnifies the picture
  about the centre: output pixel `p` shows `sample(X, c + (p - c) / k)`. "Turn by `theta`":
  output pixel `p` shows `sample(X, c + Rot(-theta) (p - c))` with, in y-down coordinates,
  `Rot(a) = [[cos a, -sin a], [sin a, cos a]]` (a positive `a` turns clockwise on screen).
  Combined (zoom `k`, turn `theta`): `sample(X, c + Rot(-theta) (p - c) / k)`.

## 2. Where a cut sits, and its progress

All counts are whole frames at the timeline's `fps` (30), the first scene starting at 0.

* Scene `k` covers frames `[start_k, start_{k+1})` (the last one `[start_last, end_last)`);
  `start = round(start_s * fps)`. A gap between scenes belongs to the scene before it; a
  scene shorter than one frame is skipped.
* A cut lasts `n = round(duration_s * fps)` frames: `0` (a plain cut) when `n < 2`
  (`transitions.min_frames`), and never more than `floor(min(len_A, len_B) / 2)`. Its
  duration is `D = n / fps`.
* The cut is **centred on the boundary** `C` (the first frame of the incoming scene on the
  voice clock): it covers frames `[C - h, C - h + n)` with `h = floor(n / 2)`.
* Cut frame `k` (`0 <= k < n`) is drawn at the **middle of its time slot**:
  `t_raw = (k + 0.5) / n`, `t = ease(t_raw)`. So no cut frame is a plain picture; the frames
  just outside the window are. A preview showing video time `T` (at any display rate) inside
  the window that starts at `T0` uses `t_raw = clamp((T - T0 + 0.5 / fps) / D)` with `fps`
  the video's frame rate: exactly the reference's frames at the video's own frame times.
* `A` and `B` keep moving during the cut: each scene clip spans its incoming and outgoing
  overlaps and the camera path runs over the whole clip (`MOTION-PARITY.md`). Cut frame `k`
  uses `A` and `B` at that same video frame.
* The **seed** of a cut is the 1-based number of the scene it leaves (`glitch_subtle`,
  `light_leak_warm` and `film_burn` use it).
* The **end of the video**: the last scene's own transition becomes a fade to a colour over
  its last `n_f` frames (`n_f` from its duration, at most half the scene): white for
  `dip_white`, the `color` parameter of `dip_black` / `dip_white`, black for anything else.
  Frame `k` is `mix(A, colour, (k + 1) / n_f)` in display values, so the final frame is the
  plain colour.
* Old FFmpeg `xfade` names in a timeline map to v2 ids before anything is drawn
  (`fade -> film_dissolve`, `wipeleft -> luma_wipe_left`, `slideleft -> push_left`,
  `zoomin -> zoom_through`, `fadeblack -> dip_black`, `fadewhite -> dip_white`, the other
  names through the `legacy:` table of `config/transitions.yaml`, anything else
  `film_dissolve`).

## 3. Easing

`t = ease(t_raw)`; the curve of each transition comes from `config/transitions.yaml`
(`easing`), else the engine default in `registry.py`.

| name | formula |
|---|---|
| `ease_in_out_cubic` | `4x^3` for `x < 0.5`, else `1 - (2 - 2x)^3 / 2` |
| `ease_out_quart` | `1 - (1 - x)^4` |
| `snap` | `16x^5` for `x < 0.5`, else `1 - (2 - 2x)^5 / 2` |
| `linear` | `x` |
| inside transitions | `ease_in_quad = x^2`, `ease_out_quad = 1 - (1 - x)^2`, `ease_out_cubic = 1 - (1 - x)^3` |

**Speed** (for motion blur): `speed = (ease(t_raw + h) - ease(t_raw - h)) / (2h)` with
`h = 0.001` (one-sided at the ends), per second `speed / D`.

**Taper** (keeps speed-driven effects off the first and last frame whatever the curve):
`taper(t) = smoothstep(0, 0.08, t) * smoothstep(0, 0.08, 1 - t)`.

## 4. Shared building blocks

* `mix_linear(A, B, w, g) = clamp(g * enc((1 - w) lin(A) + w lin(B)))` per channel.
* `gauss(X, sigma)`: Gaussian blur, standard deviation `sigma` pixels (nothing below
  0.35 px). The reference blurs large sigmas (> 3 px) on a copy area-scaled down by a power
  of two and scales it back up bilinearly; a separable or mip-mapped Gaussian of the same
  sigma matches it visually.
* `boxblur(X, L, axis)`: the mean of `X` over the centred segment of `round(L) | 1` pixels
  along one axis (a shutter open while the picture travels `L` pixels). Nothing when
  `round(L) < 2`.
* `zoomblur(X, k, theta, span, dtheta)`: the mean of `N` samples, `j = 0 .. N-1`, of `X`
  zoomed by `k q^j` and turned by `theta + j d`, with `q = (1 + span)^(1 / (N - 1))`,
  `d = dtheta / (N - 1)`. `N` keeps neighbouring samples at most 2 pixels apart where they
  spread furthest, the frame corner (fewer samples show as separate ghost copies of sharp
  detail instead of a smooth streak):
  ```
  travel = R |span| + R |dtheta| pi / 180          R = half the frame diagonal, in pixels
  N = 1 (no blur) when travel < 1, else 2^ceil(log2(travel / 2 + 1)), at most 128
  ```
  (at 1080p the peak of `zoom_through` gives `N = 128`, of `spin_zoom` 64). The reference
  averages by halving: copy + copy moved by `(q, d)`, then the result + itself moved by
  `(q^2, 2d)`, then by `(q^4, 4d)` ..., `log2(N)` bilinear warps in all. A shader may loop
  over the `N` samples directly. Because the blur is linear, a transition that blurs two
  pictures with the same `span` and `dtheta` may mix them first and blur once (the
  reference does).

## 5. The transitions

`t` is eased progress. Parameter names are those of `config/transitions.yaml`; the engine
also accepts the aliases in brackets. Defaults are the engine's.

### film_dissolve

```
out = mix_linear(A, B, t, 1 + luma_lift * sin(pi t))          luma_lift = 0.03
```
Black to white at `t = 0.5` gives `0.5^(1/2.2) * 1.03 = 0.752` (a plain crossfade: 0.5).

### dip_black, dip_white

```
C = color (black / white)        h0 = 0.5 - hold/2,  h1 = 0.5 + hold/2      hold = 0.08
t <  h0:  out = mix(A, C, (t / h0)^2)
t <= h1:  out = C
t >  h1:  out = mix(C, B, 1 - (1 - (t - h1) / (1 - h1))^2)
```

### blur_dissolve

```
sigma = max_blur_px * s                                         max_blur_px = 24 [blur_px]
out = mix_linear(gauss(A, sigma t), gauss(B, sigma (1 - t)), t, 1)
```

### zoom_through

```
kA = 1 + (a_scale_end - 1) t                                    a_scale_end = 1.25
kB = b_scale_start + (1 - b_scale_start) t                      b_scale_start = 1.15
m  = smoothstep(0.5 - w/2, 0.5 + w/2, t)                        crossfade_window w = 0.4
span = 0.12 sin(pi t)       (radial_blur: true; false -> 0; a number -> that peak span)
out = mix(zoomblur(A, kA, 0, span, 0), zoomblur(B, kB, 0, span, 0), m)
```
Every sample magnifies, so the frame edge is never shown.

### push_left, push_right, push_up, push_down

Both pictures travel as one strip; `p` is how far, in frame lengths.
```
c chosen so that max over t of (t + c t^2 (1 - t)) = 1 + overshoot    overshoot = 0.02
    (overshoot 0: p = t; overshoot 0.02: c = 1.4754, the peak 1.02 at t = 0.914)
p = t + c t^2 (1 - t)
push_left:  q = x + p W;   out = q < W ? A(q, y) : B(q - W, y)        (B enters from the right)
push_right: q = x + (1 - p) W;  out = q < W ? B(q, y) : A(q - W, y)  (B enters from the left)
push_up:    q = y + p H;   out = q < H ? A(x, q) : B(x, q - H)        (B rises from below)
push_down:  q = y + (1 - p) H;  out = q < H ? B(x, q) : A(x, q - H)  (B drops from above)
```
Positions past the strip (the overshoot) mirror back into `B`. Sampling is continuous
(bicubic across the seam). `ease_out_quart` starts at full speed (a 0.5 s push at 30 fps
moves 15%, 31% and 25% of the frame in its first three frames), so the strip is smeared
along its travel like a film camera with a 180-degree shutter would smear it. This is the
whip's blur with the strip's own speed `dp/dt` (the derivative of `p` above by `t`) and at
most a quarter of the frame:
```
len = W (left/right) or H (up/down)
L = min(motion_blur * len * |dp/dt| * speed / D * shutter_s, 0.25 len) * taper(t)
        motion_blur = 0.5 (0 turns it off), shutter_s = 1/30
out = boxblur(strip(p), L, travel axis)
```
The blur fades away as the push slows, so the 2% settle at the end is crisp.

### whip_left, whip_right, whip_up, whip_down

The push strip with `p = t` (no overshoot), smeared along the travel:
```
len = W (left/right) or H (up/down)
L = min(motion_blur * len * speed / D * shutter_s, max_blur * len) * taper(t)
        motion_blur = 1.0, shutter_s = 1/30, max_blur = 0.5
out = boxblur(strip(p), L, travel axis)
```

### luma_wipe_left, luma_wipe_right, luma_wipe_up, luma_wipe_down, luma_wipe_diag

```
G: left 1 - u | right u | up 1 - v | down v | diag (u + v) / 2
e = t (1 + f)                                                  feather f = 0.12
alpha = smoothstep(0, 1, (e - G) / f)
out = mix(A, B, alpha)
```
`G` is where `B` appears (0 first): `luma_wipe_left` uncovers `B` from the right edge, the
soft edge travelling left.

### crosswarp (after GL Transitions "crosswarp", Eke Peter, MIT)

```
x   = smoothstep(0, 1, 2t + u - 1)                  (per column: the right side goes first)
out = mix(sample(A, c + (p - c)(1 - x)), sample(B, c + (p - c) x), x)
```

### directional_warp (after GL Transitions "directionalwarp", pschroen, MIT)

`direction` is in **GL axes (x right, y up)**, default `(-1, 1)`; `smoothness` 0.5.
```
d0 = normalize(direction);  d0 /= |d0.x| + |d0.y|;   k = d0.x * 0.5 + d0.y * 0.5
m  = 1 - smoothstep(-smoothness, 0, d0.x u + d0.y (1 - v) - (k - 0.5 + t (1 + smoothness)))
out = mix(sample(A, c + (p - c)(1 - m)), sample(B, c + (p - c) m), m)
```
With the default direction the change starts in the bottom-right corner. The reference
computes `m` on a quarter-size grid of the same pixel centres and scales it up bilinearly
(`m` is a ramp half the frame wide).

### light_leak_warm

Two soft warm blobs of light drift across a linear-light crossfade, screen-blended.
```
t' = t^(ln 0.5 / ln peak_t)                                     peak_t = 0.5
I  = strength * sin(pi t')^2                                    strength = 0.85 [intensity]
P  = (u * W/H, v)                                               (frame heights)
mirror the x of both centres (cx -> 1 - cx) when the seed is odd
c1 = (-0.15 + 1.1 t, 0.30 + 0.10 sin(2 pi t)),  r1 = 0.45
c2 = ( 1.10 - 0.9 t, 0.85 - 0.35 t),            r2 = 0.35
gi = exp(-|P - (ci.x * W/H, ci.y)|^2 / (2 ri^2))
L  = clamp(I (g1 C1 + 0.8 g2 C2 + 0.06 C1))      C1 = color (255, 176, 96), C2 = (255, 92, 31)
base = mix_linear(A, B, t, 1)
out  = base + L (1 - base)                                      (screen, display values)
```
The reference evaluates `L` on a quarter-size grid and scales it up bilinearly (it is
smooth); an exact per-pixel evaluation differs by less than one code value.

### film_burn

The first picture burns away behind a ragged glowing edge into warm white, then the second
picture comes up out of the white.
```
F = 0.7 u + 0.3 fbm(u, v; cells 3, octaves 4, seed)              (the burn order, section 6)
WARM = color (255, 240, 220), HOT = edge_color (255, 140, 40), w = 0.06
t <  0.5:  s = 2t;  e = -w + s (1 + 2w);  z = (e - F) / w
           burnt = smoothstep(0, 1, z)
           char  = max(0, 1 - ((z + 1.6) / 0.8)^2)^2 * 0.5 smoothstep(0, 0.15, s)
           glow  = max(0, 1 - z^2)^2
           out = mix(A, WARM, burnt) * (1 - char) + 0.9 glow HOT     (clamped)
t >= 0.5:  s = 2t - 1;  out = mix(WARM, B, 1 - (1 - s)^3)
```

### glitch_subtle

A few frames of digital break-up on `A`, then a clean cut. Timing counts frames of a 30 fps
reference clock on **linear** progress; the strength follows the eased `t`.
```
N   = 30 D                                      (reference frames in the cut)
G   = lo + floor(rand(0, 99, seed) (hi - lo + 1))              glitch_frames [lo, hi] = [6, 8]
cut = min(1, G / N)
t_raw >= cut:  out = B
otherwise:  k   = min(floor(t_raw N), G - 1)                     (the glitch state)
            env = sqrt(min(1, t / ease(cut)))
            split = round(max_shift_px s env (0.6 + 0.4 rand(k, 0, seed)))   max_shift_px = 6
            out.r = A.r shifted right by split, out.g = A.g, out.b = A.b shifted left by split
            bands: lo_s + floor(rand(k, 1, seed) (hi_s - lo_s + 1))          slices = [3, 5]
            band i: top = floor(rand(k, 2+3i, seed) H),
                    height = max(2, floor((0.02 + 0.08 rand(k, 3+3i, seed)) H)),
                    shift = round((2 rand(k, 4+3i, seed) - 1) * 40 s env) pixels sideways
Shifts wrap round the frame edge; bands apply one after another to the split frame.
```

### spin_zoom

```
thetaA = max_angle_deg t                       kA = 1 + (a_scale_end - 1) t
thetaB = b_angle_start_deg (1 - t)             kB = 1 + (1.3 - 1) (1 - t)
bt = 3 sin(pi t),  bs = 0.05 sin(pi t)         (the blur's extra turn and scale)
cover(a) = cos|a| + max(W/H, H/W) sin|a|       (|a| at most 89 degrees)
kX = max(kX, cover(thetaX), cover(thetaX + bt))          (the frame stays covered, even in
                                                          the blur's last sample)
m  = smoothstep(0.35, 0.65, t)
out = mix(zoomblur(A, kA, thetaA, bs, bt), zoomblur(B, kB, thetaB, bs, bt), m)
        max_angle_deg = 12, a_scale_end = 1.4, b_angle_start_deg = -8
```
`clockwise: false` flips the sign of every angle (`bt` too).

### morph_blend

The luma difference of the two pictures pushes pixels along the local structure while they
crossfade (a soft "morph" without optical flow).
```
LA, LB = luma 0.299 R + 0.587 G + 0.114 B of A and B on a quarter-size (area-averaged) copy,
         Gaussian-smoothed by 12 s pixels (full-size pixels)
S = LA + LB;  g = (dS/dx, dS/dy) from 3x3 Sobel / 8 (per quarter-size pixel)
V = max_displacement_px * s * (LB - LA) * g / sqrt(|g|^2 + 0.02^2)   (scaled up to full size)
                                                          max_displacement_px = 18 [max_px]
out = mix(sample(A, p + V t), sample(B, p - V (1 - t)), t)
```
Identical pictures give `V = 0`: they pass through untouched.

### cube_left, cube_right

Two faces of a box turning a quarter turn about its vertical axis, seen by a pinhole camera.
Units are frame heights; the face is `a x 1` with `a = W / H` and the box is `a` deep.
```
z0 = (2.5 - 1.5 perspective) max(a, 1)        (perspective = 0.7 -> 1.45 max(a, 1))
phi = +(pi/2) t for cube_left, -(pi/2) t for cube_right
centre C = (0, 0, z0 + a/2 + 0.35 max(a, 1) sin(pi t))
rotate (x, z) -> (x cos phi + z sin phi, -x sin phi + z cos phi), then add C
face corners (TL, TR, BR, BL), halves of (a, 1, a) from the box centre:
  A (front):            (-1,-1,-1) (1,-1,-1) (1,1,-1) (-1,1,-1), normal (0,0,-1)
  B, cube_left (right): (1,-1,-1) (1,-1,1) (1,1,1) (1,1,-1),     normal (1,0,0)
  B, cube_right (left): (-1,-1,1) (-1,-1,-1) (-1,1,-1) (-1,1,1), normal (-1,0,0)
project: X = W/2 + z0 x / z * H,  Y = H/2 + z0 y / z * H
```
A face is drawn when its rotated normal points at the camera (`n . face centre < 0`), its
picture mapped onto the projected quad (a homography from the picture's corners), darkened by
`1 - shade (1 - cos)` with `cos = -n . centre / |centre|` and `shade = 0.45`. The background
is black. At `t = 0` face `A` fills the frame exactly.

## 6. Noise and hash

GPU `sin`-based hashes differ between devices; the integer hash below does not (WebGL2 has
`uint`).
```
hash_u32(x, y, seed) (all uint32, wrapping):
    h = x * 0x8DA6B343 ^ y * 0xD8163841 ^ seed * 0x9E3779B1
    h ^= h >> 13;  h *= 0x5BD1E995;  h ^= h >> 15
hash01 = float(h >> 8) / 16777216                 (in [0, 1))
rand(step, slot, seed) = hash01(step, slot, seed)
```
Value noise with `cells` lattice cells across the frame **height** (square cells):
```
P = (u * W/H * cells, v * cells);  i = floor(P);  f = P - i;  w = f * f * (3 - 2f)
n = mix(mix(h(i), h(i + (1,0)), w.x), mix(h(i + (0,1)), h(i + (1,1)), w.x), w.y)
    with h(j) = hash01(j.x, j.y, seed)
fbm = sum over octaves o = 0..3 of 0.5^o * noise(cells * 2^o, seed + 1013 o) / sum of 0.5^o
```

## 7. Matching the reference

Parity tests should compare against frames from `registry.render_frame(id, A, B, t_raw,
duration_s=D, seed=...)`. Expected agreement: within 2 code values for dissolves, dips and
wipes; about 6 for the blurs (a different Gaussian approximation) and the light leak; one
pixel of position for the geometric ones (pushes, whips, zooms, spins, warps, cube). Every
transition equals `A` at `t = 0` and `B` at `t = 1` exactly.
