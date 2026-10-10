"""The Master Sheet endpoints end to end (docs/M5A-CONTRACT.md section 2) on a temporary
team folder: two channels with frameworks and voices, one music folder they share with a
licensed and an unlicensed WAV, and exported projects (one live with audit rows, one
archived from before sign-in, one never exported, one whose folder is gone).

Every TestClient signs in as the test admin by itself (conftest.py); the sign-in test is
marked ``signed_out`` and a viewer reads everything an admin does. Nothing touches the
network; ffprobe is used when installed (the WAV headers are read otherwise), the video
lengths come from a stand-in prober.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import wave
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cashcow_studio.api import library as library_api
from cashcow_studio.app import create_app
from cashcow_studio.library import durations
from cashcow_studio.models.channel import Channel
from cashcow_studio.models.export import ExportDoc, ExportedFile, ExportMetadata
from cashcow_studio.models.project import Project, ProjectSource, StageName
from cashcow_studio.storage import db
from cashcow_studio.storage.channel_store import ChannelStore
from conftest import AppEnv

KIND = "kind-ledger"
CALM = "calm-harbor"
LIVE_ID = "11111111-1111-4111-8111-111111111111"
ARCHIVED_ID = "22222222-2222-4222-8222-222222222222"
UNEXPORTED_ID = "33333333-3333-4333-8333-333333333333"
GONE_ID = "44444444-4444-4444-8444-444444444444"
VIDEO_SECONDS = {"sea-story_1080p.mp4": 45.25}
URLS = (
    "/api/library/frameworks",
    "/api/library/voices",
    "/api/library/music",
    "/api/library/music/folders",
    "/api/production-log",
    "/api/production-log.csv",
)


# The temporary team folder -------------------------------------------------------------------


def write_wav(path: Path, seconds: float, rate: int = 8000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(seconds * rate))
    return path


def write_file(path: Path, data: bytes | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    return path


def stamp(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def make_project(
    root: Path,
    project_id: str,
    channel_slug: str,
    topic: str,
    title: str,
    created: str,
    *,
    approved_by: str | None = None,
    fmt: str = "long",
    language: str = "English",
) -> Path:
    folder = root / f"{created[:10]}_{channel_slug}_{topic}"
    (folder / "08_export").mkdir(parents=True)
    (folder / "07_edit").mkdir()
    project = Project(
        id=project_id,
        channel_slug=channel_slug,
        topic_slug=topic,
        title=title,
        format=fmt,  # type: ignore[arg-type]
        language=language,  # type: ignore[arg-type]
        created_at=stamp(created),
        updated_at=stamp(created),
        folder=str(folder),
        source=ProjectSource(kind="own_topic", topic_text=title),
        current_stage=StageName.export,
    )
    project.stages[StageName.export].status = "done"
    project.stages[StageName.export].approved_by = approved_by
    write_file(folder / "job.json", project.model_dump_json(indent=2))
    return folder


def write_export(
    folder: Path,
    project_id: str,
    channel_slug: str,
    topic: str,
    export_folder: Path,
    *,
    exported: bool,
    exported_at: str,
    presets: list[str],
    seo_title: str,
    video_bytes: dict[str, int],
) -> None:
    files: list[ExportedFile] = []
    if exported:
        for preset in presets:
            name = f"{topic}_{preset}.mp4"
            size = video_bytes[preset]
            write_file(export_folder / name, b"\x00" * size)
            files.append(ExportedFile(name=name, source=f"07_edit/final_{preset}.mp4",
                                      bytes=size, kind="video"))
        write_file(export_folder / f"{topic}_thumbnail.png", b"png")
        files.append(ExportedFile(name=f"{topic}_thumbnail.png", source="08_export/v1.png",
                                  bytes=3, kind="thumbnail"))
    doc = ExportDoc(
        project_id=project_id,
        exported_at=stamp(exported_at),
        export_folder=str(export_folder),
        topic_slug=topic,
        presets=presets,
        exported=exported,
        files=files,
    )
    write_file(folder / "08_export" / "export.json", doc.model_dump_json(indent=2))
    metadata = ExportMetadata(
        project_id=project_id,
        channel_slug=channel_slug,
        format="long",
        language="English",
        title=seo_title,
        generated_at=stamp(exported_at),
    )
    write_file(folder / "08_export" / "metadata.json", metadata.model_dump_json(indent=2))


def add_audit(app_data_dir: Path, rows: list[dict[str, Any]]) -> None:
    """``export.done`` rows in the audit table of docs/M5A-CONTRACT.md section 1 (made here
    when the sign-in part has not made it yet)."""
    db.ensure_table(
        app_data_dir,
        "CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, "
        "user TEXT, machine TEXT, action TEXT, project_id TEXT, channel_slug TEXT, detail TEXT)",
    )
    with db.connect(app_data_dir) as conn:
        for row in rows:
            conn.execute(
                "INSERT INTO audit (at, user, machine, action, project_id, channel_slug, detail)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row["at"], row["user"], row["machine"], row.get("action", "export.done"),
                 row["project_id"], row["channel_slug"], json.dumps(row.get("detail", {}))),
            )


@dataclass
class Library:
    env: AppEnv
    music: Path
    sample: Path
    live_export: Path
    archived_export: Path


def build_library(env: AppEnv, tmp_path: Path) -> Library:
    shared = env.shared_dir
    music = tmp_path / "music"
    write_wav(music / "calm-piano.wav", 1.5)
    write_file(music / "calm-piano.wav.license.txt", "Licensed to the team, 2026.")
    write_wav(music / "drums.wav", 0.75)
    write_file(music / "notes.txt", "not a track")
    sample = write_wav(tmp_path / "voices" / "daniel-sample.wav", 0.5)

    frameworks = shared / "channels" / KIND / "frameworks"
    write_file(frameworks / "title.txt", "Title rules")
    write_file(frameworks / "script.pdf", b"%PDF-1.4 not really")
    write_file(frameworks / "script.pdf.extracted.txt", "Script rules")
    write_file(frameworks / "thumbs.pdf", b"%PDF-1.4 not read yet")
    write_file(shared / "channels" / CALM / "frameworks" / "style.md", "# Calm style")

    store = ChannelStore(shared)
    store.create(Channel.model_validate({
        "slug": KIND,
        "channel": {"name": "Kind Ledger", "music_folder": str(music)},
        "frameworks": [
            {"type": "title", "name": "Title rules", "version": "v2",
             "path": f"channels/{KIND}/frameworks/title.txt"},
            {"type": "script_long", "name": "Long script", "formats": "long",
             "path": f"channels/{KIND}/frameworks/script.pdf"},
            {"type": "thumbnail", "name": "Thumbnails",
             "path": f"channels/{KIND}/frameworks/thumbs.pdf"},
            {"type": "seo", "name": "SEO doc", "path": "https://docs.google.com/document/d/x"},
            {"type": "other", "name": "Lost", "path": f"channels/{KIND}/frameworks/lost.txt"},
        ],
        "voice": {
            "tool": "ai33", "clone_ref": "clone-123", "name": "Daniel", "language": "English",
            "sample_path": str(sample),
            "consent": {"owner_name": "Daniel Reed", "consented_by": "Imran",
                        "consented_at": "2026-09-01T10:00:00Z", "statement": "I agree."},
        },
    }))
    store.create(Channel.model_validate({
        "slug": CALM,
        # The same folder written another way: one scan, both channels.
        "channel": {"name": "Calm Harbor", "music_folder": str(music) + os.sep + "." + os.sep},
        "frameworks": [
            {"type": "style_guide", "name": "Calm style",
             "path": f"channels/{CALM}/frameworks/style.md"},
        ],
        "voice": {"tool": "minimax", "name": "Stock narrator", "language": "Spanish"},
    }))

    projects, exports = env.projects_dir, env.exports_dir
    live = make_project(projects, LIVE_ID, KIND, "first-video", "First video",
                        "2026-10-01T08:00:00+00:00")
    live_export = exports / KIND / "2026-10-01_first-video"
    write_export(live, LIVE_ID, KIND, "first-video", live_export, exported=True,
                 exported_at="2026-10-05T10:00:05+00:00", presets=["1080p"],
                 seo_title="First video, the SEO title", video_bytes={"1080p": 4000})

    archived = make_project(projects / "_archived", ARCHIVED_ID, CALM, "sea-story",
                            "Sea story", "2026-09-19T08:00:00+00:00", approved_by="Sara")
    archived_export = exports / CALM / "2026-09-19_sea-story"
    write_export(archived, ARCHIVED_ID, CALM, "sea-story", archived_export, exported=True,
                 exported_at="2026-09-20T08:00:00+00:00", presets=["1080p", "720p"],
                 seo_title="Sea story", video_bytes={"1080p": 3000, "720p": 1000})

    pending = make_project(projects, UNEXPORTED_ID, KIND, "not-yet", "Not yet",
                           "2026-10-06T08:00:00+00:00")
    write_export(pending, UNEXPORTED_ID, KIND, "not-yet", exports / KIND / "2026-10-06_not-yet",
                 exported=False, exported_at="2026-10-06T09:00:00+00:00", presets=["1080p"],
                 seo_title="Not yet", video_bytes={})

    add_audit(env.app_data_dir, [
        {"at": "2026-10-02T09:00:00+00:00", "user": "sara", "machine": "LAPTOP-2",
         "project_id": LIVE_ID, "channel_slug": KIND,
         "detail": {"presets": ["720p"], "duration_s": 60.0}},
        {"at": "2026-10-05T10:00:06+00:00", "user": "imran", "machine": "LAPTOP-1",
         "project_id": LIVE_ID, "channel_slug": KIND,
         "detail": {"presets": ["1080p"], "duration_s": 61.5, "size_bytes": 4000}},
        {"at": "2026-09-25T12:00:00Z", "user": "imran", "machine": "LAPTOP-1",
         "project_id": GONE_ID, "channel_slug": KIND,
         "detail": {"title": "Deleted by hand", "format": "shorts", "language": "English",
                    "presets": ["1080p"], "duration_s": 30, "size_bytes": 1234,
                    "export_folder": str(exports / KIND / "gone")}},
        {"at": "2026-10-07T12:00:00Z", "user": "imran", "machine": "LAPTOP-1",
         "action": "stage.approve", "project_id": UNEXPORTED_ID, "channel_slug": KIND},
    ])
    return Library(env, music, sample, live_export, archived_export)


# Fixtures (every TestClient signs in as the test admin: see conftest.py) -------------------------


@pytest.fixture
def library(app_env: AppEnv, tmp_path: Path) -> Library:
    return build_library(app_env, tmp_path)


@pytest.fixture
def probed(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Videos get their length from :data:`VIDEO_SECONDS`; audio goes to the real prober."""
    calls: list[Path] = []
    real = durations.prober_for

    def prober_for(settings: Any = None) -> durations.Prober:
        audio = real(settings)

        def probe(path: Path) -> float:
            calls.append(path)
            if path.suffix == ".mp4":
                return VIDEO_SECONDS[path.name]
            return audio(path)

        return probe

    monkeypatch.setattr(library_api, "prober_for", prober_for)
    return calls


