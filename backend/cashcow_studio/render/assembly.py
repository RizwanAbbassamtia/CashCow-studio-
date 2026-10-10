"""Assembly: scene clips + transition clips -> the finished video (TRANSITIONS-V2-CONTRACT
section 3).

Frame layout (all in whole frames of the timeline's fps, the first scene starting at 0):

* scene ``k`` runs from its start to the next scene's start (the last one to its end):
  ``len_k`` frames;
* the cut between scenes ``i`` and ``j`` is ``n`` frames centred on the boundary ``C``:
  ``h = n // 2`` frames before it and ``n - h`` after, so the change sits on the narration's
  cut point; ``n = round(duration * fps)`` (0 below ``transitions.min_frames``) and never more
  than half the shorter scene;
* the last scene ends with an ``n_f``-frame fade to black (white for ``dip_white``) inside
  its own frames;
* scene clip ``k`` holds ``h_in`` frames before the scene, the scene, and ``n_out - h_out``
  frames after it. Its first ``n_in`` frames feed the incoming cut, its last ``n_out`` (or
  ``n_f``) the outgoing one, the rest is the body.

Rendering runs in three phases (progress 55 / 15 / 30 %): scene clips (two at a time,
cached by hash), transition clips (the overlap frames decoded through a pipe, the
transition drawn in Python, encoded through a pipe; cached by the hash of both scene clips
and the transition), then one FFmpeg pass: the concat demuxer reads
``[clip 1][cut 1][clip 2]...``, ``select`` drops the overlap frames (the cuts replace them),
then popups, captions, the audio mix and the preset's encoder. Cancelling kills the running
child process and keeps every finished clip. A cached clip that is not a whole MP4 (cut short
or zeroed by a crash) is removed and drawn again; one that cannot be decoded while a cut is
drawn is removed and the render stops with a plain message, so the next render redraws it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from ..models.timeline import Timeline
from . import transitions as tr
from .ffmpeg import (
    NO_WINDOW,
    RenderCancelled,
    RenderError,
    RenderPlan,
    RenderResult,
    _pump_stderr,
    _tail,
    find_ffmpeg,
    parse_progress_line,
    probe_media,
    resolve_path,
)
from .presets import RenderConfig, RenderPreset
from .scene_clips import (
    CLIPS_DIR,
    ClipEncoding,
    ClipReadError,
    FrameWriter,
    SceneClipJob,
    clip_encoding_for,
    clip_workers,
    decode_frames,
    make_scene_job,
    mp4_is_whole,
    render_scene_clip,
)

log = logging.getLogger(__name__)

CONCAT_FILE = "concat.txt"
PHASE_SHARE = {"scenes": 55.0, "cuts": 15.0, "assembly": 30.0}
CUT_ENGINE_VERSION = "cuts.1"


# Layout --------------------------------------------------------------------------------------


@dataclass
class CutJob:
    """A transition clip: the change from scene ``a`` to scene ``b`` (or, without ``b``, the
    fade at the very end). ``cut_NN`` is the transition out of scene NN."""

    number: int
    frames: int
    a: SceneClipJob
    b: SceneClipJob | None
    transition: tr.ResolvedTransition | None
    fade_colour: tuple[float, float, float] | None
    seed: int
    encoding: ClipEncoding
    key: str = ""
    reused: bool = False
    seconds: float = 0.0
    path: Path | None = None

    @property
    def label(self) -> str:
        if self.b is None:
            return "fade to " + ("white" if self.fade_colour == (1.0, 1.0, 1.0) else "black")
        return self.transition.id if self.transition else "cut"

    @property
    def file_name(self) -> str:
        return f"cut_{self.number:02d}.{self.key}.mp4"

    def make_key(self) -> None:
        """The hash of everything the cut's frames come from. The seed (the scene number)
        only counts for the transitions that use it, so inserting a scene early in the video
        does not redraw every dissolve after it."""
        payload = {
            "engine": CUT_ENGINE_VERSION,
            "a": self.a.key,
            "b": self.b.key if self.b is not None else None,
            "fade": list(self.fade_colour) if self.fade_colour else None,
            "transition": self.transition.cache_token() if self.transition else None,
            "frames": self.frames,
            "seed": self.transition.seed_key(self.seed) if self.transition else None,
            "encoding": self.encoding.token(),
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        self.key = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class Layout:
    """Where every frame of the video comes from (see the module docstring)."""

    fps: int
    size: tuple[int, int]
    clips_dir: str
    """Relative to the render's work folder: ``clips/<preset>``."""
    scenes: list[SceneClipJob] = field(default_factory=list)
    cuts: list[CutJob] = field(default_factory=list)
    order: list[SceneClipJob | CutJob] = field(default_factory=list)
    """Files in concat order."""
    drops: list[tuple[int, int]] = field(default_factory=list)
    """Frame ranges (inclusive) of the joined clips that the cuts replace."""
    total_frames: int = 0
    scene_frames: dict[int, tuple[int, int]] = field(default_factory=dict)
    """Scene number -> (first frame, frame count) in the finished video."""

    @property
    def total_s(self) -> float:
        return self.total_frames / self.fps

    @property
    def concat_path(self) -> str:
        return f"{self.clips_dir}/{CONCAT_FILE}"

    def select_filter(self) -> str:
        """``select`` + ``setpts``: the joined clips without the overlap frames, retimed."""
        retime = f"setpts=N/({self.fps}*TB)"
        if not self.drops:
            return retime
        terms = "+".join(f"between(n\\,{lo}\\,{hi})" for lo, hi in self.drops)
        return f"select='not({terms})',{retime}"

    def concat_text(self) -> str:
        lines = ["ffconcat version 1.0"]
        for item in self.order:
            name = item.path.name if item.path is not None else item.file_name
            lines.append(f"file '{name}'")
        return "\n".join(lines) + "\n"


