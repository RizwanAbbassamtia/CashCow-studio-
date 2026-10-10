"""Track and video lengths for the Master Sheet: measured once with ffprobe and cached in
SQLite by path, size and modification time; unreadable files are remembered, a missing
ffprobe is not; slow probes finish in the background within a time budget."""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from cashcow_studio.library import durations
from cashcow_studio.library.durations import (
    MUSIC_TABLE,
    VIDEO_TABLE,
    DurationCache,
    ProbeUnavailable,
    UnreadableMedia,
    ffprobe_seconds,
    file_key,
    measure,
    prober_for,
    stamp_of,
    wav_seconds,
)
from cashcow_studio.render import ffmpeg as ff
from cashcow_studio.storage import db
from test_library_api import write_wav


@pytest.fixture
def cache(tmp_path: Path) -> DurationCache:
    return DurationCache(tmp_path / "app", MUSIC_TABLE)


def counting(seconds: float, calls: list[str]) -> durations.Prober:
    def prober(path: Path) -> float:
        calls.append(path.name)
        return seconds

    return prober


def test_measured_once_and_reused_until_the_file_changes(
    tmp_path: Path, cache: DurationCache
) -> None:
    track = write_wav(tmp_path / "a.wav", 1.0)
    calls: list[str] = []
    first = stamp_of(track)
    assert first is not None and first.key == file_key(track)
    assert measure([first], cache, counting(1.0, calls)) == {first.key: 1.0}
    assert measure([stamp_of(track)], cache, counting(9.0, calls)) == {first.key: 1.0}  # type: ignore[list-item]
    assert calls == ["a.wav"]

    write_wav(track, 2.0)  # a new file under the same name: new size and time
    second = stamp_of(track)
    assert second is not None and second.key == first.key
    assert measure([second], cache, counting(2.0, calls)) == {first.key: 2.0}
    assert calls == ["a.wav", "a.wav"]

    # Only the time changed (the file was replaced by one of the same size): measured again.
    os.utime(track, ns=(second.mtime_ns + 5_000_000_000,) * 2)
    third = stamp_of(track)
    assert third is not None and third.size_bytes == second.size_bytes
    assert measure([third], cache, counting(2.5, calls)) == {first.key: 2.5}
    assert len(calls) == 3


def test_unreadable_files_are_remembered_a_missing_ffprobe_is_not(
    tmp_path: Path, cache: DurationCache
) -> None:
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"not audio")
    calls: list[str] = []

    def unreadable(path: Path) -> float:
        calls.append("unreadable")
        raise UnreadableMedia("Invalid data found when processing input")

    stamp = stamp_of(broken)
    assert stamp is not None
    assert measure([stamp], cache, unreadable) == {stamp.key: None}
    assert measure([stamp], cache, unreadable) == {stamp.key: None}
    assert calls == ["unreadable"]
    with sqlite3.connect(db.db_path(cache.app_data_dir)) as conn:
        (error,) = conn.execute("SELECT error FROM music_cache").fetchone()
    assert "Invalid data" in error

    other = tmp_path / "other.mp3"
    other.write_bytes(b"also not audio")

    def unavailable(path: Path) -> float:
        calls.append("unavailable")
        raise ProbeUnavailable("ffprobe is not installed")

    other_stamp = stamp_of(other)
    assert other_stamp is not None
    assert measure([other_stamp], cache, unavailable) == {other_stamp.key: None}
    assert measure([other_stamp], cache, unavailable) == {other_stamp.key: None}
    assert calls.count("unavailable") == 2  # tried again: ffprobe may be installed by now


def test_a_failing_prober_never_fails_the_request(tmp_path: Path, cache: DurationCache) -> None:
    track = write_wav(tmp_path / "a.wav", 1.0)

    def broken(path: Path) -> float:
        raise RuntimeError("adapter bug")

    stamp = stamp_of(track)
    assert stamp is not None
    assert measure([stamp], cache, broken) == {stamp.key: None}