@pytest.fixture
def api(library: Library, probed: list[Path]) -> Iterator[TestClient]:
    with TestClient(create_app()) as client:
        assert client.get("/api/auth/me").status_code == 200  # signed in as the test admin
        yield client


# Frameworks ------------------------------------------------------------------------------------


def test_frameworks_from_every_channel(api: TestClient, library: Library) -> None:
    response = api.get("/api/library/frameworks")
    assert response.status_code == 200, response.text
    rows = response.json()
    # Channels by name (Calm Harbor first), frameworks in the channel's own order.
    assert [(r["channel_slug"], r["name"]) for r in rows] == [
        (CALM, "Calm style"),
        (KIND, "Title rules"), (KIND, "Long script"), (KIND, "Thumbnails"), (KIND, "SEO doc"),
        (KIND, "Lost"),
    ]
    by_name = {r["name"]: r for r in rows}
    title = by_name["Title rules"]
    assert title["channel_name"] == "Kind Ledger" and title["type"] == "title"
    assert title["version"] == "v2" and title["formats"] == "both"
    assert title["path"] == f"channels/{KIND}/frameworks/title.txt"
    assert title["size_bytes"] == len("Title rules") and title["exists"] is True
    assert title["extracted"] is True  # plain text needs no extraction
    updated = datetime.fromisoformat(title["updated_at"].replace("Z", "+00:00"))
    expected = (library.env.shared_dir / title["path"]).stat().st_mtime
    assert abs(updated.timestamp() - expected) < 1.5

    assert by_name["Long script"]["extracted"] is True  # script.pdf.extracted.txt
    assert by_name["Long script"]["formats"] == "long"
    assert by_name["Thumbnails"]["extracted"] is False  # a PDF nobody has read yet
    assert by_name["Thumbnails"]["size_bytes"] == len(b"%PDF-1.4 not read yet")
    link = by_name["SEO doc"]
    assert link["is_link"] is True and link["size_bytes"] is None
    assert link["updated_at"] is None and link["extracted"] is False and link["problem"] is None
    lost = by_name["Lost"]
    assert lost["exists"] is False and lost["size_bytes"] is None
    assert "not in the shared folder" in lost["problem"]
    assert by_name["Calm style"]["extracted"] is True and by_name["Calm style"]["size_bytes"]