def _image_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except (OSError, UnidentifiedImageError) as exc:
        raise RenderError(f"The picture {path} could not be read: {exc}") from exc


def cut_frames(duration_s: float, fps: int, min_frames: int, longest: int) -> int:
    frames = int(round(max(float(duration_s), 0.0) * fps))
    frames = min(frames, max(longest, 0))
    return frames if frames >= max(min_frames, 1) else 0


def plan_layout(
    timeline: Timeline,
    preset: RenderPreset,
    *,
    folder: Path,
    config: RenderConfig,
    encoding: ClipEncoding | None = None,
    final_x264: str | None = None,
) -> Layout:
    """The scene and cut jobs and the frame bookkeeping for one preset. Reads the pictures'
    sizes and dates; renders nothing."""
    scenes = list(timeline.scenes)
    if not scenes:
        raise RenderError("The timeline has no scenes.")
    folder = Path(folder)
    fps = int(timeline.fps)
    size = preset.size(timeline.aspect)
    encoding = encoding or clip_encoding_for(preset, final_x264)
    min_frames = config.transition_min_frames
    first = round(scenes[0].start_s * fps)
    starts = [round(scene.start_s * fps) - first for scene in scenes]
    ends = starts[1:] + [round(scenes[-1].end_s * fps) - first]
    lengths = [max(0, end - start) for start, end in zip(starts, ends, strict=True)]
    images: dict[int, tuple[Path, tuple[int, int]]] = {}
    for position, scene in enumerate(scenes):
        image = resolve_path(folder, scene.image)
        if not image.is_file():
            raise RenderError(f"The picture for scene {position + 1} is missing: {image}")
        images[position] = (image, _image_size(image))
    kept = [i for i, length in enumerate(lengths) if length > 0]
    if not kept:
        raise RenderError("The timeline is shorter than one frame.")

    # Cut lengths between kept neighbours, then the fade at the end.
    cut_n: dict[int, int] = {}
    for left, right in zip(kept, kept[1:], strict=False):
        longest = min(lengths[left], lengths[right]) // 2
        cut_n[left] = cut_frames(scenes[left].transition_out.duration_s, fps, min_frames,
                                 longest)
    last = kept[-1]
    fade_n = cut_frames(scenes[last].transition_out.duration_s, fps, min_frames,
                        lengths[last] // 2)

    def consumed(index: int, before: int | None) -> int:
        """Frames of scene ``index``'s own span taken by its cuts."""
        incoming = 0
        if before is not None and cut_n.get(before, 0):
            n = cut_n[before]
            incoming = n - n // 2
        outgoing = cut_n.get(index, 0) // 2 if index != last else fade_n
        return incoming + outgoing

    previous: int | None = None
    for index in kept:  # a safety net; the caps above already keep every body >= 0
        while consumed(index, previous) > lengths[index]:
            if index != last and cut_n.get(index, 0):
                cut_n[index] = 0
            elif index == last and fade_n:
                fade_n = 0
            elif previous is not None:
                cut_n[previous] = 0
            else:
                break
        previous = index

    layout = Layout(fps=fps, size=size, clips_dir=f"{CLIPS_DIR}/{preset.id}")
    jobs: dict[int, SceneClipJob] = {}
    previous = None
    for index in kept:
        n_in = cut_n.get(previous, 0) if previous is not None else 0
        h_in = n_in // 2
        is_last = index == last
        n_out = 0 if is_last else cut_n.get(index, 0)
        tail_out = n_out - n_out // 2
        frames = lengths[index] + h_in + tail_out
        tail = fade_n if is_last else n_out
        image, image_size = images[index]
        job = make_scene_job(
            number=index + 1,
            index=index,
            image=image,
            image_size=image_size,
            motion=scenes[index].motion,
            preset=preset,
            aspect=timeline.aspect,
            fps=fps,
            frames=frames,
            lead_in=h_in,
            tail_out=tail_out,
            keyframes=(n_in, frames - tail),
            config=config,
            folder=folder,
            encoding=encoding,
        )
        jobs[index] = job
        layout.scenes.append(job)
        previous = index

    # Concat order, drop ranges and the cut jobs.
    offset = 0
    out_frame = 0
    previous = None
    for index in kept:
        job = jobs[index]
        is_last = index == last
        n_in = cut_n.get(previous, 0) if previous is not None else 0
        tail = fade_n if is_last else cut_n.get(index, 0)
        layout.order.append(job)
        if n_in:
            layout.drops.append((offset, offset + n_in - 1))
        if tail:
            layout.drops.append((offset + job.frames - tail, offset + job.frames - 1))
        body = job.frames - n_in - tail
        layout.scene_frames[job.number] = (out_frame, body)
        out_frame += body
        offset += job.frames
        if is_last and fade_n:
            cut = CutJob(
                number=job.number, frames=fade_n, a=job, b=None, transition=None,
                fade_colour=tr.fade_out_colour(scenes[index].transition_out.type),
                seed=job.number, encoding=encoding,
            )
        elif not is_last and tail:
            following = jobs[kept[kept.index(index) + 1]]
            cut = CutJob(
                number=job.number, frames=tail, a=job, b=following,
                transition=tr.resolve(scenes[index].transition_out.type),
                fade_colour=None, seed=job.number, encoding=encoding,
            )
        else:
            cut = None
        if cut is not None:
            cut.make_key()
            layout.cuts.append(cut)
            layout.order.append(cut)
            offset += cut.frames
            out_frame += cut.frames
        previous = index
    layout.total_frames = out_frame
    expected = ends[-1]
    if out_frame != expected:  # pragma: no cover - the bookkeeping above guarantees it
        raise RenderError(
            f"Internal frame count mismatch ({out_frame} frames planned, {expected} in the "
            "timeline)."
        )
    return layout


# Rendering ---------------------------------------------------------------------------------


class _Reporter:
    """Thread-safe, monotonic, throttled ``Rendering <name> N%`` messages."""

    def __init__(self, callback: Callable[[str, float], None] | None, name: str) -> None:
        self.callback = callback
        self.name = name
        self.last = -1.0
        self.last_time = 0.0
        self.lock = threading.Lock()

    def __call__(self, pct: float, *, force: bool = False) -> None:
        if self.callback is None:
            return
        pct = min(100.0, max(0.0, float(pct)))
        with self.lock:
            if pct < self.last:
                return
            now = time.monotonic()
            if force or pct == 100.0 or pct >= self.last + 1.0 or now - self.last_time > 2.0:
                self.last, self.last_time = pct, now
                self.callback(f"Rendering {self.name} {int(pct)}%", pct)


def _existing(clips_dir: Path, prefix: str, note: Callable[[str], None] | None = None
              ) -> dict[str, Path]:
    """``hash -> file`` of finished clips (``<prefix>_NN.<hash>.mp4``). A clip that is not a
    whole MP4 (cut short or zeroed by a crash) is removed, so it is drawn again."""
    found: dict[str, Path] = {}
    try:
        entries = list(clips_dir.glob(f"{prefix}_*.mp4"))
    except OSError:
        return found
    for path in entries:
        parts = path.name.split(".")  # scene_NN.<hash>.mp4 (a .part.mp4 has four parts)
        if len(parts) != 3 or parts[2] != "mp4":
            continue
        if mp4_is_whole(path):
            found[parts[1]] = path
            continue
        try:
            path.unlink()
        except OSError:
            continue
        if note is not None:
            note(f"{path.name} was damaged and has been removed; it is drawn again")
    return found


def render_cut(cut: CutJob, clips_dir: Path, *, ffmpeg: str, fps: int,
               size: tuple[int, int], stop: Callable[[], bool],
               on_frame: Callable[[], None]) -> None:
    """Draw one transition clip from the overlap frames of its scene clips. A scene clip
    that cannot be decoded is removed (so the next render draws it again) and the render
    stops with a plain message."""
    started = time.monotonic()
    try:
        _draw_cut(cut, clips_dir, ffmpeg=ffmpeg, fps=fps, size=size, stop=stop,
                  on_frame=on_frame)
    except ClipReadError as exc:
        raise _damaged_clip(cut, clips_dir, exc) from exc
    cut.seconds = round(time.monotonic() - started, 2)


def _damaged_clip(cut: CutJob, clips_dir: Path, exc: ClipReadError) -> RenderError:
    owner = cut.a
    if cut.b is not None and exc.path.name == (cut.b.path or clips_dir / cut.b.file_name).name:
        owner = cut.b
    try:
        exc.path.unlink()
    except OSError:
        return RenderError(
            f"The saved clip of scene {owner.number} ({exc.path.name}) could not be read: "
            f"{exc.detail}"
        )
    return RenderError(
        f"The saved clip of scene {owner.number} could not be read ({exc.detail}), so it "
        "was removed. Render again to draw it afresh."
    )


def _draw_cut(cut: CutJob, clips_dir: Path, *, ffmpeg: str, fps: int,
              size: tuple[int, int], stop: Callable[[], bool],
              on_frame: Callable[[], None]) -> None:
    a_path = cut.a.path or clips_dir / cut.a.file_name
    n = cut.frames
    tail: deque[Any] = deque(maxlen=n)
    for frame in decode_frames(a_path, size, ffmpeg=ffmpeg, fps=fps,
                               start_frame=cut.a.frames - n, stop=stop):
        tail.append(frame)
    if not tail:
        raise RenderError(f"Scene {cut.a.number}'s clip has no frames to make cut {cut.number}.")
    while len(tail) < n:  # a clip shorter than planned: hold its first overlap frame
        tail.appendleft(tail[0])
    head = None
    if cut.b is not None:
        b_path = cut.b.path or clips_dir / cut.b.file_name
        head = decode_frames(b_path, size, ffmpeg=ffmpeg, fps=fps, count=n, stop=stop)
    out_path = clips_dir / cut.file_name
    writer = FrameWriter(out_path, size, fps, cut.encoding, ffmpeg=ffmpeg, stop=stop)
    cache: dict[str, Any] = {}
    previous_b = None
    try:
        for k in range(n):
            if stop():
                raise RenderCancelled("The render was stopped.")
            a = tail[k]
            if head is None:
                assert cut.fade_colour is not None
                frame = tr.fade_out_frame(a, cut.fade_colour, k, n, cache)
            else:
                b = next(head, None)
                if b is None:
                    if previous_b is None:
                        raise RenderError(f"Scene {cut.b.number if cut.b else '?'}'s clip has "
                                          f"no frames to make cut {cut.number}.")
                    b = previous_b
                previous_b = b
                assert cut.transition is not None
                frame = tr.render_frame(cut.transition, a, b, tr.frame_time(k, n),
                                        duration_s=n / fps, fps=fps, seed=cut.seed,
                                        cache=cache)
            writer.write(frame)
            on_frame()
        writer.close()
    except BaseException:
        writer.abort()
        raise
    finally:
        if head is not None:
            head.close()
    cut.path = out_path


@dataclass
class _Run:
    """State shared by the phases of one render."""

    name: str
    clips_dir: Path
    ffmpeg: str
    report: _Reporter
    note: Callable[[str], None]
    stop: Callable[[], bool]
    abort: threading.Event
    seconds: dict[str, float] = field(default_factory=dict)


def _scene_phase(layout: Layout, run: _Run) -> tuple[int, int]:
    started = time.monotonic()
    existing = _existing(run.clips_dir, "scene", run.note)
    pending: dict[str, list[SceneClipJob]] = {}
    reused = 0
    for job in layout.scenes:
        found = existing.get(job.key)
        if found is not None:
            job.reused = True
            job.path = found
            reused += 1
            run.note(f"scene {job.number:02d}: reused {found.name}")
        else:
            pending.setdefault(job.key, []).append(job)
    first_of = [jobs[0] for jobs in pending.values()]
    total = sum(job.frames for job in first_of)
    done: dict[str, int] = {}
    lock = threading.Lock()
    share = PHASE_SHARE["scenes"]

    def on_frames(job: SceneClipJob, frames: int) -> None:
        with lock:
            done[job.key] = min(frames, job.frames)
            fraction = sum(done.values()) / total if total else 1.0
        run.report(share * fraction)

    if first_of:
        workers = min(clip_workers(), len(first_of))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="scene-clip") as pool:
            futures = {
                pool.submit(render_scene_clip, job, run.clips_dir, ffmpeg=run.ffmpeg,
                            stop=run.stop, on_frames=partial(on_frames, job)): job
                for job in first_of
            }
            try:
                for future in as_completed(futures):
                    job = futures[future]
                    future.result()
                    path = run.clips_dir / job.file_name
                    for twin in pending[job.key]:
                        twin.path = path
                        twin.renderer = job.renderer
                    run.note(
                        f"scene {job.number:02d}: drawn in {job.seconds:.1f} s "
                        f"({job.renderer}, {job.frames} frames) -> {job.file_name}"
                    )
            except BaseException:
                run.abort.set()
                for future in futures:
                    future.cancel()
                raise
    run.report(share, force=True)
    run.seconds["scenes"] = round(time.monotonic() - started, 2)
    return reused, len(layout.scenes)


