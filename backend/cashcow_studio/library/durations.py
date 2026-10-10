"""How long a track or a video is, measured with ffprobe and cached in SQLite.

The music library and the production log show the length of every track and exported
video. ffprobe needs a tenth of a second or so per file, so each answer is kept in a table
(``music_cache`` for the music folders, ``video_cache`` for exported videos) under the
file's full path and reused for as long as the file keeps its size and modification time.
A file ffprobe cannot read is remembered as unreadable until it changes, so one broken file
does not cost a probe on every page load. When ffprobe is not installed nothing is cached
(WAV files are still measured from their header), so the lengths appear once it is.

Files that are not in the cache yet are measured a few at a time in worker threads within a
time budget per request. A file that is not done in time is shown without a length; its
probe keeps running and stores the answer, so the next page load has it.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import subprocess
import threading
import wave
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from ..render import ffmpeg as ff
from ..storage import db

log = logging.getLogger(__name__)

MUSIC_TABLE = "music_cache"
VIDEO_TABLE = "video_cache"
TABLES = frozenset({MUSIC_TABLE, VIDEO_TABLE})
PROBE_WORKERS = 4
PROBE_TIMEOUT_S = 30.0
PROBE_BUDGET_S = 10.0
"""How long one request waits for new measurements before it answers without them."""
LOOKUP_CHUNK = 500
"""Paths per ``IN (...)`` query, well under SQLite's variable limit."""

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    path TEXT PRIMARY KEY,
    mtime_ns INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    duration_s REAL,
    error TEXT,
    probed_at TEXT NOT NULL
)
"""
UPSERT_SQL = (
    "INSERT INTO {table} (path, mtime_ns, size_bytes, duration_s, error, probed_at) "
    "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET "
    "mtime_ns = excluded.mtime_ns, size_bytes = excluded.size_bytes, "
    "duration_s = excluded.duration_s, error = excluded.error, probed_at = excluded.probed_at"
)


class ProbeUnavailable(Exception):
    """The length could not be measured right now (no ffprobe, it could not start or was
    too slow); nothing is cached, so the next request tries again."""


class UnreadableMedia(Exception):
    """ffprobe ran but found no length in the file; cached until the file changes."""


Prober = Callable[[Path], float]
"""Seconds of one media file; raises :class:`UnreadableMedia` or :class:`ProbeUnavailable`."""


# Files -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FileStamp:
    """A file as the cache knows it: its key (full path) plus size and modification time."""

    path: Path
    key: str
    mtime_ns: int
    size_bytes: int


def file_key(path: Path | str) -> str:
    """The cache key: the full path, lower-cased on Windows where names ignore case."""
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def stamp_of(path: Path | str, stat: os.stat_result | None = None) -> FileStamp | None:
    """The file's stamp, or ``None`` when it is not there (moved, deleted, drive offline).

    ``stat`` saves a system call when the caller already has it (``os.DirEntry.stat()``).
    """
    try:
        info = stat if stat is not None else os.stat(path)
    except OSError:
        return None
    return FileStamp(
        path=Path(path), key=file_key(path), mtime_ns=int(info.st_mtime_ns),
        size_bytes=int(info.st_size),
    )


# Measuring ---------------------------------------------------------------------------------


def _positive_seconds(value: Any) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None  # missing or "N/A"
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def _seconds_from(data: dict[str, Any]) -> float | None:
    """The container's duration, else the longest stream's (ffprobe JSON)."""
    fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
    container = _positive_seconds(fmt.get("duration"))
    if container is not None:
        return round(container, 3)
    streams = [
        _positive_seconds(stream.get("duration"))
        for stream in data.get("streams") or []
        if isinstance(stream, dict)
    ]
    found = [seconds for seconds in streams if seconds is not None]
    return round(max(found), 3) if found else None


def ffprobe_seconds(path: Path, ffprobe: str) -> float:
    """The length of ``path`` in seconds as ffprobe reports it."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed program, our own arguments
            [
                ffprobe, "-v", "error",
                "-show_entries", "format=duration:stream=duration",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_S,
            creationflags=ff.NO_WINDOW,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeUnavailable(f"ffprobe took longer than {PROBE_TIMEOUT_S:.0f} s") from exc
    except OSError as exc:
        raise ProbeUnavailable(f"ffprobe could not be started: {exc}") from exc
    if completed.returncode != 0:
        lines = (completed.stderr or "").strip().splitlines()
        raise UnreadableMedia(lines[-1] if lines else f"ffprobe exit code {completed.returncode}")
    try:
        data = json.loads(completed.stdout or "{}")
    except ValueError as exc:
        raise UnreadableMedia("ffprobe gave no readable answer") from exc
    seconds = _seconds_from(data if isinstance(data, dict) else {})
    if seconds is None:
        raise UnreadableMedia("ffprobe found no length in the file")
    return seconds


def wav_seconds(path: Path) -> float:
    """The length of a PCM WAV file from its header (no ffprobe needed)."""
    if Path(path).suffix.lower() != ".wav":
        raise ProbeUnavailable("ffprobe is not installed, so only WAV files can be measured")
    try:
        with wave.open(str(path), "rb") as handle:
            frames, rate = handle.getnframes(), handle.getframerate()
    except (wave.Error, EOFError, OSError) as exc:
        # Python reads PCM only; ffprobe may still read a float or compressed WAV.
        raise ProbeUnavailable(f"the WAV header could not be read: {exc}") from exc
    if rate <= 0:
        raise UnreadableMedia("the WAV file has no sample rate")
    return round(frames / rate, 3)


def prober_for(settings: Any = None) -> Prober:
    """ffprobe (``FFPROBE_PATH`` in Settings, else the one on PATH); WAV headers only when
    ffprobe is not installed."""
    try:
        executable = ff.find_ffprobe(settings)
    except ff.RenderError:
        return wav_seconds
    return partial(ffprobe_seconds, ffprobe=executable)


# The cache ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class CachedLength:
    duration_s: float | None
    error: str | None = None


class DurationCache:
    """One cache table in ``<app_data_dir>/db.sqlite``. A cache never fails a request: a
    database problem only means the file is measured again."""

    _ready: set[tuple[str, str]] = set()
    _ready_lock = threading.Lock()

    def __init__(self, app_data_dir: Path | str, table: str) -> None:
        if table not in TABLES:
            raise ValueError(f"Unknown cache table {table!r}")
        self.app_data_dir = Path(app_data_dir)
        self.table = table

    def _ensure(self, force: bool = False) -> None:
        marker = (str(self.app_data_dir), self.table)
        with self._ready_lock:
            if marker in self._ready and not force:
                return
            db.ensure_table(self.app_data_dir, TABLE_SQL.format(table=self.table))
            self._ready.add(marker)

    def _select(self, keys: list[str]) -> dict[str, sqlite3.Row]:
        rows: dict[str, sqlite3.Row] = {}
        with db.connect(self.app_data_dir) as conn:
            for start in range(0, len(keys), LOOKUP_CHUNK):
                chunk = keys[start:start + LOOKUP_CHUNK]
                marks = ", ".join("?" for _ in chunk)
                query = (
                    f"SELECT path, mtime_ns, size_bytes, duration_s, error FROM {self.table} "
                    f"WHERE path IN ({marks})"
                )
                for row in conn.execute(query, chunk):
                    rows[row["path"]] = row
        return rows

    def lookup(self, stamps: Iterable[FileStamp]) -> dict[str, CachedLength]:
        """Cached lengths of the files that have not changed since they were measured."""
        wanted = {stamp.key: stamp for stamp in stamps}
        if not wanted:
            return {}
        try:
            self._ensure()
            try:
                rows = self._select(list(wanted))
            except sqlite3.OperationalError:
                self._ensure(force=True)  # the database was replaced while the app ran
                rows = self._select(list(wanted))
        except (sqlite3.Error, OSError) as exc:
            log.warning("The %s table could not be read: %s", self.table, exc)
            return {}
        found: dict[str, CachedLength] = {}
        for key, stamp in wanted.items():
            row = rows.get(key)
            if row is None:
                continue
            if int(row["mtime_ns"]) != stamp.mtime_ns or int(row["size_bytes"]) != stamp.size_bytes:
                continue  # the file changed: measure it again
            duration = row["duration_s"]
            found[key] = CachedLength(
                duration_s=float(duration) if duration is not None else None, error=row["error"]
            )
        return found

    def store(self, stamp: FileStamp, duration_s: float | None, error: str | None = None) -> None:
        params = (
            stamp.key, stamp.mtime_ns, stamp.size_bytes, duration_s, error,
            datetime.now(UTC).isoformat(timespec="seconds"),
        )
        try:
            self._ensure()
            with db.connect(self.app_data_dir) as conn:
                conn.execute(UPSERT_SQL.format(table=self.table), params)
        except (sqlite3.Error, OSError) as exc:
            log.warning("The %s table could not be written: %s", self.table, exc)


def measure(
    stamps: Iterable[FileStamp],
    cache: DurationCache,
    prober: Prober,
    *,
    budget_s: float | None = None,
) -> dict[str, float | None]:
    """``{stamp.key: seconds}`` for every file: from the cache, else measured now.

    ``None`` means unknown: unreadable, not measurable without ffprobe, or still being
    measured when the budget ran out (that answer is cached when it arrives).
    """
    unique = {stamp.key: stamp for stamp in stamps}
    known = cache.lookup(unique.values())
    lengths: dict[str, float | None] = {key: entry.duration_s for key, entry in known.items()}
    todo = [stamp for key, stamp in unique.items() if key not in known]
    if not todo:
        return lengths

    def work(stamp: FileStamp) -> float | None:
        try:
            seconds = prober(stamp.path)
        except UnreadableMedia as exc:
            cache.store(stamp, None, str(exc)[:500])
            return None
        except ProbeUnavailable:
            return None
        except Exception:  # noqa: BLE001 - one odd file must not break a library page
            log.exception("Measuring %s failed", stamp.path.name)
            return None
        cache.store(stamp, seconds)
        return seconds

    budget = PROBE_BUDGET_S if budget_s is None else max(0.0, float(budget_s))
    pool = ThreadPoolExecutor(
        max_workers=max(1, min(PROBE_WORKERS, len(todo))), thread_name_prefix="ccs-probe"
    )
    try:
        futures = {pool.submit(work, stamp): stamp.key for stamp in todo}
        done, _not_done = wait(futures, timeout=budget)
        for future in done:
            lengths[futures[future]] = future.result()
    finally:
        # Probes already running finish in the background and fill the cache; the ones not
        # started yet are dropped (the next request starts them again).
        pool.shutdown(wait=False, cancel_futures=True)
    for stamp in todo:
        lengths.setdefault(stamp.key, None)
    return lengths