# Voices ----------------------------------------------------------------------------------------


def test_voices_with_consent_and_sample(api: TestClient, library: Library) -> None:
    response = api.get("/api/library/voices")
    assert response.status_code == 200, response.text
    calm, kind = response.json()
    assert kind["channel_slug"] == KIND and kind["channel_name"] == "Kind Ledger"
    assert kind["tool"] == "ai33" and kind["clone_ref"] == "clone-123"
    assert kind["name"] == "Daniel" and kind["language"] == "English"
    assert kind["sample_path"] == str(library.sample) and kind["sample_exists"] is True
    consent = kind["consent"]
    assert consent["present"] is True and consent["required"] is True
    assert consent["owner_name"] == "Daniel Reed"
    assert consent["consented_at"].startswith("2026-09-01T10:00:00")
    assert consent["problem"] is None and "Consent fields" in consent["ref"]

    # A stock voice: no clone, no sample, so no consent needed and none recorded.
    assert calm["channel_slug"] == CALM and calm["tool"] == "minimax"
    assert calm["language"] == "Spanish" and calm["sample_path"] == ""
    assert calm["sample_exists"] is False
    assert calm["consent"] == {
        "present": False, "owner_name": None, "consented_at": None, "required": False,
        "ref": None, "problem": None,
    }


