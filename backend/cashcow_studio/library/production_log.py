"""The production log: one row per exported video, newest first (docs/M5A-CONTRACT.md
section 2), as JSON for the Master Sheet and as CSV for the team sheet.

Where a row comes from:

1. ``08_export/export.json`` of every project folder, live ones and archived ones
   (``<projects_dir>/_archived/``), because archiving a finished project does not undo its
   export. It gives the export folder, the presets and the size of every copied file;
   ``metadata.json`` next to it gives the published title and ``job.json`` the channel,
   format and language.
2. The ``export.done`` rows of the audit table in ``<app_data_dir>/db.sqlite``: who exported
   it, on which laptop and when. The table belongs to the sign-in part of the app and is
   read defensively: without it (an install from before sign-in) the log is built from the
   export files alone.

A project counts once, with its latest export. A project exported before sign-in existed
has no audit row: it shows the reviewer name typed on the export approval (if any) and no
laptop. An audit row whose project folder is gone still shows, from the details recorded
with it. A project whose export pack is ready but was never copied is not in the log.

export.json does not record the video's length, so it comes from the audit row's details
or from ffprobe on the exported file (the project's own render when the copy has moved),
cached in SQLite ``video_cache`` (see :mod:`.durations`).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..export.exporter import EXPORT_FILE, FINAL_PREFIX, METADATA_FILE
from ..storage import db
from ..storage.project_store import ARCHIVE_DIR, JOB_FILE
from .durations import VIDEO_TABLE, DurationCache, FileStamp, Prober, measure, stamp_of

log = logging.getLogger(__name__)

AUDIT_TABLE = "audit"
EXPORT_ACTION = "export.done"
EXPORT_STAGE_DIR = "08_export"
EDIT_STAGE_DIR = "07_edit"
CSV_COLUMNS = (
    "at", "user", "machine", "channel_slug", "title", "project_id", "format", "language",
    "presets", "duration_s", "size_bytes", "export_folder",
)
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class ProductionLogRow(BaseModel):
    """One row of ``GET /api/production-log``."""

    at: datetime = Field(description="when the video was exported (UTC)")
    user: str = Field(default="", description="who exported it; empty when unknown")
    machine: str = Field(default="", description="the laptop it was exported on")
    channel_slug: str = ""
    title: str = ""
    project_id: str
    format: str = ""
    language: str = ""
    presets: list[str] = []
    duration_s: float | None = None
    size_bytes: int | None = Field(default=None, description="all exported video files")
    export_folder: str = ""
    archived: bool = Field(default=False, description="the project folder is archived")


class InvalidSince(ValueError):
    """The ``since`` filter is not a date; the message is plain English."""


# Small readers -------------------------------------------------------------------------------


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _size(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.replace(";", ",").split(",")
    if not isinstance(value, list | tuple):
        return []
    items: list[str] = []
    for item in value:
        text = _text(item) if isinstance(item, str) else ""
        if text and text not in items:
            items.append(text)
    return items


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def utc(value: datetime) -> datetime:
    """Aware, in UTC, to the second (a time without a zone is taken as UTC, which is how the
    app writes its own times)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0)


