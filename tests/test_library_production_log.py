"""The production log's rules: which projects count, how the audit rows and the export
files are merged, what happens without an audit table, the ``since`` filter and a CSV a
spreadsheet cannot run as formulas."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cashcow_studio.library.production_log import (
    InvalidSince,
    ProductionLogRow,
    build_production_log,
    parse_since,
    parse_time,
    read_export_audit,
    to_csv,
)
from cashcow_studio.models.export import ExportDoc
from cashcow_studio.storage import db
from test_library_api import add_audit, make_project, write_export, write_file

A = "aaaaaaaa-0000-4000-8000-000000000001"
B = "bbbbbbbb-0000-4000-8000-000000000002"


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    projects, app, exports = tmp_path / "projects", tmp_path / "app", tmp_path / "exports"
    for folder in (projects, app, exports):
        folder.mkdir()
    return projects, app, exports


def lengths(table: dict[str, float], calls: list[str] | None = None):
    def prober(path: Path) -> float:
        if calls is not None:
            calls.append(path.name)
        return table[path.name]

    return prober


def no_probe(path: Path) -> float:
    raise AssertionError(f"{path} should not be measured")


def exported_project(projects: Path, exports: Path, project_id: str = A, *,
                     exported: bool = True, approved_by: str | None = "Imran",
                     title: str = "Story", at: str = "2026-10-01T10:00:00+00:00") -> Path:
    folder = make_project(projects, project_id, "kind-ledger", "story", title,
                          "2026-09-30T08:00:00+00:00", approved_by=approved_by)
    write_export(folder, project_id, "kind-ledger", "story", exports / "story",
                 exported=exported, exported_at=at, presets=["1080p"], seo_title=title,
                 video_bytes={"1080p": 500})
    return folder


def test_works_without_an_audit_table(dirs: tuple[Path, Path, Path]) -> None:
    projects, app, exports = dirs
    exported_project(projects, exports)
    assert not db.db_path(app).exists()
    (row,) = build_production_log(projects, app, prober=lengths({"story_1080p.mp4": 12.5}))
    assert row.user == "Imran" and row.machine == ""  # the name typed on the approval
    assert row.at == datetime(2026, 10, 1, 10, tzinfo=UTC)
    assert row.duration_s == 12.5 and row.size_bytes == 500 and row.presets == ["1080p"]

    # An audit table of another shape is ignored, not fatal.
    db.ensure_table(app, "CREATE TABLE audit (id INTEGER PRIMARY KEY, ts TEXT, who TEXT)")
    assert read_export_audit(app) == []
    (again,) = build_production_log(projects, app, prober=no_probe)  # the length is cached
    assert again.user == "Imran" and again.duration_s == 12.5


def test_an_auto_export_without_a_reviewer_has_no_user(dirs: tuple[Path, Path, Path]) -> None:
    projects, app, exports = dirs
    exported_project(projects, exports, approved_by=None)
    (row,) = build_production_log(projects, app, prober=lengths({"story_1080p.mp4": 1.0}))
    assert row.user == "" and row.machine == ""


def test_never_copied_packs_are_left_out_unless_exported_before(
    dirs: tuple[Path, Path, Path]
) -> None:
    projects, app, exports = dirs
    exported_project(projects, exports, A, exported=False)
    assert build_production_log(projects, app, prober=no_probe) == []

    # Exported once (audit row), then the metadata was edited and not exported again:
    # export.json says "not exported" and lists no files; the audit row says what went.
    add_audit(app, [{
        "at": "2026-10-02T09:30:00+00:00", "user": "sara", "machine": "LAPTOP-2",
        "project_id": A, "channel_slug": "kind-ledger",
        "detail": {"presets": ["1080p", "720p"], "size_bytes": 9000, "duration_s": 33.3},
    }])
    (row,) = build_production_log(projects, app, prober=no_probe)
    assert (row.user, row.machine) == ("sara", "LAPTOP-2")
    assert row.at == datetime(2026, 10, 2, 9, 30, tzinfo=UTC)
    assert row.presets == ["1080p", "720p"] and row.size_bytes == 9000
    assert row.duration_s == 33.3 and row.title == "Story"
    assert row.export_folder == str(exports / "story")

    # When the audit row recorded the title and folder, those are what went out, not the
    # edited pack waiting for its next export.
    add_audit(app, [{
        "at": "2026-10-02T10:00:00+00:00", "user": "sara", "machine": "LAPTOP-2",
        "project_id": A, "channel_slug": "kind-ledger",
        "detail": {"presets": ["1080p"], "title": "Story (as exported)",
                   "export_folder": str(exports / "then")},
    }])
    (row,) = build_production_log(projects, app, prober=lengths({}))
    assert row.title == "Story (as exported)" and row.export_folder == str(exports / "then")
    assert row.presets == ["1080p"] and row.duration_s is None


def test_the_latest_audit_row_wins(dirs: tuple[Path, Path, Path]) -> None:
    projects, app, exports = dirs
    exported_project(projects, exports)
    add_audit(app, [
        {"at": "2026-10-03T08:00:00Z", "user": "new", "machine": "PC-NEW", "project_id": A,
         "channel_slug": "kind-ledger", "detail": {"duration_s": 7}},
        {"at": "2026-10-01T08:00:00Z", "user": "old", "machine": "PC-OLD", "project_id": A,
         "channel_slug": "kind-ledger", "detail": {"duration_s": 6}},
        {"at": "not a time", "user": "broken", "machine": "?", "project_id": A,
         "channel_slug": "kind-ledger"},
        {"at": "2026-10-04T08:00:00Z", "user": "nobody", "machine": "?", "project_id": "",
         "channel_slug": "kind-ledger"},
        {"at": "2026-10-05T08:00:00Z", "user": "x", "machine": "?", "project_id": A,
         "channel_slug": "kind-ledger", "action": "settings.update"},
    ])
    (row,) = build_production_log(projects, app, prober=no_probe)
    assert (row.user, row.machine, row.duration_s) == ("new", "PC-NEW", 7.0)
    assert row.at == datetime(2026, 10, 3, 8, tzinfo=UTC)

    # An export the run copied by itself (stage set to auto) may carry no user: the name on
    # the export approval is the best there is.
    add_audit(app, [{"at": "2026-10-06T08:00:00Z", "user": "", "machine": "PC-AUTO",
                     "project_id": A, "channel_slug": "kind-ledger"}])
    (row,) = build_production_log(projects, app, prober=lengths({"story_1080p.mp4": 3.0}))
    assert (row.user, row.machine, row.duration_s) == ("Imran", "PC-AUTO", 3.0)


def test_reads_what_the_export_stage_records(dirs: tuple[Path, Path, Path]) -> None:
    """The activity log's own writer and table, with the details the export stage gives
    ``export.done`` (``pipeline/stages/export.py``: ``record_export``)."""
    audit = pytest.importorskip("cashcow_studio.auth.audit")
    projects, app, exports = dirs
    exported_project(projects, exports)
    audit.record(
        app, "export.done", "imran", machine="EDIT-PC-3", project_id=A,
        channel_slug="kind-ledger", at=datetime(2026, 10, 8, 7, 45, 12, tzinfo=UTC),
        detail={"title": "Story", "format": "long", "language": "English",
                "presets": ["1080p"], "duration_s": 58.4, "size_bytes": 500, "files": 3,
                "export_folder": str(exports / "story")},
    )
    audit.record(app, "login", "imran", machine="EDIT-PC-3")
    (row,) = build_production_log(projects, app, prober=no_probe)
    assert (row.user, row.machine) == ("imran", "EDIT-PC-3")
    assert row.at == datetime(2026, 10, 8, 7, 45, 12, tzinfo=UTC)
    assert row.duration_s == 58.4 and row.size_bytes == 500 and row.presets == ["1080p"]


def test_a_copied_project_folder_counts_once(dirs: tuple[Path, Path, Path]) -> None:
    projects, app, exports = dirs
    exported_project(projects / "_archived", exports, at="2026-10-01T10:00:00Z")
    exported_project(projects, exports, at="2026-10-01T10:00:00Z")
    rows = build_production_log(projects, app, prober=lengths({"story_1080p.mp4": 2.0}))
    assert len(rows) == 1 and rows[0].archived is False


def test_an_audit_row_without_its_folder(dirs: tuple[Path, Path, Path]) -> None:
    projects, app, exports = dirs
    folder = exports / "kept"
    write_file(folder / "kept_1080p.mp4", b"\x00" * 700)
    write_file(folder / "kept_720p.mp4", b"\x00" * 300)
    write_file(folder / "kept_thumbnail.png", b"png")
    add_audit(app, [{
        "at": "2026-10-02T09:30:00+05:00", "user": "imran", "machine": "LAPTOP-1",
        "project_id": B, "channel_slug": "kind-ledger",
        "detail": {"title": "Kept", "presets": "1080p, 720p", "export_folder": str(folder)},
    }])
    calls: list[str] = []
    (row,) = build_production_log(projects, app, prober=lengths({"kept_1080p.mp4": 20.0,
                                                                 "kept_720p.mp4": 20.0}, calls))
    assert row.at == datetime(2026, 10, 2, 4, 30, tzinfo=UTC)
    assert row.presets == ["1080p", "720p"] and row.size_bytes == 1000
    assert row.duration_s == 20.0 and calls == ["kept_1080p.mp4"]
    assert row.title == "Kept" and row.export_folder == str(folder)


def test_length_from_the_render_when_the_copy_moved(dirs: tuple[Path, Path, Path]) -> None:
    projects, app, exports = dirs
    folder = exported_project(projects, exports)
    for item in (exports / "story").iterdir():
        item.unlink()  # someone moved the exported files away
    write_file(folder / "07_edit" / "final_1080p.mp4", b"\x00" * 64)
    calls: list[str] = []
    (row,) = build_production_log(projects, app,
                                  prober=lengths({"final_1080p.mp4": 9.5}, calls))
    assert row.duration_s == 9.5 and calls == ["final_1080p.mp4"]
    assert row.size_bytes == 500  # what export.json recorded when it copied


def test_newest_first_and_filters(dirs: tuple[Path, Path, Path]) -> None:
    projects, app, exports = dirs
    exported_project(projects, exports, A, at="2026-10-01T10:00:00Z")
    other = make_project(projects, B, "calm-harbor", "sea", "Sea", "2026-09-01T08:00:00Z")
    write_export(other, B, "calm-harbor", "sea", exports / "sea", exported=True,
                 exported_at="2026-10-03T10:00:00Z", presets=["720p"], seo_title="Sea",
                 video_bytes={"720p": 100})
    prober = lengths({"story_1080p.mp4": 1.0, "sea_720p.mp4": 2.0})
    rows = build_production_log(projects, app, prober=prober)
    assert [r.project_id for r in rows] == [B, A]
    only = build_production_log(projects, app, prober=prober, channel_slug=" calm-harbor ")
    assert [r.project_id for r in only] == [B]
    since = build_production_log(projects, app, prober=prober,
                                 since=datetime(2026, 10, 2, tzinfo=UTC))
    assert [r.project_id for r in since] == [B]


def test_times() -> None:
    assert parse_time("2026-10-01T10:00:00Z") == datetime(2026, 10, 1, 10, tzinfo=UTC)
    assert parse_time("2026-10-01T15:00:00.123+05:00") == datetime(2026, 10, 1, 10, tzinfo=UTC)
    assert parse_time("2026-10-01T10:00:00") == datetime(2026, 10, 1, 10, tzinfo=UTC)
    assert parse_time(0) == datetime(1970, 1, 1, tzinfo=UTC)
    assert parse_time("yesterday") is None and parse_time(None) is None
    assert parse_time(True) is None

    assert parse_since(None) is None and parse_since("  ") is None
    assert parse_since("2026-10-01T09:00:00Z") == datetime(2026, 10, 1, 9, tzinfo=UTC)
    local_midnight = datetime(2026, 10, 1).astimezone()
    assert parse_since("2026-10-01") == local_midnight.astimezone(UTC)
    with pytest.raises(InvalidSince) as error:
        parse_since("next " * 30)
    assert "is not a date" in str(error.value) and len(str(error.value)) < 200


def test_csv_is_safe_to_open_in_a_spreadsheet() -> None:
    at = datetime(2026, 10, 1, 10, tzinfo=UTC)
    rows = [
        ProductionLogRow(at=at, user="@admin", machine="=cmd", channel_slug="kind-ledger",
                         title='=HYPERLINK("http://x","click")', project_id=A, format="long",
                         language="Hindi", presets=["1080p", "2160p"], duration_s=61.25,
                         size_bytes=123, export_folder="D:\\Exports\\kind"),
        ProductionLogRow(at=at - timedelta(days=1), title="-5 tips, and why", project_id=B,
                         user="", machine=""),
        ProductionLogRow(at=at - timedelta(days=2), title="कहानी 10 सबक", project_id=B),
    ]
    text = to_csv(rows)
    assert text.startswith("\ufeff")
    table = list(csv.reader(io.StringIO(text[1:])))
    assert len(table) == 4
    first = table[1]
    assert first[1] == "'@admin" and first[2] == "'=cmd"
    assert first[4] == "'=HYPERLINK(\"http://x\",\"click\")"
    assert first[8] == "1080p, 2160p" and first[9] == "61.25" and first[10] == "123"
    assert first[0] == "2026-10-01T10:00:00Z"
    second = table[2]
    assert second[4] == "'-5 tips, and why" and second[9] == "" and second[10] == ""
    assert table[3][4] == "कहानी 10 सबक"
    assert to_csv([]).splitlines() == ["\ufeffat,user,machine,channel_slug,title,project_id,"
                                       "format,language,presets,duration_s,size_bytes,"
                                       "export_folder"]


def test_broken_export_files_are_skipped(dirs: tuple[Path, Path, Path]) -> None:
    projects, app, exports = dirs
    exported_project(projects, exports, A)
    broken = projects / "2026-10-02_kind-ledger_broken"
    write_file(broken / "08_export" / "export.json", "{not json")
    listing = projects / "2026-10-03_kind-ledger_list"
    write_file(listing / "08_export" / "export.json", json.dumps(["not", "an", "object"]))
    nameless = projects / "2026-10-04_kind-ledger_nameless"
    doc = ExportDoc(project_id="", exported_at=datetime(2026, 10, 4, tzinfo=UTC),
                    export_folder="", topic_slug="x", exported=True)
    write_file(nameless / "08_export" / "export.json", doc.model_dump_json())
    rows = build_production_log(projects, app, prober=lengths({"story_1080p.mp4": 1.0}))
    assert [r.project_id for r in rows] == [A]