# Music -----------------------------------------------------------------------------------------


def test_music_folder_scanned_once_for_both_channels(api: TestClient, library: Library) -> None:
    response = api.get("/api/library/music")
    assert response.status_code == 200, response.text
    tracks = response.json()
    assert [t["file"] for t in tracks] == ["calm-piano.wav", "drums.wav"]  # notes.txt is not music
    folder = os.path.abspath(library.music)
    for track in tracks:
        assert os.path.normcase(track["folder"]) == os.path.normcase(folder)
        assert track["used_by_channels"] == [CALM, KIND]
        assert track["size_bytes"] == (library.music / track["file"]).stat().st_size
    piano, drums = tracks
    assert piano["license_ok"] is True
    assert piano["license_file"] == "calm-piano.wav.license.txt"
    assert drums["license_ok"] is False and drums["license_file"] is None
    assert piano["duration_s"] == pytest.approx(1.5, abs=0.01)
    assert drums["duration_s"] == pytest.approx(0.75, abs=0.01)

    folders = api.get("/api/library/music/folders")
    assert folders.status_code == 200, folders.text
    (info,) = folders.json()
    assert info["exists"] is True and info["tracks"] == 2 and info["licensed_tracks"] == 1
    assert info["used_by_channels"] == [CALM, KIND]
    assert info["license_file"] is None and info["problem"] is None


def test_music_lengths_are_cached_in_sqlite(
    api: TestClient, library: Library, probed: list[Path]
) -> None:
    assert api.get("/api/library/music").status_code == 200
    assert sorted(p.name for p in probed) == ["calm-piano.wav", "drums.wav"]
    with sqlite3.connect(db.db_path(library.env.app_data_dir)) as conn:
        cached = dict(conn.execute("SELECT path, duration_s FROM music_cache").fetchall())
    assert {os.path.basename(path) for path in cached} == {"calm-piano.wav", "drums.wav"}
    probed.clear()
    again = api.get("/api/library/music").json()
    assert probed == []  # nothing measured twice
    assert again[0]["duration_s"] == pytest.approx(1.5, abs=0.01)
    # A replaced track (new size and time) is measured again.
    write_wav(library.music / "drums.wav", 2.0)
    os.utime(library.music / "drums.wav", ns=(1_900_000_000_000_000_000,) * 2)
    third = api.get("/api/library/music").json()
    assert [p.name for p in probed] == ["drums.wav"]
    assert third[1]["duration_s"] == pytest.approx(2.0, abs=0.01)


# Production log --------------------------------------------------------------------------------


