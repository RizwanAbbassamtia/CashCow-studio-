"""The Master Sheet libraries and the production log (docs/M5A-CONTRACT.md section 2).

| Method | Path | Response |
|---|---|---|
| GET | ``/api/library/frameworks`` | ``FrameworkEntry[]``: every channel's framework files |
| GET | ``/api/library/voices`` | ``VoiceEntry[]``: every channel's voice, consent and sample |
| GET | ``/api/library/music`` | ``MusicTrack[]``: every track of every channel music folder |
| GET | ``/api/library/music/folders`` | ``MusicFolderInfo[]``: whether each folder can be used |
| GET | ``/api/production-log`` | ``ProductionLogRow[]``, newest first; ``?channel_slug=&since=`` |
| GET | ``/api/production-log.csv`` | the same rows as a CSV download for the team sheet |

Everything is read-only and open to every signed-in user, whatever their role. The work is
in :mod:`cashcow_studio.library`; the handlers are plain functions, so FastAPI runs them in
its worker threads and a first music scan (ffprobe on every track) never stalls the event
stream.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from ..config import Settings
from ..library.channels import load_channels
from ..library.durations import MUSIC_TABLE, DurationCache, prober_for
from ..library.frameworks import FrameworkEntry, list_frameworks
from ..library.music import MusicFolderInfo, MusicLibrary, MusicTrack, scan_music
from ..library.production_log import (
    InvalidSince,
    ProductionLogRow,
    build_production_log,
    parse_since,
    to_csv,
)
from ..library.voices import VoiceEntry, list_voices
from ..models.channel import Channel
from ..storage.channel_store import ChannelStore
from .deps import ChannelStoreDep, CurrentUser, SettingsDep

router = APIRouter(prefix="/api", tags=["library"])

CSV_MEDIA_TYPE = "text/csv"

ChannelSlugQuery = Annotated[
    str | None, Query(description="only this channel's videos", max_length=120)
]
SinceQuery = Annotated[
    str | None,
    Query(description="only videos exported since: a date (2026-10-01) or an ISO time",
          max_length=64),
]


def _channels(store: ChannelStore) -> list[Channel]:
    try:
        return load_channels(store)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"The channels in the shared folder could not be read: {exc}. If the "
            "shared folder is in Google Drive, check that Drive is running.",
        ) from exc


def _music(settings: Settings, store: ChannelStore, *, lengths: bool) -> MusicLibrary:
    return scan_music(
        _channels(store),
        DurationCache(settings.app_data_dir, MUSIC_TABLE),
        prober_for(settings),
        measure_lengths=lengths,
    )


@router.get("/library/frameworks", response_model=list[FrameworkEntry])
def library_frameworks(
    settings: SettingsDep, store: ChannelStoreDep, _user: CurrentUser
) -> list[FrameworkEntry]:
    return list_frameworks(_channels(store), settings.resolved_shared_dir, settings.app_data_dir)


@router.get("/library/voices", response_model=list[VoiceEntry])
def library_voices(
    settings: SettingsDep, store: ChannelStoreDep, _user: CurrentUser
) -> list[VoiceEntry]:
    return list_voices(_channels(store), settings.resolved_shared_dir)


@router.get("/library/music", response_model=list[MusicTrack])
def library_music(
    settings: SettingsDep, store: ChannelStoreDep, _user: CurrentUser
) -> list[MusicTrack]:
    return _music(settings, store, lengths=True).tracks


@router.get("/library/music/folders", response_model=list[MusicFolderInfo])
def library_music_folders(
    settings: SettingsDep, store: ChannelStoreDep, _user: CurrentUser
) -> list[MusicFolderInfo]:
    return _music(settings, store, lengths=False).folders


def _production_log(
    settings: Settings, channel_slug: str | None, since: str | None
) -> list[ProductionLogRow]:
    try:
        floor = parse_since(since)
    except InvalidSince as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return build_production_log(
            settings.projects_dir,
            settings.app_data_dir,
            prober=prober_for(settings),
            channel_slug=channel_slug,
            since=floor,
        )
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"The project folders could not be read: {exc}. Check that the projects "
            "folder in Settings is available.",
        ) from exc


@router.get("/production-log", response_model=list[ProductionLogRow])
def production_log(
    settings: SettingsDep,
    _user: CurrentUser,
    channel_slug: ChannelSlugQuery = None,
    since: SinceQuery = None,
) -> list[ProductionLogRow]:
    return _production_log(settings, channel_slug, since)


@router.get(
    "/production-log.csv",
    response_class=Response,
    responses={200: {"content": {CSV_MEDIA_TYPE: {}}, "description": "the log as CSV"}},
)
def production_log_csv(
    settings: SettingsDep,
    _user: CurrentUser,
    channel_slug: ChannelSlugQuery = None,
    since: SinceQuery = None,
) -> Response:
    rows = _production_log(settings, channel_slug, since)
    filename = f"production-log-{date.today().isoformat()}.csv"
    return Response(
        content=to_csv(rows).encode("utf-8"),
        media_type=CSV_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