def test_slow_probes_finish_in_the_background(tmp_path: Path, cache: DurationCache) -> None:
    track = write_wav(tmp_path / "slow.wav", 1.0)
    release = threading.Event()

    def slow(path: Path) -> float:
        release.wait(10)
        return 3.0

    stamp = stamp_of(track)
    assert stamp is not None
    started = time.monotonic()
    assert measure([stamp], cache, slow, budget_s=0.05) == {stamp.key: None}
    assert time.monotonic() - started < 5  # answered without waiting for the probe
    release.set()
    deadline = time.monotonic() + 10
    while not cache.lookup([stamp]) and time.monotonic() < deadline:
        time.sleep(0.02)

    def never(path: Path) -> float:
        raise AssertionError("measured twice")

    assert measure([stamp], cache, never) == {stamp.key: 3.0}


def test_many_files_and_duplicates(tmp_path: Path, cache: DurationCache) -> None:
    tracks = [write_wav(tmp_path / f"t{i:03}.wav", 0.1) for i in range(30)]
    calls: list[str] = []
    stamps = [stamp_of(t) for t in tracks]
    stamps = [s for s in stamps if s is not None]
    lengths = measure(stamps + stamps[:5], cache, counting(0.1, calls))
    assert len(lengths) == 30 and set(lengths.values()) == {0.1}
    assert sorted(calls) == sorted(t.name for t in tracks)  # each file once
    assert len(cache.lookup(stamps)) == 30


def test_the_cache_survives_a_replaced_database(tmp_path: Path, cache: DurationCache) -> None:
    track = write_wav(tmp_path / "a.wav", 1.0)
    calls: list[str] = []
    stamp = stamp_of(track)
    assert stamp is not None
    measure([stamp], cache, counting(1.0, calls))
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(db.db_path(cache.app_data_dir)) + suffix)
        if path.exists():
            path.unlink()
    assert measure([stamp], cache, counting(1.0, calls)) == {stamp.key: 1.0}
    assert calls == ["a.wav", "a.wav"]
    assert cache.lookup([stamp])[stamp.key].duration_s == 1.0


def test_only_known_tables(tmp_path: Path) -> None:
    assert DurationCache(tmp_path, VIDEO_TABLE).table == "video_cache"
    with pytest.raises(ValueError):
        DurationCache(tmp_path, "projects; DROP TABLE projects")


def test_missing_files_have_no_stamp(tmp_path: Path) -> None:
    assert stamp_of(tmp_path / "nowhere.wav") is None


def test_ffprobe_json_lengths() -> None:
    seconds_from = durations._seconds_from
    assert seconds_from({"format": {"duration": "12.34567"}}) == 12.346
    assert seconds_from({
        "format": {"duration": "N/A"}, "streams": [{"duration": "4.0"}, {"duration": "5.5"}],
    }) == 5.5
    assert seconds_from({"format": {"duration": "nan"}}) is None
    assert seconds_from({"format": {"duration": "inf"}}) is None
    assert seconds_from({"streams": [{"codec_type": "audio"}]}) is None
    assert seconds_from({}) is None


def test_real_ffprobe_reads_a_wav_and_refuses_junk(tmp_path: Path) -> None:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        pytest.skip("ffprobe is not installed")
    track = write_wav(tmp_path / "real.wav", 1.25)
    assert ffprobe_seconds(track, ffprobe) == pytest.approx(1.25, abs=0.01)
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(b"this is not audio at all " * 20)
    with pytest.raises(UnreadableMedia):
        ffprobe_seconds(junk, ffprobe)
    with pytest.raises(ProbeUnavailable):
        ffprobe_seconds(track, str(tmp_path / "no-such-ffprobe.exe"))


def test_without_ffprobe_wav_headers_still_give_lengths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(settings: object = None) -> str:
        raise ff.RenderError("FFprobe was not found on this PC.")

    monkeypatch.setattr(durations.ff, "find_ffprobe", missing)
    probe = prober_for(None)
    assert probe is wav_seconds
    assert probe(write_wav(tmp_path / "a.wav", 1.5)) == 1.5
    with pytest.raises(ProbeUnavailable):
        probe(tmp_path / "song.mp3")
    not_pcm = tmp_path / "odd.wav"
    not_pcm.write_bytes(b"RIFF\x00\x00\x00\x00WAVEjunk")
    with pytest.raises(ProbeUnavailable):
        probe(not_pcm)
