# Transitions v2 + Motion v2 contract: cinematic cuts between every picture

Owner's brief (2026-10-10): "add high quality transition between each pic". Today every cut is an
FFmpeg `xfade` (fade, wipe, slide, zoom, pixelate ...). This wave replaces that with a cinematic
transition engine, adds 2.5D parallax camera moves, restructures rendering so each scene is cached
as its own clip (fast re-renders), and gives the storyboard AI a curated, styled library to choose
from. The browser preview engine of M5 must be able to reproduce every transition exactly, so each
one is defined by simple, documented maths.

## 0. Rules

1. **Identical on both sides.** Every transition and motion is a pure function of progress
   `t in [0, 1]` (after easing) over two frames A and B (and optional depth maps). The Python
   implementation is the reference; `docs/design/TRANSITION-PARITY.md` documents the maths so the
   M5 preview engine can mirror it in a shader.
2. **CPU only, laptop budget.** A 1080p transition of 0.6 s (18 frames) must compute in under 3 s;
   parallax must cost at most 2x a plain Ken Burns scene. Measured in the smoke test.
3. **No regressions.** Proxy, presets 720p/1080p/2160p, captions, popups, music ducking, gates,
   progress and cancellation keep working. Old `xfade` names in existing timeline.json files map
   to v2 equivalents (`fade -> film_dissolve`, `wipeleft -> luma_wipe_left`, `slideleft ->
   push_left`, `zoomin -> zoom_through`, `fadeblack -> dip_black`, `fadewhite -> dip_white`,
   anything else -> `film_dissolve`).
4. **Offline and deterministic.** No external assets; light leaks and burns are procedural.
   The depth model is optional and downloaded on first use; without it parallax falls back to
   eased Ken Burns and says so in the review payload.

## 1. Transition library (`config/transitions.yaml` v2, `render/transitions/`)

Each entry: `id, label, pack, energy (calm|medium|high), duration_s, easing (ease_in_out_cubic|
ease_out_quart|linear|snap), params {...}, preview: assets path`. Packs: `cinematic` (default),
`clean`, `dynamic`. Channels choose a pack (`images.transition_pack`, default `cinematic`); the
storyboard AI only picks from that pack. The library:

| id | pack(s) | energy | what it does (reference maths in parity doc) |
|---|---|---|---|
| film_dissolve | all | calm | gamma-correct crossfade (blend in linear light) with a 3% luma lift at mid-point |
| dip_black / dip_white | all | calm | A fades to black/white over first half (ease-in), B fades in over second half |
| blur_dissolve | cinematic, clean | calm | A gains Gaussian blur 0->24 px while fading, B comes from 24->0 px |
| zoom_through | cinematic, dynamic | medium | A scales 1.0->1.25 around centre with radial motion blur; B starts at 1.15->1.0; crossfade in the middle 40% |
| push_left/right/up/down | clean, dynamic | medium | both frames translate as one strip, ease_out_quart, 2% overshoot settle |
| whip_left/right/up/down | dynamic | high | fast directional slide with directional motion blur proportional to velocity, 0.35 s default |
| luma_wipe_left/right/up/down/diag | clean, cinematic | medium | soft-edged wipe driven by a gradient map with 12% feather |
| crosswarp | cinematic, dynamic | medium | GL-transitions "crosswarp": displacement of both frames by luma-weighted progress |
| directional_warp | dynamic | medium | GL-transitions "directionalwarp" with smooth edge |
| light_leak_warm | cinematic | medium | crossfade plus an additive procedural light leak (two moving blurred warm blobs, screen blend, peak at t=0.5) |
| film_burn | cinematic | high | dip to warm white with a burning edge that sweeps across (noise-threshold mask) |
| glitch_subtle | dynamic | high | 6-8 frames of RGB channel split (max 6 px) and 3-5 horizontal slice offsets, then clean cut |
| spin_zoom | dynamic | high | A rotates up to 12 deg and scales 1.0->1.4 with blur, B unspins from -8 deg |
| morph_blend | cinematic | calm | luma-difference displacement (max 18 px) with crossfade, gives a soft morph feel |
| cube_left/right | dynamic | medium | perspective warp of A and B as two faces of a rotating cube |

Defaults: `cinematic` duration 0.6-0.8 s, `clean` 0.5 s, `dynamic` 0.3-0.45 s. Shorts multiply by 0.6.

Previews: `tools/make_transition_previews.py` renders every transition between two bundled
placeholder frames (gradient A, gradient B, each with a simple shape so motion is visible) to
`frontend/public/transitions/<id>.webp` (animated, 320x180, 24 frames) and
`frontend/public/transitions/catalogue.json` (the YAML as JSON with preview URLs). Both are
committed; the front end reads the static catalogue (no new API route).

## 2. Motion v2 (`render/motion.py`, `render/parallax/`)

- Ken Burns gets eased interpolation (`ease_in_out_cubic`) between `start_rect` and `end_rect`
  instead of linear; sub-pixel accurate (`cv2.warpAffine` with `INTER_LANCZOS4` or FFmpeg
  `zoompan` when parallax is off, both producing the same path).