def test_production_log_rows(api: TestClient, library: Library, probed: list[Path]) -> None:
    response = api.get("/api/production-log")
    assert response.status_code == 200, response.text
    rows = response.json()
    # Newest first; the export pack that was never copied is not a production.
    assert [r["project_id"] for r in rows] == [LIVE_ID, GONE_ID, ARCHIVED_ID]
    live, gone, archived = rows

    # The latest export.done row says who and where; the files say what.
    assert live["at"] == "2026-10-05T10:00:06Z"
    assert live["user"] == "imran" and live["machine"] == "LAPTOP-1"
    assert live["channel_slug"] == KIND and live["title"] == "First video, the SEO title"
    assert live["format"] == "long" and live["language"] == "English"
    assert live["presets"] == ["1080p"] and live["size_bytes"] == 4000
    assert live["duration_s"] == 61.5 and live["archived"] is False
    assert live["export_folder"] == str(library.live_export)

    # Exported before sign-in existed: the reviewer typed on the approval, no laptop; the
    # length is measured from the exported file.
    assert archived["at"] == "2026-09-20T08:00:00Z"
    assert archived["user"] == "Sara" and archived["machine"] == ""
    assert archived["channel_slug"] == CALM and archived["archived"] is True
    assert archived["presets"] == ["1080p", "720p"] and archived["size_bytes"] == 4000
    assert archived["duration_s"] == 45.25
    assert archived["export_folder"] == str(library.archived_export)
    assert [p.name for p in probed if p.suffix == ".mp4"] == ["sea-story_1080p.mp4"]

    # The project folder is gone: the audit row's details are all there is.
    assert gone == {
        "at": "2026-09-25T12:00:00Z", "user": "imran", "machine": "LAPTOP-1",
        "channel_slug": KIND, "title": "Deleted by hand", "project_id": GONE_ID,
        "format": "shorts", "language": "English", "presets": ["1080p"], "duration_s": 30.0,
        "size_bytes": 1234, "export_folder": str(library.env.exports_dir / KIND / "gone"),
        "archived": False,
    }

    # The measured length is cached: a second load does not run ffprobe again.
    probed.clear()
    assert api.get("/api/production-log").json()[2]["duration_s"] == 45.25
    assert probed == []


def test_production_log_filters(api: TestClient) -> None:
    calm = api.get("/api/production-log", params={"channel_slug": CALM}).json()
    assert [r["project_id"] for r in calm] == [ARCHIVED_ID]
    assert api.get("/api/production-log", params={"channel_slug": "nobody"}).json() == []

    since = api.get("/api/production-log", params={"since": "2026-09-25T12:00:00Z"}).json()
    assert [r["project_id"] for r in since] == [LIVE_ID, GONE_ID]
    by_date = api.get("/api/production-log", params={"since": "2026-10-03"}).json()
    assert [r["project_id"] for r in by_date] == [LIVE_ID]
    both = api.get("/api/production-log", params={"since": "2026-09-01", "channel_slug": KIND})
    assert [r["project_id"] for r in both.json()] == [LIVE_ID, GONE_ID]

    bad = api.get("/api/production-log", params={"since": "last tuesday"})
    assert bad.status_code == 422
    assert "is not a date" in bad.json()["detail"] and "2026-10-01" in bad.json()["detail"]
    assert api.get("/api/production-log.csv", params={"since": "soon"}).status_code == 422


def test_production_log_csv(api: TestClient, library: Library) -> None:
    response = api.get("/api/production-log.csv")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="production-log-')
    assert disposition.endswith('.csv"')
    assert response.content.startswith(b"\xef\xbb\xbf")  # Excel reads it as UTF-8
    table = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert table[0] == [
        "at", "user", "machine", "channel_slug", "title", "project_id", "format", "language",
        "presets", "duration_s", "size_bytes", "export_folder",
    ]
    assert [row[5] for row in table[1:]] == [LIVE_ID, GONE_ID, ARCHIVED_ID]
    assert table[1] == [
        "2026-10-05T10:00:06Z", "imran", "LAPTOP-1", KIND, "First video, the SEO title",
        LIVE_ID, "long", "English", "1080p", "61.5", "4000", str(library.live_export),
    ]
    assert table[3][8] == "1080p, 720p" and table[3][1] == "Sara" and table[3][2] == ""
    filtered = api.get("/api/production-log.csv", params={"channel_slug": CALM})
    assert len(list(csv.reader(io.StringIO(filtered.content.decode("utf-8-sig"))))) == 2


# Sign-in and roles -------------------------------------------------------------------------------


@pytest.mark.signed_out
def test_library_needs_a_signed_in_user(library: Library) -> None:
    with TestClient(create_app()) as anonymous:
        assert anonymous.get("/api/auth/me").status_code == 401
        for url in URLS:
            response = anonymous.get(url)
            assert response.status_code == 401, url
            assert response.json() == {"detail": "Please sign in."}


def test_every_role_can_read_the_libraries(
    library: Library, probed: list[Path], viewer_client: TestClient
) -> None:
    """The libraries and the log are read-only, so a viewer sees them as an admin does."""
    me = viewer_client.get("/api/auth/me").json()
    assert me["role"] == "viewer"
    for url in URLS:
        response = viewer_client.get(url)
        assert response.status_code == 200, (url, response.text)
    rows = viewer_client.get("/api/production-log").json()
    assert [r["project_id"] for r in rows] == [LIVE_ID, GONE_ID, ARCHIVED_ID]
