"""Team accounts, sign-in sessions and the audit log (docs/M5A-CONTRACT.md section 1).

* :mod:`.passwords`  PBKDF2-HMAC-SHA256 hashes with a per-user salt (the SceneForge scheme) and
  generated one-time passwords that are strong but easy to type;
* :mod:`.users`      the team roster, one file per account in ``<shared_dir>/team/users/``,
  mirrored to ``<app_data_dir>/users.cache.json`` so a laptop can still sign people in when
  the shared folder cannot be reached;
* :mod:`.sessions`   the signed ``ccs_session`` cookie, the per-laptop secret, this laptop's
  list of sessions (so signing out ends a session on the server) and "remember my login
  details" (the username only);
* :mod:`.limits`     at most five failed sign-ins per account per ten minutes on this laptop;
* :mod:`.audit`      who did what, when and on which laptop (SQLite table ``audit``);
* :mod:`.middleware` the sign-in check in front of every ``/api`` request and the WebSocket.

Passwords, hashes, salts and the session secret are never logged, returned or written to the
audit log.
"""

from __future__ import annotations

import os
import platform
import socket

MAX_MACHINE_LENGTH = 64
FALLBACK_MACHINE = "this-laptop"


def machine_name() -> str:
    """The Windows computer name of this laptop (``COMPUTERNAME``), else the host name."""
    for candidate in (os.environ.get("COMPUTERNAME"), _node(), _hostname()):
        name = (candidate or "").strip()
        if name:
            return name[:MAX_MACHINE_LENGTH]
    return FALLBACK_MACHINE


def same_machine(first: str | None, second: str | None) -> bool:
    """Computer names compare without regard to case or surrounding spaces."""
    return (first or "").strip().casefold() == (second or "").strip().casefold()


def _node() -> str:
    try:
        return platform.node()
    except Exception:  # noqa: BLE001 - a broken platform call must not stop a sign-in
        return ""


def _hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return ""
