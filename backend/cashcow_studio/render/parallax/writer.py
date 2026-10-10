"""Frames to an H.264 clip through an FFmpeg ``rawvideo`` pipe.

Python draws the frames (``uint8`` RGB arrays of shape ``(height, width, 3)``); a background
thread feeds them to FFmpeg's stdin while the next frame is being drawn, so drawing and
encoding run side by side. The clip is written to ``<name>.part`` and renamed when FFmpeg
finishes cleanly, so a cancelled or failed render never leaves a half clip under the final
name (the scene-clip cache trusts file names).

Colour: the RGB frames are converted with the BT.709 matrix to limited-range ``yuv420p`` and
the file is tagged BT.709, so decoding a clip back to RGB (the transition engine does) gives
the same colours.
"""

from __future__ import annotations

import collections
import os
import queue
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..ffmpeg import NO_WINDOW, RenderCancelled, RenderError, find_ffmpeg

DEFAULT_CRF = 14
DEFAULT_X264_PRESET = "fast"
_STOP = object()


@dataclass(frozen=True)
class ClipInfo:
    path: Path
    frames: int
    width: int
    height: int
    fps: float
    size_bytes: int
    seconds: float


def encoder_args(
    *,
    crf: int = DEFAULT_CRF,
    x264_preset: str = DEFAULT_X264_PRESET,
    colour: str = "bt709",
) -> list[str]:
    """The output options every scene and cut clip shares (identical codec parameters let
    the assembly join them)."""
    return [
        "-an",
        "-vf", f"scale=out_color_matrix={colour}:out_range=tv,format=yuv420p",
        "-c:v", "libx264",
        "-preset", x264_preset,
        "-crf", str(int(crf)),
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-colorspace", colour,
        "-color_primaries", colour,
        "-color_trc", colour,
        "-color_range", "tv",
        "-movflags", "+faststart",
    ]