- Presets gain `drift` (very slow pan), `tilt_up/down`, `dolly_in/out` (zoom with slight pan),
  `handheld` (tiny smoothed noise on position, amplitude 0.4%); every preset has an `energy`.
- 2.5D parallax (`images.motion_style: "parallax"`): depth from Depth Anything V2 **Small**
  (Apache-2.0) via `onnxruntime` CPU, model file cached under `<app_data_dir>/models/` after a
  one-time download (URL in `config/render.yaml`; sha256 checked). Depth maps saved next to the
  image as `scene_NN.depth.png` (16-bit). Per-frame layered displacement: pixels move with the
  camera path scaled by `(depth - mean_depth) * strength` (default strength 0.035 of width);
  disocclusions filled by edge-extend inpainting (`cv2.inpaint` TELEA, radius 3) on the mask of
  stretched areas; frames written to a per-scene clip. Preview engine parity: the same
  displacement formula on a 64x36 mesh.
- Continuity rules in the timeline builder: alternate directions; a `zoom_through` cut starts
  from a scene ending zoomed in and lands on a scene starting wide; whips follow the pan direction
  of the outgoing scene.

## 3. Render pipeline restructure (`render/ffmpeg.py`, new `render/assembly.py`, `render/scene_clips.py`)

1. **Scene clips:** each scene is rendered once per preset to `07_edit/clips/<preset>/scene_NN.<hash>.mp4`
   (H.264 crf 14, yuv420p, preset fast, the scene duration plus the outgoing transition overlap
   plus the incoming overlap). The hash covers image path+mtime, motion, rects, duration, preset,
   motion style and strength. Unchanged scenes are reused on re-render (incremental).
2. **Transition clips:** for each cut, the Python engine reads the last `d` seconds of A and the
   first `d` seconds of B from the scene clips (decoded with FFmpeg to raw RGB frames via a pipe),
   computes the transition frames with numpy/OpenCV, and writes `07_edit/clips/<preset>/cut_NN.<hash>.mp4`.
3. **Assembly:** one FFmpeg pass concatenates `[A body][cut][B body]...` with the concat demuxer
   (identical codec parameters), then applies popups (`overlay` with enable windows), captions
   (`subtitles=captions.ass`), the audio mix (voice + ducked music, loudnorm) and encodes the
   preset. Proxy uses the same structure at 854x480 with ultrafast settings.
4. Progress covers all three phases (scene clips 55%, transitions 15%, assembly 30%); cancellation
   stops the current subprocess and leaves cached clips in place.
5. `render.log` lists per-scene and per-cut timings; the edit review payload shows "reused N of M
   scene clips".

## 4. AI selection v2 (`pipeline/stages/storyboard.py`, `llm/prompts/storyboard.md`, `policy`)

- The prompt receives the channel's pack with each transition's label, energy and a one-line
  feel ("whip: energetic jump between ideas"), and chooses per cut from narrative beat: calm beats
  get calm/medium, reveals get `zoom_through` or `light_leak_warm`, topic jumps get
  `push`/`whip`, section boundaries get `dip_black`/`film_burn` (at most one dip per section).
- Deterministic fix-ups after the LLM: no identical transition on adjacent cuts; at most 2 of
  the same id per 10 cuts; `light_leak_warm` and `film_burn` at most once per 60 s; energy
  `high` never twice in a row; the last cut uses the pack's closer (`dip_black`).
- `variety` in the storyboard payload reports the transition histogram; the policy gate
  `visual_variety` counts distinct transition ids (warn below 4 in a long-form video).

## 5. Front end (storyboard and edit review)

- Transition picker component (`components/storyboard/TransitionPicker.tsx`): a popover grid of
  animated previews from `/transitions/catalogue.json`, grouped by pack, showing label, energy
  chip and duration; selecting writes `transition_out.type`/`duration_s`. Used in the scene card,
  and in EditReview's scene strip (per cut).
- Scene card motion chip shows the preset name and an icon; a "Motion style: Ken Burns / 2.5D
  parallax" switch sits in the storyboard header (channel default, per-video override).
- Settings → Render gains "Transition pack" and "Motion style" defaults plus "Depth model: not
  downloaded / ready" with a Download button (calls `POST /api/settings/models/depth` — add it to
  the existing settings router, not a new router).

## 6. Tests and smoke

Unit: every transition returns frames of the right shape, equals A at t=0 and B at t=1 within
tolerance, is monotonic in blend for dissolve-type transitions, honours easing; legacy id mapping;
scene clip hashing and reuse; assembly durations equal the timeline within 1 frame; parallax falls
back without the model; depth map caching; selection fix-ups. A real 3-scene render at 480p with
`zoom_through`, `light_leak_warm` and `whip_left` (skipped only if ffmpeg is missing). Smoke:
30-scene mock project at 1080p: total time, per-phase time, reuse on a second run after editing
one scene, frames extracted at each cut for visual check.

Dependencies to add: `opencv-python-headless`, `numpy` (core); `onnxruntime` under an optional
extra `parallax` (document in README).
