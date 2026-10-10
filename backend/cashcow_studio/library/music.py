"""The music library: every track in every channel's music folder.

A folder named by several channels is scanned once and its tracks list all of those
channels in ``used_by_channels``. The folder is read the way the edit stage reads it
(``render/music.py``): the audio files directly inside it, the same file types, and the
same licence rule, so the badge here is the verdict the ``edit.music_license`` gate gives:
a ``<track>.license.txt`` (or ``.licence.txt``, with or without the audio extension) next to
the track, or a ``LICENSE*`` / ``LICENCE*`` file anywhere in the folder. The folder is listed
once per scan (one directory read, not one per track), which matters for big synced folders.

Track lengths come from ffprobe through :mod:`.durations` (SQLite table ``music_cache``).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from ..models.channel import Channel
from ..render.music import AUDIO_EXTENSIONS, LICENCE_PREFIXES
from .durations import DurationCache, FileStamp, Prober, measure, stamp_of

log = logging.getLogger(__name__)

QUOTES = "\"'"


class MusicTrack(BaseModel):
    """One row of ``GET /api/library/music``."""

    folder: str = Field(description="the music folder, as a full path")
    file: str = Field(description="the track's file name inside the folder")
    duration_s: float | None = Field(default=None, description="null until measured")
    size_bytes: int | None = None
    license_ok: bool = False
    license_file: str | None = Field(
        default=None, description="name of the licence file inside the folder"
    )
    used_by_channels: list[str] = Field(default=[], description="channel slugs")


class MusicFolderInfo(BaseModel):
    """One row of ``GET /api/library/music/folders``: the state of a music folder, so the
    Music page can say why a channel shows no tracks."""

    folder: str
    exists: bool = False
    tracks: int = 0
    licensed_tracks: int = 0
    license_file: str | None = Field(
        default=None, description="a LICENSE file that covers the whole folder"
    )
    used_by_channels: list[str] = []
    problem: str | None = Field(default=None, description="plain English, when not usable")


@dataclass
class MusicLibrary:
    tracks: list[MusicTrack] = field(default_factory=list)
    folders: list[MusicFolderInfo] = field(default_factory=list)


@dataclass
class _Folder:
    path: Path
    configured: str
    channels: list[str] = field(default_factory=list)


@dataclass
class _Track:
    folder: _Folder
    name: str
    stamp: FileStamp | None
    licence_file: str | None


# Folders -----------------------------------------------------------------------------------


def music_folders(channels: Iterable[Channel]) -> list[_Folder]:
    """The channels' music folders, one entry per folder (paths compared the way Windows
    compares them: case and trailing slashes do not matter)."""
    groups: dict[str, _Folder] = {}
    for channel in channels:
        configured = (channel.channel.music_folder or "").strip()
        if not configured:
            continue
        path = Path(os.path.abspath(configured))
        key = os.path.normcase(str(path))
        group = groups.setdefault(key, _Folder(path=path, configured=configured))
        if channel.slug not in group.channels:
            group.channels.append(channel.slug)
    for group in groups.values():
        group.channels.sort()
    return sorted(groups.values(), key=lambda group: str(group.path).casefold())


def _unusable_setting(folder: _Folder) -> str | None:
    """A folder setting the edit stage cannot use whatever is on disk (it reads the path as
    written): quote marks around it, or a path that is not a full path."""
    text = folder.configured
    if text[:1] in QUOTES or text[-1:] in QUOTES:
        return (
            f"The music folder {text} has quote marks around it. Remove them in the "
            "channel's settings."
        )
    if not Path(text).is_absolute():
        return (
            f"The music folder '{text}' is not a full path. Use the whole path, for example "
            "D:\\Music\\Calm, in the channel's settings."
        )
    return None


def _not_found(folder: _Folder) -> str:
    return (
        f"The music folder {folder.path} was not found on this laptop. If it is in Google "
        "Drive, check that Drive is running and the folder is synced."
    )


def _sidecar_licence(track_name: str, files: dict[str, str]) -> str | None:
    """``<track>.license.txt`` and its spellings, as ``render.music.has_licence`` tries
    them (``files`` maps the case-folded names Windows would match to the real names)."""
    stem = Path(track_name).stem
    for base in (track_name, stem):
        for word in LICENCE_PREFIXES:
            found = files.get(os.path.normcase(f"{base}.{word}.txt"))
            if found is not None:
                return found
    return None


def _scan_folder(folder: _Folder) -> tuple[MusicFolderInfo, list[_Track]]:
    info = MusicFolderInfo(folder=str(folder.path), used_by_channels=list(folder.channels))
    unusable = _unusable_setting(folder)
    if unusable is not None:
        info.folder = folder.configured
        info.problem = unusable
        return info, []
    try:
        with os.scandir(folder.path) as listing:
            entries = sorted(listing, key=lambda entry: entry.name.lower())
    except FileNotFoundError:
        info.problem = _not_found(folder)
        return info, []
    except NotADirectoryError:
        info.problem = f"{folder.path} is a file, not a folder. Choose the folder that holds it."
        return info, []
    except PermissionError:
        info.problem = f"Windows did not allow CashCow Studio to open {folder.path}."
        return info, []
    except OSError as exc:
        info.problem = f"The music folder {folder.path} could not be read: {exc.strerror or exc}"
        return info, []
    info.exists = True
    files: dict[str, str] = {}
    audio: list[os.DirEntry[str]] = []
    for entry in entries:
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        files[os.path.normcase(entry.name)] = entry.name
        if Path(entry.name).suffix.lower() in AUDIO_EXTENSIONS:
            audio.append(entry)
    folder_licence = next(
        (name for name in files.values() if name.lower().startswith(LICENCE_PREFIXES)), None
    )
    info.license_file = folder_licence
    tracks: list[_Track] = []
    for entry in audio:
        try:
            stamp = stamp_of(Path(entry.path), entry.stat())
        except OSError:
            stamp = None
        licence = _sidecar_licence(entry.name, files) or folder_licence
        tracks.append(_Track(folder=folder, name=entry.name, stamp=stamp, licence_file=licence))
    info.tracks = len(tracks)
    info.licensed_tracks = sum(1 for track in tracks if track.licence_file)
    if not tracks:
        kinds = ", ".join(sorted(ext.lstrip(".") for ext in AUDIO_EXTENSIONS))
        info.problem = f"The music folder {folder.path} has no audio files ({kinds})."
    return info, tracks


# The library -------------------------------------------------------------------------------


def scan_music(
    channels: Iterable[Channel],
    cache: DurationCache,
    prober: Prober,
    *,
    budget_s: float | None = None,
    measure_lengths: bool = True,
) -> MusicLibrary:
    """Every track of every channel's music folder, sorted by folder then file name.

    ``cache`` is the ``music_cache`` table; ``prober`` measures a track that is not in it.
    ``measure_lengths=False`` skips the lengths (the folder overview does not show them).
    """
    library = MusicLibrary()
    tracks: list[_Track] = []
    for folder in music_folders(channels):
        info, found = _scan_folder(folder)
        library.folders.append(info)
        tracks.extend(found)
    stamps = [track.stamp for track in tracks if track.stamp is not None]
    lengths: dict[str, float | None] = {}
    if stamps and measure_lengths:
        lengths = measure(stamps, cache, prober, budget_s=budget_s)
    for track in tracks:
        library.tracks.append(
            MusicTrack(
                folder=str(track.folder.path),
                file=track.name,
                duration_s=lengths.get(track.stamp.key) if track.stamp else None,
                size_bytes=track.stamp.size_bytes if track.stamp else None,
                license_ok=track.licence_file is not None,
                license_file=track.licence_file,
                used_by_channels=list(track.folder.channels),
            )
        )
    return library
