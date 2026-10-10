"""The frameworks library: every channel's framework files in one list.

The entries come from each channel's ``channel.json`` (in the channel's own order, which is
also the order the stages pick them in); the size and date come from the file in the shared
folder. A path is checked with the same rule the stages use (``llm/frameworks.py``): only
files inside the shared folder are ever looked at, never a full path or a hidden file, and a
web link is shown but never opened.

``extracted`` says whether the framework's text is ready for the writing model:

* a ``<name>.extracted.txt`` file next to it (``title.pdf.extracted.txt`` or
  ``title.extracted.txt``, the convention of the team's earlier tools), or
* the app's own copy of the text, which the stages keep under
  ``<app_data_dir>/cache/frameworks/`` (never next to the synced file) and which is at least
  as new as the file, or
* a plain-text framework (``.txt``, ``.md``), whose text needs no extraction.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from ..llm.frameworks import (
    TEXT_SUFFIXES,
    FrameworkError,
    cache_path,
    check_framework_path,
    framework_cache_dir,
    is_link,
    resolve_path,
)
from ..models.channel import Channel, Framework, FrameworkType, VideoFormat

log = logging.getLogger(__name__)

EXTRACTED_SUFFIX = ".extracted.txt"


class FrameworkEntry(BaseModel):
    """One row of ``GET /api/library/frameworks``."""

    channel_slug: str
    channel_name: str
    type: FrameworkType
    name: str
    path: str = Field(description="relative to the shared folder, or a web link")
    version: str = ""
    formats: VideoFormat = "both"
    size_bytes: int | None = Field(default=None, description="null for a link or a missing file")
    updated_at: datetime | None = Field(default=None, description="the file's last change")
    extracted: bool = Field(default=False, description="the text is ready for the writing model")
    exists: bool = Field(default=False, description="the file is in the shared folder")
    is_link: bool = False
    problem: str | None = Field(
        default=None, description="plain English, when the stages cannot use this framework"
    )


def is_extracted(path: Path, cache_dir: Path | None) -> bool:
    """See the module docstring. ``path`` is an existing file inside the shared folder."""
    for name in (path.name + EXTRACTED_SUFFIX, path.stem + EXTRACTED_SUFFIX):
        if path.with_name(name).is_file():
            return True
    if cache_dir is not None:
        cached = cache_path(path, cache_dir)
        try:
            if cached.is_file() and cached.stat().st_mtime >= path.stat().st_mtime:
                return True
        except OSError:
            pass
    return path.suffix.lower() in TEXT_SUFFIXES


def framework_entry(
    channel: Channel, framework: Framework, shared_dir: Path, cache_dir: Path | None
) -> FrameworkEntry:
    entry = FrameworkEntry(
        channel_slug=channel.slug,
        channel_name=channel.channel.name,
        type=framework.type,
        name=framework.name,
        path=framework.path,
        version=framework.version,
        formats=framework.formats,
    )
    raw = (framework.path or "").strip().strip('"')
    if not raw:
        entry.problem = "No file has been chosen for this framework yet."
        return entry
    if is_link(raw):
        entry.is_link = True
        return entry
    problem = check_framework_path(raw)
    if problem is not None:
        entry.problem = problem[:1].upper() + problem[1:]
        return entry
    try:
        path = resolve_path(raw, shared_dir)
        info = path.stat()
    except FrameworkError as exc:
        text = str(exc)
        entry.problem = text[:1].upper() + text[1:]
        return entry
    except OSError:
        entry.problem = (
            f"The file {raw} is not in the shared folder on this laptop. If the folder is in "
            "Google Drive, check that it has finished syncing."
        )
        return entry
    if not path.is_file():
        entry.problem = f"{raw} is a folder, not a file."
        return entry
    entry.exists = True
    entry.size_bytes = int(info.st_size)
    entry.updated_at = datetime.fromtimestamp(info.st_mtime, UTC).replace(microsecond=0)
    try:
        entry.extracted = is_extracted(path, cache_dir)
    except OSError:
        entry.extracted = False
    return entry


def list_frameworks(
    channels: Iterable[Channel], shared_dir: Path | str, app_data_dir: Path | str | None
) -> list[FrameworkEntry]:
    """Every framework of every channel: channels by name, frameworks in channel order."""
    root = Path(shared_dir)
    cache_dir = framework_cache_dir(Path(app_data_dir)) if app_data_dir is not None else None
    rows: list[FrameworkEntry] = []
    for channel in channels:
        for framework in channel.frameworks:
            try:
                rows.append(framework_entry(channel, framework, root, cache_dir))
            except Exception:  # noqa: BLE001 - one odd entry must not hide the others
                log.exception("Framework '%s' of %s could not be listed", framework.name,
                              channel.slug)
    return rows


__all__ = ["FrameworkEntry", "framework_entry", "is_extracted", "list_frameworks"]