def _cut_phase(layout: Layout, run: _Run) -> tuple[int, int]:
    started = time.monotonic()
    existing = _existing(run.clips_dir, "cut", run.note)
    pending: list[CutJob] = []
    reused = 0
    twins: dict[str, list[CutJob]] = {}
    for cut in layout.cuts:
        found = existing.get(cut.key)
        if found is not None:
            cut.reused = True
            cut.path = found
            reused += 1
            run.note(f"cut {cut.number:02d} ({cut.label}): reused {found.name}")
        elif cut.key in twins:
            twins[cut.key].append(cut)
        else:
            twins[cut.key] = [cut]
            pending.append(cut)
    total = sum(cut.frames for cut in pending)
    done = 0
    lock = threading.Lock()
    base = PHASE_SHARE["scenes"]
    share = PHASE_SHARE["cuts"]

    def on_frame() -> None:
        nonlocal done
        with lock:
            done += 1
            fraction = done / total if total else 1.0
        run.report(base + share * fraction)

    if pending:
        # Cuts are independent: two at a time overlap FFmpeg's start-up with the drawing.
        workers = min(clip_workers(), len(pending))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cut-clip") as pool:
            futures = {
                pool.submit(render_cut, cut, run.clips_dir, ffmpeg=run.ffmpeg, fps=layout.fps,
                            size=layout.size, stop=run.stop, on_frame=on_frame): cut
                for cut in pending
            }
            try:
                for future in as_completed(futures):
                    cut = futures[future]
                    future.result()
                    assert cut.path is not None
                    for twin in twins[cut.key]:
                        twin.path = cut.path
                    run.note(
                        f"cut {cut.number:02d} ({cut.label}, {cut.frames} frames): drawn in "
                        f"{cut.seconds:.1f} s -> {cut.file_name}"
                    )
            except BaseException:
                run.abort.set()
                for future in futures:
                    future.cancel()
                raise
    run.report(base + share, force=True)
    run.seconds["cuts"] = round(time.monotonic() - started, 2)
    return reused, len(layout.cuts)


