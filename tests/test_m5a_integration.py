"""Seams between the M5A pieces: the ``ccs users`` first-aid commands, the app window's
browser profile, the Windows-safe file replace every JSON write goes through, and the API
description without duplicate ids."""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cashcow_studio import cli
from cashcow_studio.auth.users import UserStore
from cashcow_studio.config import load_settings
from cashcow_studio.storage import settings_store
from conftest import AppEnv


def test_ccs_users_commands_give_account_first_aid(app_env: AppEnv) -> None:
    settings = load_settings()
    settings.ensure_dirs()
    store = UserStore(settings.resolved_shared_dir, settings.app_data_dir)
    store.create_first_admin("imran", "Imran", "first-admin-pass")
    store.add("maria", "Maria", "admin")  # the last active admin cannot be turned off
    store.set_disabled("imran", True)
    runner = CliRunner()

    listed = runner.invoke(cli.app, ["users", "list"])
    assert listed.exit_code == 0, listed.output
    assert "imran" in listed.output and "turned off" in listed.output

    reset = runner.invoke(cli.app, ["users", "reset-password", "imran"])
    assert reset.exit_code == 0, reset.output
    password = reset.output.split("New one-time password for imran: ", 1)[1].split()[0]
    assert store.verify("imran", password) is not None

    enabled = runner.invoke(cli.app, ["users", "enable", "imran"])
    assert enabled.exit_code == 0 and "turned on again" in enabled.output
    assert store.refresh().users["imran"].disabled is False

    missing = runner.invoke(cli.app, ["users", "reset-password", "ghost"])
    assert missing.exit_code == 1


def test_ccs_users_unlock_frees_an_admin_locked_to_a_lost_laptop(
    app_env: AppEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = load_settings()
    settings.ensure_dirs()
    store = UserStore(settings.resolved_shared_dir, settings.app_data_dir)
    store.create_first_admin("imran", "Imran", "first-admin-pass")
    # Locked on the laptop it was made on, which is then lost or renamed.
    monkeypatch.setenv("COMPUTERNAME", "OLD-LAPTOP")
    store.set_machine("imran", "OLD-LAPTOP")
    monkeypatch.setenv("COMPUTERNAME", "NEW-LAPTOP")
    runner = CliRunner()
    unlocked = runner.invoke(cli.app, ["users", "unlock", "imran"])
    assert unlocked.exit_code == 0, unlocked.output
    assert "any laptop" in unlocked.output
    assert store.refresh().users["imran"].machine is None
    again = runner.invoke(cli.app, ["users", "unlock", "imran"])
    assert again.exit_code == 0 and "not locked" in again.output
    assert runner.invoke(cli.app, ["users", "unlock", "ghost"]).exit_code == 1


class FakeWebview:
    """Stands in for pywebview: records how the window is started."""

    def __init__(self) -> None:
        self.settings: dict[str, object] = {"ALLOW_DOWNLOADS": False}
        self.windows: list[tuple[str, str]] = []
        self.started: dict[str, object] = {}

    def create_window(self, title: str, url: str, **_size: object) -> None:
        self.windows.append((title, url))

    def start(self, **options: object) -> None:
        self.started = options


def test_the_app_window_keeps_its_cookies_between_starts(
    app_env: AppEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pywebview's default private mode deletes every cookie at each start, which would end
    "Keep me signed in" whenever the app is closed; the window uses a profile of its own."""
    fake = FakeWebview()
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(cli, "check_webview2", lambda: SimpleNamespace(status="ok"))
    profile = app_env.app_data_dir / "webview"
    assert cli._open_native_window("http://127.0.0.1:8790", profile) is True
    assert fake.started == {"private_mode": False, "storage_path": str(profile)}
    assert profile.is_dir() and fake.settings["ALLOW_DOWNLOADS"] is True

    # `ccs open` (and `python -m cashcow_studio`) hand the window that profile folder.
    opened: list[Path] = []
    monkeypatch.setattr(cli, "_our_server_answers", lambda port: True)
    monkeypatch.setattr(
        cli, "_open_native_window", lambda url, folder: opened.append(folder) or True
    )
    result = CliRunner().invoke(cli.app, ["open", "--port", "8790"])
    assert result.exit_code == 0, result.output
    assert opened == [load_settings().app_data_dir / "webview"]


def test_a_busy_file_is_replaced_once_windows_lets_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "job.json"
    target.write_text("old", encoding="utf-8")
    real_replace = os.replace
    refusals = {"left": 2}

    def busy_then_free(source: str | os.PathLike[str], dest: str | os.PathLike[str]) -> None:
        if refusals["left"]:
            refusals["left"] -= 1
            raise PermissionError(13, "Access is denied")
        real_replace(source, dest)

    monkeypatch.setattr(settings_store, "RETRY_REFUSED_REPLACE", True)
    monkeypatch.setattr(settings_store.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(settings_store.os, "replace", busy_then_free)
    settings_store.atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert refusals["left"] == 0
    assert [p.name for p in tmp_path.iterdir()] == ["job.json"]  # no temp file left behind


def test_a_file_that_stays_locked_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "job.json"
    target.write_text("old", encoding="utf-8")
    attempts: list[str] = []

    def always_busy(source: str | os.PathLike[str], dest: str | os.PathLike[str]) -> None:
        attempts.append(str(dest))
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(settings_store, "RETRY_REFUSED_REPLACE", True)
    monkeypatch.setattr(settings_store.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(settings_store.os, "replace", always_busy)
    with pytest.raises(PermissionError):
        settings_store.atomic_write_text(target, "new")
    assert len(attempts) == settings_store.REPLACE_ATTEMPTS
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["job.json"]

    # Elsewhere a refusal is real at once (no waiting on a permission that will not change).
    attempts.clear()
    monkeypatch.setattr(settings_store, "RETRY_REFUSED_REPLACE", False)
    with pytest.raises(PermissionError):
        settings_store.atomic_write_text(target, "new")
    assert len(attempts) == 1


def test_the_api_description_builds_without_duplicate_ids(client: TestClient) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        schema = client.get("/api/openapi.json").json()
    files = schema["paths"]["/api/projects/{project_id}/files/{path}"]
    assert set(files) == {"get"}
    ids = [
        operation["operationId"]
        for methods in schema["paths"].values()
        for operation in methods.values()
    ]
    assert len(ids) == len(set(ids))


def test_project_files_still_answer_head_requests_with_a_session(client: TestClient) -> None:
    # Unknown project: the route answers (404), it is not missing (405).
    assert client.head("/api/projects/nope/files/a.txt").status_code == 404
    client.cookies.clear()
    assert client.head("/api/projects/nope/files/a.txt").status_code == 401
