"""The channels the libraries are gathered from: every channel the Channels page lists."""

from __future__ import annotations

import logging

from ..models.channel import Channel
from ..storage.channel_store import ChannelStore, ChannelStoreError

log = logging.getLogger(__name__)


def load_channels(store: ChannelStore) -> list[Channel]:
    """Every readable channel in the shared folder, sorted by name like the Channels page.

    Archived channels (``channels/_archived/``) are left out. A channel file that cannot be
    read right now (a Drive sync in progress, a hand edit gone wrong) is skipped with a log
    line, so one odd channel never takes a whole library page down.
    """
    channels: list[Channel] = []
    for summary in store.list():
        try:
            channels.append(store.get(summary.slug))
        except (ChannelStoreError, OSError) as exc:
            log.warning("Channel %s left out of the libraries: %s", summary.slug, exc)
    return channels