def parse_time(value: Any) -> datetime | None:
    """An ISO 8601 text (``Z`` or an offset allowed) or Unix seconds."""
    if isinstance(value, datetime):
        return utc(value)
    if isinstance(value, int | float) and not isinstance(value, bool):
        try:
            return utc(datetime.fromtimestamp(float(value), UTC))
        except (OverflowError, OSError, ValueError):
            return None
    text = _text(value)
    if not text:
        return None
    try:
        return utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def parse_since(value: str | None) -> datetime | None:
    """The ``since`` filter: a date (``2026-10-01``, midnight on this laptop) or a full
    ISO time; ``None`` when empty."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        shown = text if len(text) <= 40 else text[:40] + "..."
        raise InvalidSince(
            f"'{shown}' is not a date. Use the form 2026-10-01 (or a full time such as "
            "2026-10-01T09:00)."
        ) from exc
    if moment.tzinfo is None:
        moment = moment.astimezone()  # the laptop's own time zone
    return utc(moment)


# The audit table -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ExportAudit:
    """One ``export.done`` row of the audit table."""

    at: datetime
    user: str
    machine: str
    project_id: str
    channel_slug: str
    detail: dict[str, Any]
    seq: int = 0


def _detail(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str | bytes) and value:
        try:
            data = json.loads(value)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def read_export_audit(app_data_dir: Path | str) -> list[ExportAudit]:
    """Every ``export.done`` row; empty when the table (or the database) does not exist."""
    if not db.db_path(Path(app_data_dir)).is_file():
        return []
    try:
        with db.connect(Path(app_data_dir)) as conn:
            known = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (AUDIT_TABLE,)
            ).fetchone()
            if known is None:
                return []
            rows = conn.execute(
                f"SELECT * FROM {AUDIT_TABLE} WHERE action = ?", (EXPORT_ACTION,)
            ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        log.warning("The audit table could not be read; the production log uses the export "
                    "files only: %s", exc)
        return []
    audits: list[ExportAudit] = []
    for row in rows:
        data = dict(zip(row.keys(), tuple(row), strict=True))
        project_id = _text(data.get("project_id"))
        at = parse_time(data.get("at"))
        if not project_id or at is None:
            continue
        seq = data.get("id")
        audits.append(
            ExportAudit(
                at=at,
                user=_text(data.get("user")),
                machine=_text(data.get("machine")),
                project_id=project_id,
                channel_slug=_text(data.get("channel_slug")),
                detail=_detail(data.get("detail")),
                seq=seq if isinstance(seq, int) else 0,
            )
        )
    return audits


def latest_by_project(audits: Iterable[ExportAudit]) -> dict[str, ExportAudit]:
    latest: dict[str, ExportAudit] = {}
    for audit in audits:
        current = latest.get(audit.project_id)
        if current is None or (audit.at, audit.seq) > (current.at, current.seq):
            latest[audit.project_id] = audit
    return latest


# The export files ----------------------------------------------------------------------------


@dataclass
class ExportFiles:
    """What one project folder says about its export."""

    project_id: str
    folder: Path
    archived: bool
    export: dict[str, Any]
    metadata: dict[str, Any]
    job: dict[str, Any]

    @property
    def exported(self) -> bool:
        return self.export.get("exported") is True

    @property
    def exported_at(self) -> datetime | None:
        return parse_time(self.export.get("exported_at"))

    def rank(self) -> tuple[bool, bool, datetime]:
        """A copied folder holds the same project twice: live beats archived, exported beats
        not exported, then the later export wins."""
        return (not self.archived, self.exported, self.exported_at or EPOCH)


def _project_folders(root: Path) -> list[Path]:
    try:
        return sorted(
            p for p in root.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))
        )
    except OSError:
        return []


def scan_exports(projects_dir: Path | str) -> list[ExportFiles]:
    """Every project folder (live and archived) with an ``08_export/export.json``."""
    root = Path(projects_dir)
    found: dict[str, ExportFiles] = {}
    for base, archived in ((root, False), (root / ARCHIVE_DIR, True)):
        for folder in _project_folders(base):
            stage = folder / EXPORT_STAGE_DIR
            export = _read_json(stage / EXPORT_FILE)
            if not export:
                continue
            job = _read_json(folder / JOB_FILE)
            metadata = _read_json(stage / METADATA_FILE)
            project_id = (
                _text(export.get("project_id")) or _text(job.get("id"))
                or _text(metadata.get("project_id"))
            )
            if not project_id:
                continue
            record = ExportFiles(project_id, folder, archived, export, metadata, job)
            current = found.get(project_id)
            if current is None or record.rank() > current.rank():
                found[project_id] = record
    return list(found.values())


# Rows ------------------------------------------------------------------------------------------


@dataclass
class _Entry:
    row: ProductionLogRow
    videos: list[Path] = field(default_factory=list)
    """Where the video is, best first: the exported copies, then the project's renders."""


def _approved_by(job: dict[str, Any]) -> str:
    stages = job.get("stages") if isinstance(job.get("stages"), dict) else {}
    export = stages.get("export") if isinstance(stages.get("export"), dict) else {}
    return _text(export.get("approved_by"))


def _size_on_disk(paths: Iterable[Path]) -> int | None:
    total, seen = 0, False
    for path in paths:
        try:
            total += path.stat().st_size
            seen = True
        except OSError:
            continue
    return total if seen else None


def _first(*values: str) -> str:
    return next((value for value in values if value), "")


def _from_files(record: ExportFiles, audit: ExportAudit | None) -> _Entry:
    export, meta, job = record.export, record.metadata, record.job
    detail = audit.detail if audit is not None else {}

    def pick(from_files: str, from_audit: str) -> str:
        # Exported, changed since and not exported again: the audit row says what went out.
        if record.exported:
            return _first(from_files, from_audit)
        return _first(from_audit, from_files)

    export_folder = pick(_text(export.get("export_folder")), _text(detail.get("export_folder")))
    names: list[str] = []
    recorded: list[int] = []
    presets: list[str] = []
    if record.exported:
        missing = set(_text_list(export.get("missing_presets")))
        presets = [p for p in _text_list(export.get("presets")) if p not in missing]
        for item in export.get("files") or []:
            if not isinstance(item, dict) or item.get("kind", "video") != "video":
                continue
            name = _text(item.get("name"))
            if name:
                names.append(name)
                recorded.append(_size(item.get("bytes")) or 0)
    presets = presets or _text_list(detail.get("presets"))
    topic = _text(export.get("topic_slug"))
    if not names and topic:
        names = [f"{topic}_{preset}.mp4" for preset in presets]
    videos = [Path(export_folder) / name for name in names] if export_folder else []
    size = sum(recorded) if recorded and all(recorded) else None
    if size is None:
        size = _size(detail.get("size_bytes", detail.get("size")))
    if size is None:
        size = _size_on_disk(videos)
    renders = [record.folder / EDIT_STAGE_DIR / f"{FINAL_PREFIX}{p}.mp4" for p in presets]
    at = audit.at if audit is not None else (
        record.exported_at or parse_time(_mtime(record.folder / EXPORT_STAGE_DIR / EXPORT_FILE))
        or EPOCH
    )
    row = ProductionLogRow(
        at=at,
        user=_first(audit.user if audit is not None else "", _approved_by(job)),
        machine=audit.machine if audit is not None else "",
        channel_slug=_first(
            _text(job.get("channel_slug")), _text(meta.get("channel_slug")),
            audit.channel_slug if audit is not None else "", _text(detail.get("channel_slug")),
        ),
        title=pick(_first(_text(meta.get("title")), _text(job.get("title"))),
                   _text(detail.get("title"))),
        project_id=record.project_id,
        format=_first(_text(job.get("format")), _text(meta.get("format")),
                      _text(detail.get("format"))),
        language=_first(_text(job.get("language")), _text(meta.get("language")),
                        _text(detail.get("language"))),
        presets=presets,
        duration_s=_number(detail.get("duration_s", detail.get("duration"))),
        size_bytes=size,
        export_folder=export_folder,
        archived=record.archived,
    )
    return _Entry(row=row, videos=videos + renders)


