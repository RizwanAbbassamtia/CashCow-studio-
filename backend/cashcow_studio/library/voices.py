"""The voices library: the voice each channel narrates with, its consent record and sample.

The consent verdict is the voice step's own (``pipeline/stages/voice.py``), so the page and
the ``voice.consent`` gate never disagree:

* ``required``: the channel names a cloned voice (``voice.clone_ref``) or a voice sample
  (``voice.sample_path``), as the rule's ``require_when`` parameter says; a voice with
  neither is a stock voice of the tool and needs no record;
* ``present``: a complete record exists, either the Consent fields of the channel's Voice
  tab or a ``consent.json`` next to the sample or in the channel folder.

The sample path is a file on this laptop (``D:\\Voices\\daniel.mp3``); a relative path is
looked up in the shared folder. Only its existence is checked, the file is never read.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..models.channel import Channel, Language, VoiceTool
from ..pipeline.stages.voice import consent_status
from ..policy import gates

log = logging.getLogger(__name__)

CONSENT_RULE = "voice.consent"
MISSING_CONSENT = (
    "This voice is a clone, so the voice step needs a consent record: fill in the Consent "
    "fields on the channel's Voice tab, or put a consent.json next to the voice sample."
)


class VoiceConsentInfo(BaseModel):
    present: bool = Field(default=False, description="a complete consent record was found")
    owner_name: str | None = None
    consented_at: datetime | None = None
    required: bool = Field(
        default=False, description="the voice is a clone, so the voice step needs the record"
    )
    ref: str | None = Field(default=None, description="where the record was found")
    problem: str | None = Field(default=None, description="plain English, when something is off")


class VoiceEntry(BaseModel):
    """One row of ``GET /api/library/voices`` (one per channel)."""

    channel_slug: str
    channel_name: str
    tool: VoiceTool
    clone_ref: str = ""
    name: str = ""
    language: Language = "English"
    consent: VoiceConsentInfo = VoiceConsentInfo()
    sample_path: str = ""
    sample_exists: bool = False


def consent_rule_params() -> dict[str, Any]:
    """The ``voice.consent`` rule's parameters (``require_when``); defaults if unreadable."""
    try:
        return gates.params(CONSENT_RULE)
    except Exception:  # noqa: BLE001 - a broken rules file must not take the page down
        log.exception("The %s rule could not be read; using its defaults", CONSENT_RULE)
        return {}


def sample_file(sample_path: str, shared_dir: Path) -> Path | None:
    """The voice sample on this laptop: a full path as typed (``%USERPROFILE%`` and ``~``
    work), a relative path inside the shared folder."""
    raw = (sample_path or "").strip().strip('"')
    if not raw:
        return None
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    return path if path.is_absolute() else Path(shared_dir) / path


def _incomplete_fields(channel: Channel) -> list[str]:
    """What the Voice tab's Consent fields still miss once an owner is filled in."""
    inline = channel.voice.consent
    if not inline.owner_name.strip():
        return []
    missing = []
    if not inline.consented_by.strip():
        missing.append("who recorded the consent")
    if inline.consented_at is None:
        missing.append("the date of the consent")
    return missing


def _consent(channel: Channel, channels_dir: Path, params: dict[str, Any]) -> VoiceConsentInfo:
    required = consent_status(channel, channels_dir, params).is_clone
    found = consent_status(channel, channels_dir, {**params, "require_when": "always"})
    record = found.record or {}
    problem: str | None = None
    if not found.found:
        missing = _incomplete_fields(channel)
        if missing:
            problem = (
                "The Consent fields on the channel's Voice tab are missing "
                + " and ".join(missing) + "."
            )
        elif found.error:
            problem = found.error[:1].upper() + found.error[1:].rstrip(".") + "."
        elif required:
            problem = MISSING_CONSENT
    return VoiceConsentInfo.model_validate({
        "present": found.found,
        "owner_name": (record.get("owner_name") or None) if found.found else None,
        "consented_at": (record.get("consented_at") or None) if found.found else None,
        "required": required,
        "ref": found.ref or None,
        "problem": problem,
    })


def voice_entry(
    channel: Channel, shared_dir: Path, channels_dir: Path, params: dict[str, Any]
) -> VoiceEntry:
    voice = channel.voice
    sample = sample_file(voice.sample_path, shared_dir)
    try:
        sample_exists = sample is not None and sample.is_file()
    except OSError:
        sample_exists = False
    return VoiceEntry(
        channel_slug=channel.slug,
        channel_name=channel.channel.name,
        tool=voice.tool,
        clone_ref=voice.clone_ref,
        name=voice.name,
        language=voice.language,
        consent=_consent(channel, channels_dir, params),
        sample_path=voice.sample_path,
        sample_exists=sample_exists,
    )


def list_voices(channels: Iterable[Channel], shared_dir: Path | str) -> list[VoiceEntry]:
    """One row per channel, channels by name."""
    root = Path(shared_dir)
    channels_dir = root / "channels"
    params = consent_rule_params()
    rows: list[VoiceEntry] = []
    for channel in channels:
        try:
            rows.append(voice_entry(channel, root, channels_dir, params))
        except Exception:  # noqa: BLE001 - one odd channel must not hide the others
            log.exception("The voice of %s could not be listed", channel.slug)
    return rows
