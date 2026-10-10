"""The music library scan: one scan per folder however many channels share it, the same
files and the same licence verdict as the edit stage (``render/music.py``), and a plain
reason when a channel's folder cannot be used."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cashcow_studio.library.durations import MUSIC_TABLE, DurationCache
from cashcow_studio.library.music import music_folders, scan_music
from cashcow_studio.models.channel import Channel
from cashcow_studio.render.music import has_licence, list_tracks
from test_library_api import write_file, write_wav


def channel(slug: str, music_folder: str) -> Channel:
    return Channel.model_validate(
        {"slug": slug, "channel": {"name": slug.replace("-", " ").title(),
                                   "music_folder": music_folder}}
    )


def scan(tmp_path: Path, channels: list[Channel], calls: list[str] | None = None, **kwargs):
    seen = calls if calls is not None else []

    def prober(path: Path) -> float:
        seen.append(path.name)
        return 2.0

    return scan_music(channels, DurationCache(tmp_path / "app", MUSIC_TABLE), prober, **kwargs)


def test_licence_verdicts_match_the_edit_stage(tmp_path: Path) -> None:
    sidecars = tmp_path / "sidecars"
    write_wav(sidecars / "a.wav", 0.1)
    write_file(sidecars / "a.wav.license.txt", "CC-BY")
    write_file(sidecars / "b.mp3", b"ID3")
    write_file(sidecars / "b.licence.txt", "bought")  # without the audio extension
    write_file(sidecars / "c.m4a", b"m4a")
    write_file(sidecars / "c.m4a.licence.txt", "bought")
    write_file(sidecars / "d.flac", b"fLaC")  # no licence at all
    write_file(sidecars / "d.flac.txt", "not a licence file")
    covered = tmp_path / "covered"
    write_file(covered / "LICENSE.md", "Everything in this folder is ours.")
    write_file(covered / "e.ogg", b"OggS")
    write_file(covered / "f.aac", b"aac")
    library = scan(tmp_path, [channel("one", str(sidecars)), channel("two", str(covered))])

    verdicts = {row.file: (row.license_ok, row.license_file) for row in library.tracks}
    assert verdicts == {
        "a.wav": (True, "a.wav.license.txt"),
        "b.mp3": (True, "b.licence.txt"),
        "c.m4a": (True, "c.m4a.licence.txt"),
        "d.flac": (False, None),
        "e.ogg": (True, "LICENSE.md"),
        "f.aac": (True, "LICENSE.md"),
    }
    for row in library.tracks:
        assert row.license_ok == has_licence(Path(row.folder) / row.file), row.file
    by_folder = {Path(info.folder).name: info for info in library.folders}
    assert by_folder["covered"].license_file == "LICENSE.md"
    assert by_folder["covered"].licensed_tracks == 2
    assert by_folder["sidecars"].license_file is None
    assert by_folder["sidecars"].tracks == 4 and by_folder["sidecars"].licensed_tracks == 3


def test_same_files_and_order_as_the_edit_stage(tmp_path: Path) -> None:
    folder = tmp_path / "music"
    for name in ("Zebra.MP3", "alpha.wav", "beta.opus", "notes.txt", "cover.jpg", "midi.mid"):
        write_file(folder / name, b"x")
    write_file(folder / "inner" / "deep.mp3", b"x")  # sub-folders are not scanned
    library = scan(tmp_path, [channel("one", str(folder))])
    assert [row.file for row in library.tracks] == [p.name for p in list_tracks(folder)]
    assert [row.file for row in library.tracks] == ["alpha.wav", "beta.opus", "Zebra.MP3"]


def test_a_shared_folder_is_scanned_once(tmp_path: Path) -> None:
    shared = tmp_path / "Shared Music"
    write_wav(shared / "one.wav", 0.2)
    own = tmp_path / "own"
    write_wav(own / "two.wav", 0.2)
    spelled = str(shared) + os.sep + "." + os.sep
    if os.name == "nt":
        spelled = spelled.upper()  # Windows paths ignore case
    channels = [channel("b-channel", spelled), channel("a-channel", str(shared)),
                channel("c-channel", str(own)), channel("no-music", "")]
    folders = music_folders(channels)
    assert [(Path(f.path).name.lower(), f.channels) for f in folders] == [
        ("own", ["c-channel"]), ("shared music", ["a-channel", "b-channel"]),
    ]
    calls: list[str] = []
    library = scan(tmp_path, channels, calls)
    assert [(row.file, row.used_by_channels) for row in library.tracks] == [
        ("two.wav", ["c-channel"]), ("one.wav", ["a-channel", "b-channel"]),
    ]
    assert sorted(calls) == ["one.wav", "two.wav"]
    assert all(row.duration_s == 2.0 for row in library.tracks)


def test_folders_that_cannot_be_used_say_why(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    write_file(empty / "readme.txt", "no music yet")
    a_file = write_file(tmp_path / "song.mp3", b"ID3")
    missing = tmp_path / "not-synced"
    channels = [
        channel("empty", str(empty)),
        channel("file", str(a_file)),
        channel("missing", str(missing)),
        channel("quoted", f'"{empty}"'),
        channel("relative", "music"),
    ]
    library = scan(tmp_path, channels)
    assert library.tracks == []
    problems = {info.used_by_channels[0]: info for info in library.folders}
    assert problems["empty"].exists is True and "has no audio files" in problems["empty"].problem
    assert problems["file"].exists is False and "is a file" in problems["file"].problem
    assert problems["missing"].exists is False
    assert "was not found" in problems["missing"].problem
    assert "Google Drive" in problems["missing"].problem
    # Settings the edit stage cannot use, whatever is on disk: shown as typed.
    quoted = problems["quoted"]
    assert quoted.exists is False and quoted.folder == f'"{empty}"'
    assert "quote marks" in quoted.problem
    relative = problems["relative"]
    assert relative.exists is False and relative.folder == "music"
    assert "not a full path" in relative.problem


def test_folder_overview_does_not_measure(tmp_path: Path) -> None:
    folder = tmp_path / "music"
    write_wav(folder / "one.wav", 0.2)
    calls: list[str] = []
    library = scan(tmp_path, [channel("one", str(folder))], calls, measure_lengths=False)
    assert calls == [] and library.tracks[0].duration_s is None
    assert library.tracks[0].size_bytes == (folder / "one.wav").stat().st_size
    assert library.folders[0].tracks == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows file names ignore case")
def test_sidecar_names_ignore_case_on_windows(tmp_path: Path) -> None:
    folder = tmp_path / "music"
    write_wav(folder / "Song.wav", 0.1)
    write_file(folder / "song.WAV.License.TXT", "ok")
    (row,) = scan(tmp_path, [channel("one", str(folder))]).tracks
    assert row.license_ok is True and row.license_file == "song.WAV.License.TXT"
    assert has_licence(folder / "Song.wav") is True
