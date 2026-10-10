"""The activity log (SQLite ``audit``) and the account first-aid command line."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cashcow_studio.auth import audit, machine_name
from cashcow_studio.auth.__main__ import main as first_aid
from cashcow_studio.auth.users import UserStore
from cashcow_studio.config import load_settings
from cashcow_studio.storage import db
from conftest import AppEnv


def test_rows_round_trip_newest_first_with_filters(tmp_path: Path) -> None:
    start = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
    audit.record(tmp_path, "login", "imran", at=start)
    audit.record(
        tmp_path, "stage.approve", "maria", machine="LAPTOP-7", project_id="p1",
        channel_slug="kind-ledger", detail={"stage": "title"}, at=start + timedelta(minutes=1),
    )
    row = audit.record(
        tmp_path, "export.done", "maria", project_id="p1", channel_slug="kind-ledger",
        detail={"presets": ["1080p"], "size_bytes": 1234}, at=start + timedelta(minutes=2),
    )
    assert row is not None and row.id > 0 and row.machine == machine_name()

    rows = audit.query(tmp_path)
    assert [r.action for r in rows] == ["export.done", "stage.approve", "login"]
    assert rows[0].at == start + timedelta(minutes=2) and rows[0].detail["size_bytes"] == 1234
    assert rows[1].machine == "LAPTOP-7" and rows[1].detail == {"stage": "title"}
    assert [r.action for r in audit.query(tmp_path, user="maria")] == [
        "export.done", "stage.approve"
    ]
    assert [r.action for r in audit.query(tmp_path, action="login")] == ["login"]
    assert len(audit.query(tmp_path, actions=["login", "export.done"])) == 2
    assert len(audit.query(tmp_path, project_id="p1", channel_slug="kind-ledger")) == 2
    assert [r.action for r in audit.query(tmp_path, since=start + timedelta(seconds=90))] == [
        "export.done"
    ]
    assert len(audit.query(tmp_path, limit=1)) == 1

    with db.connect(tmp_path) as conn:
        columns = [r["name"] for r in conn.execute("PRAGMA table_info(audit)").fetchall()]
    assert columns == [
        "id", "at", "user", "machine", "action", "project_id", "channel_slug", "detail"
    ]


def test_a_broken_database_never_breaks_the_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_folder: Path) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(audit.db, "connect", refuse)
    monkeypatch.setattr(audit, "ensure_audit_table", lambda _folder: None)
    assert audit.record(tmp_path, "login", "imran") is None
    assert audit.query(tmp_path) == []


def test_odd_rows_and_oversized_details_are_handled(tmp_path: Path) -> None:
    audit.ensure_audit_table(tmp_path)
    with db.connect(tmp_path) as conn:
        conn.execute(
            'INSERT INTO audit ("at", "user", "machine", "action", "detail") '
            "VALUES ('yesterday', 'x', 'y', 'login', 'not json')"
        )
    big = audit.record(tmp_path, "settings.update", "imran", detail={"text": "x" * 20_000})
    assert big is not None and big.detail["truncated"] is True
    odd = audit.query(tmp_path, user="x")[0]
    assert odd.at == audit.EPOCH and odd.detail == {}


def test_first_aid_lists_resets_and_turns_accounts_back_on(
    app_env: AppEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = load_settings()
    settings.ensure_dirs()
    store = UserStore(settings.resolved_shared_dir, settings.app_data_dir)
    assert first_aid(["list"]) == 0
    assert "No accounts" in capsys.readouterr().out

    store.create_first_admin("imran", "Imran", "first-admin-pass")
    store.add("maria", "Maria", "admin")
    store.set_disabled("imran", True)
    assert first_aid(["list"]) == 0
    listing = capsys.readouterr().out
    assert "imran" in listing and "turned off" in listing and "maria" in listing

    assert first_aid(["reset-password", "imran"]) == 0
    printed = capsys.readouterr().out
    password = printed.split("New one-time password for imran: ", 1)[1].split()[0]
    assert store.verify("imran", password) is not None
    assert store.refresh().users["imran"].must_change_password is True

    assert first_aid(["enable", "imran"]) == 0
    assert "turned on again" in capsys.readouterr().out
    assert store.refresh().users["imran"].disabled is False
    assert first_aid(["enable", "imran"]) == 0
    assert "already turned on" in capsys.readouterr().out

    assert first_aid(["reset-password", "ghost"]) == 1
    assert "no such account" in capsys.readouterr().err
    rows = audit.query(settings.app_data_dir)
    assert [(r.action, r.user) for r in reversed(rows)] == [
        ("user.reset", "command-line"), ("user.update", "command-line")
    ]
    assert password not in str([r.model_dump() for r in rows])
