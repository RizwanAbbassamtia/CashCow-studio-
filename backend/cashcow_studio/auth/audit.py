"""Who did what, when and on which laptop: table ``audit`` in ``<app_data_dir>/db.sqlite``.

One row per action, written by the API (and by the export stage for ``export.done``)::

    audit (id, at, user, machine, action, project_id, channel_slug, detail json)

``at`` is an ISO 8601 UTC time (``2026-10-10T09:15:02+00:00``), ``user`` the username,
``machine`` the Windows computer name, ``detail`` a small JSON object. Actions:
``login``, ``logout``, ``stage.approve``, ``stage.redo``, ``stage.skip``, ``stage.mode``,
``project.create``, ``project.archive``, ``export.done``, ``settings.update``,
``keys.update``, ``channel.create``, ``channel.update``, ``channel.archive``,
``user.create``, ``user.update``, ``user.reset``, ``user.disable``.

A row never holds a password, hash or API key value (``keys.update`` names the keys only).
Writing a row never fails the action it describes: a database problem is logged instead.
The log is per laptop, like the database it lives in.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..storage import db
from . import machine_name

log = logging.getLogger(__name__)

ACTIONS: tuple[str, ...] = (
    "login",
    "logout",
    "stage.approve",
    "stage.redo",
    "stage.skip",
    "stage.mode",
    "project.create",
    "project.archive",
    "export.done",
    "settings.update",
    "keys.update",
    "channel.create",
    "channel.update",
    "channel.archive",
    "user.create",
    "user.update",
    "user.reset",
    "user.disable",
)

CREATE_AUDIT = """
CREATE TABLE IF NOT EXISTS audit (
    "id" INTEGER PRIMARY KEY AUTOINCREMENT,
    "at" TEXT NOT NULL,
    "user" TEXT NOT NULL DEFAULT '',
    "machine" TEXT NOT NULL DEFAULT '',
    "action" TEXT NOT NULL,
    "project_id" TEXT,
    "channel_slug" TEXT,
    "detail" TEXT NOT NULL DEFAULT '{}'
)
"""
CREATE_INDEXES = (
    'CREATE INDEX IF NOT EXISTS audit_at ON audit ("at")',
    'CREATE INDEX IF NOT EXISTS audit_user_at ON audit ("user", "at")',
    'CREATE INDEX IF NOT EXISTS audit_action_at ON audit ("action", "at")',
    'CREATE INDEX IF NOT EXISTS audit_project ON audit ("project_id")',
)
MAX_DETAIL_CHARS = 16_000
MAX_LIMIT = 5000

_READY: set[str] = set()
_READY_LOCK = threading.Lock()


class AuditRow(BaseModel):
    id: int
    at: datetime
    user: str
    machine: str
    action: str
    project_id: str | None = None
    channel_slug: str | None = None
    detail: dict[str, Any] = {}


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _stamp(value: datetime) -> str:
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def ensure_audit_table(app_data_dir: Path | str) -> None:
    """Create the table (and its indexes) once per data folder."""
    key = str(Path(app_data_dir))
    with _READY_LOCK:
        if key in _READY and db.db_path(Path(app_data_dir)).is_file():
            return
    with db.connect(Path(app_data_dir)) as conn:
        conn.execute(CREATE_AUDIT)
        for statement in CREATE_INDEXES:
            conn.execute(statement)
    with _READY_LOCK:
        _READY.add(key)


def _detail_text(detail: dict[str, Any] | None) -> str:
    if not detail:
        return "{}"
    try:
        text = json.dumps(detail, ensure_ascii=False, default=str, sort_keys=True)
    except (TypeError, ValueError):
        return json.dumps({"note": "The details could not be stored."})
    if len(text) > MAX_DETAIL_CHARS:
        return json.dumps({"note": "The details were too long to store.", "truncated": True})
    return text


def record(
    app_data_dir: Path | str,
    action: str,
    user: str,
    *,
    machine: str | None = None,
    project_id: str | None = None,
    channel_slug: str | None = None,
    detail: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> AuditRow | None:
    """Write one row; returns it, or ``None`` when the database could not be written."""
    moment = at or utc_now()
    row = {
        "at": _stamp(moment),
        "user": str(user or ""),
        "machine": str(machine if machine is not None else machine_name()),
        "action": str(action),
        "project_id": project_id or None,
        "channel_slug": channel_slug or None,
        "detail": _detail_text(detail),
    }
    try:
        ensure_audit_table(app_data_dir)
        with db.connect(Path(app_data_dir)) as conn:
            cursor = conn.execute(
                'INSERT INTO audit ("at", "user", "machine", "action", "project_id", '
                '"channel_slug", "detail") VALUES (?, ?, ?, ?, ?, ?, ?)',
                (
                    row["at"],
                    row["user"],
                    row["machine"],
                    row["action"],
                    row["project_id"],
                    row["channel_slug"],
                    row["detail"],
                ),
            )
            row_id = int(cursor.lastrowid or 0)
    except (sqlite3.Error, OSError) as exc:
        log.warning("The activity log could not record '%s': %s", action, exc)
        return None
    return _to_row({**row, "id": row_id})


def query(
    app_data_dir: Path | str,
    *,
    limit: int | None = 200,
    user: str | None = None,
    action: str | None = None,
    actions: Iterable[str] | None = None,
    project_id: str | None = None,
    channel_slug: str | None = None,
    since: datetime | str | None = None,
) -> list[AuditRow]:
    """Rows newest first, filtered by any of the arguments (``limit=None``: all of them,
    up to :data:`MAX_LIMIT`)."""
    clauses: list[str] = []
    values: list[Any] = []
    if user:
        clauses.append('"user" = ?')
        values.append(user)
    if action:
        clauses.append('"action" = ?')
        values.append(action)
    wanted = [str(a) for a in actions or () if a]
    if wanted:
        clauses.append('"action" IN (' + ", ".join("?" for _ in wanted) + ")")
        values.extend(wanted)
    if project_id:
        clauses.append('"project_id" = ?')
        values.append(project_id)
    if channel_slug:
        clauses.append('"channel_slug" = ?')
        values.append(channel_slug)
    if since is not None:
        moment = datetime.fromisoformat(since) if isinstance(since, str) else since
        clauses.append('"at" >= ?')
        values.append(_stamp(moment))
    count = MAX_LIMIT if limit is None else max(1, min(int(limit), MAX_LIMIT))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        'SELECT "id", "at", "user", "machine", "action", "project_id", "channel_slug", '
        f'"detail" FROM audit{where} ORDER BY "at" DESC, "id" DESC LIMIT ?'
    )
    try:
        ensure_audit_table(app_data_dir)
        with db.connect(Path(app_data_dir)) as conn:
            rows = conn.execute(sql, (*values, count)).fetchall()
    except (sqlite3.Error, OSError) as exc:
        log.warning("The activity log could not be read: %s", exc)
        return []
    return [_to_row(dict(row)) for row in rows]


EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _parse_at(value: Any) -> datetime:
    """A stored time; a row written by hand with an unreadable time sorts as the oldest."""
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return EPOCH
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _to_row(data: dict[str, Any]) -> AuditRow:
    try:
        detail = json.loads(data.get("detail") or "{}")
    except (TypeError, ValueError):
        detail = {}
    if not isinstance(detail, dict):
        detail = {"value": detail}
    return AuditRow(
        id=int(data.get("id") or 0),
        at=_parse_at(data.get("at")),
        user=str(data.get("user") or ""),
        machine=str(data.get("machine") or ""),
        action=str(data.get("action") or ""),
        project_id=data.get("project_id") or None,
        channel_slug=data.get("channel_slug") or None,
        detail=detail,
    )