def _from_audit(audit: ExportAudit) -> _Entry:
    """A project whose folder is gone: everything comes from the audit row."""
    detail = audit.detail
    export_folder = _text(detail.get("export_folder"))
    videos: list[Path] = []
    if export_folder:
        try:
            videos = sorted(Path(export_folder).glob("*.mp4"))
        except OSError:
            videos = []
    size = _size(detail.get("size_bytes", detail.get("size")))
    row = ProductionLogRow(
        at=audit.at,
        user=audit.user,
        machine=audit.machine,
        channel_slug=_first(audit.channel_slug, _text(detail.get("channel_slug"))),
        title=_text(detail.get("title")),
        project_id=audit.project_id,
        format=_text(detail.get("format")),
        language=_text(detail.get("language")),
        presets=_text_list(detail.get("presets")),
        duration_s=_number(detail.get("duration_s", detail.get("duration"))),
        size_bytes=size if size is not None else _size_on_disk(videos),
        export_folder=export_folder,
    )
    return _Entry(row=row, videos=videos)


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _fill_durations(
    entries: list[_Entry], cache: DurationCache, prober: Prober, budget_s: float | None
) -> None:
    wanted: list[tuple[_Entry, FileStamp]] = []
    for entry in entries:
        if entry.row.duration_s is not None:
            continue
        for video in entry.videos:
            stamp = stamp_of(video)
            if stamp is not None and stamp.size_bytes > 0:
                wanted.append((entry, stamp))
                break
    if not wanted:
        return
    lengths = measure([stamp for _, stamp in wanted], cache, prober, budget_s=budget_s)
    for entry, stamp in wanted:
        entry.row.duration_s = lengths.get(stamp.key)


def build_production_log(
    projects_dir: Path | str,
    app_data_dir: Path | str,
    *,
    prober: Prober,
    channel_slug: str | None = None,
    since: datetime | None = None,
    budget_s: float | None = None,
) -> list[ProductionLogRow]:
    """Every exported video, newest first, optionally of one channel and since a time."""
    audits = latest_by_project(read_export_audit(app_data_dir))
    entries: list[_Entry] = []
    seen: set[str] = set()
    for record in scan_exports(projects_dir):
        audit = audits.get(record.project_id)
        if not record.exported and audit is None:
            continue  # the pack is ready but was never copied to the export folder
        entries.append(_from_files(record, audit))
        seen.add(record.project_id)
    entries.extend(_from_audit(audit) for pid, audit in audits.items() if pid not in seen)
    slug = (channel_slug or "").strip()
    if slug:
        entries = [entry for entry in entries if entry.row.channel_slug == slug]
    if since is not None:
        floor = utc(since)
        entries = [entry for entry in entries if entry.row.at >= floor]
    _fill_durations(entries, DurationCache(app_data_dir, VIDEO_TABLE), prober, budget_s)
    rows = [entry.row for entry in entries]
    rows.sort(key=lambda row: (row.at, row.project_id), reverse=True)
    return rows


# CSV -------------------------------------------------------------------------------------------


def _cell(value: str) -> str:
    """Text a spreadsheet shows as text: a title starting with ``=`` (or ``+ - @``) would
    otherwise run as a formula when the file is opened in Excel or Google Sheets."""
    return "'" + value if value.startswith(FORMULA_STARTS) else value


def _seconds(value: float | None) -> str:
    return "" if value is None else str(round(value, 3))


def to_csv(rows: Iterable[ProductionLogRow]) -> str:
    """The same rows as CSV (UTF-8 with a byte-order mark so Excel shows every language)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow([
            utc(row.at).strftime("%Y-%m-%dT%H:%M:%SZ"),
            _cell(row.user),
            _cell(row.machine),
            _cell(row.channel_slug),
            _cell(row.title),
            _cell(row.project_id),
            _cell(row.format),
            _cell(row.language),
            _cell(", ".join(row.presets)),
            _seconds(row.duration_s),
            "" if row.size_bytes is None else str(row.size_bytes),
            _cell(row.export_folder),
        ])
    return "﻿" + buffer.getvalue()