def _assembly_phase(plan: RenderPlan, layout: Layout, output: Path, work_dir: Path,
                    log_file: Any, run: _Run) -> int:
    started = time.monotonic()
    (run.clips_dir / CONCAT_FILE).write_text(layout.concat_text(), encoding="utf-8")
    script = work_dir / f"filters_{plan.preset.id}.txt"
    script.write_text(plan.filter_graph, encoding="utf-8")
    command = [
        run.ffmpeg,
        "-hide_banner", "-nostdin", "-loglevel", "warning", "-nostats",
        "-progress", "pipe:1", "-stats_period", "0.5", "-y",
    ]
    for item in plan.inputs:
        command.extend(item)
    command += ["-/filter_complex", str(script), *plan.output_args(), str(output)]
    log_file.write("command: " + subprocess.list2cmdline(command) + "\n")
    log_file.flush()
    try:
        process = subprocess.Popen(
            command, cwd=str(work_dir), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW,
        )
    except OSError as exc:
        raise RenderError(f"FFmpeg could not be started: {exc}") from exc
    killed = threading.Event()

    def watchdog() -> None:
        while process.poll() is None:
            if run.stop():
                killed.set()
                process.kill()
                return
            time.sleep(0.2)

    watcher = threading.Thread(target=watchdog, name=f"render-cancel-{run.name}", daemon=True)
    watcher.start()
    pump = threading.Thread(target=_pump_stderr, args=(process.stderr, log_file),
                            name=f"render-log-{run.name}", daemon=True)
    pump.start()
    base = PHASE_SHARE["scenes"] + PHASE_SHARE["cuts"]
    total_us = max(plan.total_s, 0.01) * 1_000_000
    assert process.stdout is not None
    try:
        for line in process.stdout:
            parsed = parse_progress_line(line)
            if parsed is None:
                continue
            key, value = parsed
            if key in ("out_time_us", "out_time_ms"):
                try:
                    fraction = min(0.99, max(0.0, int(value) / total_us))
                except ValueError:
                    continue
                run.report(base + PHASE_SHARE["assembly"] * fraction)
    finally:
        process.stdout.close()
        returncode = process.wait()
        watcher.join(timeout=1.0)
        pump.join(timeout=5.0)
        if process.stderr is not None:
            process.stderr.close()
    run.seconds["assembly"] = round(time.monotonic() - started, 2)
    log_file.write(
        f"exit code {returncode} after {time.monotonic() - started:.1f} s (assembly)\n"
    )
    log_file.flush()
    if killed.is_set():
        raise RenderCancelled("The render was stopped.")
    return returncode