class ClipWriter:
    """Write frames to an MP4 clip. Use as a context manager::

        with ClipWriter(path, 1920, 1080, 30) as writer:
            for frame in frames:
                writer.write(frame)
        info = writer.info

    Leaving the block with an exception (or a set ``cancel`` flag) stops FFmpeg and removes
    the partial file; :class:`RenderCancelled` is raised for a cancel."""

    def __init__(
        self,
        output: Path | str,
        width: int,
        height: int,
        fps: float,
        *,
        crf: int = DEFAULT_CRF,
        x264_preset: str = DEFAULT_X264_PRESET,
        pix_fmt_in: str = "rgb24",
        ffmpeg: str | None = None,
        cancel: threading.Event | None = None,
        queue_frames: int = 3,
        extra_output_args: Sequence[str] = (),
    ) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("a clip needs a positive width and height")
        self.output = Path(output)
        self.partial = self.output.with_name(self.output.name + ".part")
        self.width, self.height = int(width), int(height)
        self.fps = float(fps)
        self.crf, self.x264_preset = int(crf), str(x264_preset)
        self.pix_fmt_in = pix_fmt_in
        self.ffmpeg = ffmpeg
        self.cancel = cancel
        self.queue_frames = max(int(queue_frames), 0)
        self.extra_output_args = list(extra_output_args)
        self.frames = 0
        self.info: ClipInfo | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._queue: queue.Queue[Any] | None = None
        self._feeder: threading.Thread | None = None
        self._drain: threading.Thread | None = None
        self._error: BaseException | None = None
        self._stderr: collections.deque[str] = collections.deque(maxlen=20)
        self._started = 0.0

    # Life cycle -------------------------------------------------------------------------

    def command(self) -> list[str]:
        rate = f"{self.fps:.6f}".rstrip("0").rstrip(".")
        return [
            self.ffmpeg or find_ffmpeg(),
            "-hide_banner", "-nostats", "-loglevel", "error", "-y",
            "-f", "rawvideo",
            "-pixel_format", self.pix_fmt_in,
            "-video_size", f"{self.width}x{self.height}",
            "-framerate", rate,
            "-i", "pipe:0",
            *encoder_args(crf=self.crf, x264_preset=self.x264_preset),
            *self.extra_output_args,
            "-f", "mp4",
            str(self.partial),
        ]

    def open(self) -> ClipWriter:
        if self._process is not None:
            return self
        self.output.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.partial.unlink(missing_ok=True)
        except OSError:
            pass
        self._started = time.monotonic()
        try:
            self._process = subprocess.Popen(
                self.command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=NO_WINDOW,
            )
        except OSError as exc:
            raise RenderError(f"FFmpeg could not be started for {self.output.name}: {exc}") from exc
        self._drain = threading.Thread(target=self._drain_stderr, name="clip-writer-log",
                                       daemon=True)
        self._drain.start()
        if self.queue_frames:
            self._queue = queue.Queue(maxsize=self.queue_frames)
            self._feeder = threading.Thread(target=self._feed, name="clip-writer", daemon=True)
            self._feeder.start()
        return self

    def __enter__(self) -> ClipWriter:
        return self.open()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()

    # Frames -----------------------------------------------------------------------------

    def _check(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            self.abort()
            raise RenderCancelled(f"Writing {self.output.name} was stopped.")
        if self._error is not None:
            error = self._error
            self.abort()
            raise RenderError(
                f"FFmpeg stopped while writing {self.output.name}: {error}. {self._log_tail()}"
            ) from error

    def write(self, frame: np.ndarray) -> None:
        """Queue one frame (``(height, width, 3)`` ``uint8``)."""
        if self._process is None:
            self.open()
        self._check()
        if frame.dtype != np.uint8 or frame.shape != (self.height, self.width, 3):
            raise ValueError(
                f"frame must be uint8 of shape ({self.height}, {self.width}, 3), "
                f"got {frame.dtype} {frame.shape}"
            )
        data = np.ascontiguousarray(frame)
        if self._queue is not None:
            while True:
                try:
                    self._queue.put(data, timeout=0.25)
                    break
                except queue.Full:
                    self._check()
        else:
            self._send(data)
        self.frames += 1

    def _send(self, data: np.ndarray) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise RenderError("The clip writer is not open.")
        try:
            process.stdin.write(memoryview(data).cast("B"))
        except (BrokenPipeError, OSError, ValueError) as exc:
            self._error = exc
            self._check()

    def _feed(self) -> None:
        assert self._queue is not None
        process = self._process
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            if self._error is not None:
                continue
            try:
                assert process is not None and process.stdin is not None
                process.stdin.write(memoryview(item).cast("B"))
            except (BrokenPipeError, OSError, ValueError) as exc:
                self._error = exc

    def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            for raw in process.stderr:
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    self._stderr.append(line)
        except (OSError, ValueError):
            return

    def _log_tail(self) -> str:
        lines = list(self._stderr)[-6:]
        return ("FFmpeg said: " + " | ".join(lines)) if lines else ""

    # Finishing --------------------------------------------------------------------------

    def close(self, timeout_s: float = 600.0) -> ClipInfo:
        """Finish the clip and move it to its final name."""
        if self.info is not None:
            return self.info
        if self._process is None:
            self.open()
        self._check()
        if self._queue is not None and self._feeder is not None:
            while self._feeder.is_alive():
                try:
                    self._queue.put(_STOP, timeout=0.25)
                    break
                except queue.Full:
                    self._check()
            self._feeder.join()
        process = self._process
        assert process is not None
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        deadline = time.monotonic() + timeout_s
        while process.poll() is None:
            if self.cancel is not None and self.cancel.is_set():
                self.abort()
                raise RenderCancelled(f"Writing {self.output.name} was stopped.")
            if time.monotonic() > deadline:
                self.abort()
                raise RenderError(f"FFmpeg took too long to finish {self.output.name}.")
            time.sleep(0.05)
        if self._drain is not None:
            self._drain.join(timeout=5.0)
        if self._error is not None or process.returncode != 0:
            detail = self._log_tail()
            self._remove_partial()
            raise RenderError(
                f"FFmpeg could not write {self.output.name} (exit code {process.returncode}). "
                f"{detail}".strip()
            )
        if self.frames == 0:
            self._remove_partial()
            raise RenderError(f"No frames were written to {self.output.name}.")
        os.replace(self.partial, self.output)
        self.info = ClipInfo(
            path=self.output,
            frames=self.frames,
            width=self.width,
            height=self.height,
            fps=self.fps,
            size_bytes=self.output.stat().st_size,
            seconds=round(time.monotonic() - self._started, 3),
        )
        return self.info

    def abort(self) -> None:
        """Stop FFmpeg and remove the partial file (safe to call more than once)."""
        process = self._process
        if self._queue is not None and self._feeder is not None and self._feeder.is_alive():
            self._error = self._error or RenderCancelled("aborted")
            try:
                while True:
                    self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(_STOP)
            except queue.Full:
                pass
        if process is not None and process.poll() is None:
            process.kill()
        if process is not None:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except OSError:
                pass
        if self._feeder is not None:
            self._feeder.join(timeout=5.0)
        if self._drain is not None:
            self._drain.join(timeout=2.0)
        self._remove_partial()

    def _remove_partial(self) -> None:
        try:
            self.partial.unlink(missing_ok=True)
        except OSError:
            pass


def write_clip(
    frames: Any,
    output: Path | str,
    width: int,
    height: int,
    fps: float,
    **options: Any,
) -> ClipInfo:
    """Write an iterable of frames to ``output`` in one call."""
    writer = ClipWriter(output, width, height, fps, **options)
    with writer:
        for frame in frames:
            writer.write(frame)
    assert writer.info is not None
    return writer.info