def _prune(clips_dir: Path, layout: Layout) -> None:
    """Remove clips of this preset the current timeline no longer uses."""
    keep = {CONCAT_FILE}
    for job in layout.scenes:
        keep.add(job.path.name if job.path else job.file_name)
    for cut in layout.cuts:
        keep.add(cut.path.name if cut.path else cut.file_name)
    try:
        entries = list(clips_dir.iterdir())
    except OSError:
        return
    for path in entries:
        if path.is_file() and path.name not in keep and path.suffix in (".mp4", ".txt"):
            try:
                path.unlink()
            except OSError:
                log.info("Could not remove the old clip %s", path)


def run_pipeline(
    plan: RenderPlan,
    output: Path,
    *,
    work_dir: Path,
    log_path: Path,
    progress: Callable[[str, float], None] | None = None,
    cancel: threading.Event | None = None,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
    label: str | None = None,
) -> RenderResult:
    """Render ``plan`` to ``output`` (what ``ffmpeg.run_render`` does since v2)."""
    layout: Layout | None = plan.layout
    if layout is None:
        raise RenderError("The render plan has no scene layout.")
    ffmpeg = ffmpeg or find_ffmpeg()
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    clips_dir = work_dir / layout.clips_dir
    clips_dir.mkdir(parents=True, exist_ok=True)
    name = label or plan.preset.id
    started = time.monotonic()
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    abort = threading.Event()

    def stop() -> bool:
        return abort.is_set() or (cancel is not None and cancel.is_set())

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_lock = threading.Lock()
    with log_path.open("a", encoding="utf-8") as log_file:

        def note(line: str) -> None:
            with log_lock:
                log_file.write(line + "\n")
                log_file.flush()

        log_file.write(
            f"\n===== {stamp}  render {name} -> {output.name} ({plan.width}x{plan.height}, "
            f"{plan.total_s:g} s, x264 {plan.x264_preset} crf {plan.crf}) =====\n"
        )
        log_file.write(
            f"{len(layout.scenes)} scene clips, {len(layout.cuts)} transition clips in "
            f"{layout.clips_dir} ({layout.total_frames} frames at {layout.fps} fps)\n"
        )
        log_file.flush()
        run = _Run(name=name, clips_dir=clips_dir, ffmpeg=ffmpeg,
                   report=_Reporter(progress, name), note=note, stop=stop, abort=abort)
        run.report(0.0, force=True)
        try:
            scenes_reused, scenes_total = _scene_phase(layout, run)
            if stop():
                raise RenderCancelled("The render was stopped.")
            cuts_reused, cuts_total = _cut_phase(layout, run)
            if stop():
                raise RenderCancelled("The render was stopped.")
            returncode = _assembly_phase(plan, layout, output, work_dir, log_file, run)
        except RenderCancelled:
            note(f"stopped after {time.monotonic() - started:.1f} s (finished clips are kept)")
            try:
                output.unlink()
            except OSError:
                pass
            raise RenderCancelled(f"The {name} render was stopped.") from None
        except RenderError as exc:
            note(f"failed: {exc}")
            raise RenderError(f"FFmpeg could not render {name}: {exc}") from exc
        except OSError as exc:  # a full disk, a folder that went away ...
            note(f"failed: {exc}")
            raise RenderError(
                f"Could not render {name}: a file could not be written ({exc})."
            ) from exc
        except MemoryError as exc:
            abort.set()
            note("failed: out of memory")
            raise RenderError(
                f"The computer ran out of memory while rendering {name}. Close some programs "
                "and try again."
            ) from exc
        note(
            f"phases: scene clips {run.seconds.get('scenes', 0):.1f} s "
            f"({scenes_reused} of {scenes_total} reused), transitions "
            f"{run.seconds.get('cuts', 0):.1f} s ({cuts_reused} of {cuts_total} reused), "
            f"assembly {run.seconds.get('assembly', 0):.1f} s"
        )
    if returncode != 0:
        try:
            output.unlink()  # whatever FFmpeg wrote before it failed is not a video
        except OSError:
            pass
        detail = _tail(log_path)
        raise RenderError(
            f"FFmpeg could not render {name} (exit code {returncode}). The last lines of "
            f"{log_path.name}:\n{detail}"
        )
    _prune(clips_dir, layout)
    info = probe_media(output, ffprobe, cancel)
    run.report(100.0, force=True)
    fallbacks = sum(
        1 for job in layout.scenes
        if getattr(job.request.motion, "style", None) == "parallax" and job.renderer == "zoompan"
    )
    return RenderResult(
        path=output,
        duration_s=info.duration_s,
        size_bytes=info.size_bytes,
        seconds_taken=round(time.monotonic() - started, 1),
        width=info.width,
        height=info.height,
        scene_clips_total=scenes_total,
        scene_clips_reused=scenes_reused,
        cuts_total=cuts_total,
        cuts_reused=cuts_reused,
        phase_seconds=dict(run.seconds),
        parallax_fallbacks=fallbacks,
    )
