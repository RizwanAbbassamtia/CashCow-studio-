"""The sign-in API and what it protects: first run, signing in and out, the session cookie,
the failed sign-in limit, the middleware (401, public routes, the WebSocket), roles, the
Users and Activity endpoints, approvals stamped from the session and an audit row for every
action (docs/M5A-CONTRACT.md sections 1, 4 and 5)."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from http.cookies import Morsel, SimpleCookie
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cashcow_studio.app import create_app
from cashcow_studio.auth import audit as audit_log
from cashcow_studio.auth import machine_name
from cashcow_studio.auth.limits import LoginLimiter
from cashcow_studio.auth.users import UserStore
from cashcow_studio.config import load_settings
from cashcow_studio.doctor import check_team_roster
from cashcow_studio.models.channel import Channel
from cashcow_studio.models.project import ProjectCreate, ProjectSource, StageName
from cashcow_studio.pipeline.engine import PipelineEngine
from cashcow_studio.pipeline.stages.base import StageContext, StageResult
from cashcow_studio.storage.channel_store import ChannelStore
from conftest import (
    TEST_ADMIN,
    AppEnv,
    channel_payload,
    create_user,
    sign_in,
    sign_in_as,
    sign_out,
)

SIGN_IN = {"detail": "Please sign in."}
CONTRACT_USER_FIELDS = {"username", "display_name", "role", "machine", "must_change_password"}
PROJECT_BODY: dict[str, Any] = {
    "channel_slug": "kind-ledger",
    "format": "long",
    "source": {"kind": "own_topic", "topic_text": "Strangers who paid it forward"},
    "stage_mode_overrides": {"title": "review", "script": "review"},
}


def session_cookie(response: httpx.Response) -> Morsel[str]:
    jar: SimpleCookie = SimpleCookie()
    jar.load(response.headers["set-cookie"])
    return jar["ccs_session"]


def actions(app_env: AppEnv, **filters: Any) -> list[str]:
    """Audit actions, oldest first."""
    rows = audit_log.query(app_env.app_data_dir, limit=None, **filters)
    return [row.action for row in reversed(rows)]


# Projects with stand-in stages ---------------------------------------------------------------


class CaptureStage:
    """A stage that notes who it ran for and leaves a review payload."""

    def __init__(self, name: str) -> None:
        self.name = StageName(name)
        self.users: list[str] = []

    async def run(self, ctx: StageContext) -> StageResult:
        self.users.append(ctx.user)
        out = ctx.stage_dir(self.name) / f"{self.name.value}.json"
        out.write_text("{}", encoding="utf-8")
        return StageResult(
            outputs=[out],
            summary=f"{self.name.value} done",
            needs_review_payload={"stage": self.name.value},
        )


@pytest.fixture
def studio(app_env: AppEnv, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    for name in ("LLM", "RESEARCH", "IMAGE", "VOICE"):
        monkeypatch.setenv(f"CCS_{name}_PROVIDER", "mock")
    with TestClient(create_app()) as client:
        engine = client.app.state.engine
        engine.stages.clear()
        for name in ("research", "title", "script"):
            engine.register(CaptureStage(name))
        assert client.post("/api/channels", json=channel_payload("Kind Ledger")).status_code == 201
        yield client


def wait_for(
    client: TestClient, project_id: str, stage: str, status: str, timeout: float = 10.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/projects/{project_id}").json()
        if body["stages"][stage]["status"] == status:
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"{stage} is {body['stages'][stage]['status']}, not {status}")
        time.sleep(0.02)


# First run, signing in and out ---------------------------------------------------------------


@pytest.mark.signed_out
def test_a_fresh_install_offers_the_first_admin_account_once(
    client: TestClient, app_env: AppEnv
) -> None:
    status = client.get("/api/auth/status").json()
    assert status["needs_first_run"] is True and status["signed_in"] is False
    assert status["user"] is None and status["roster_source"] == "none"
    assert status["machine"] == machine_name()
    assert status["min_password_length"] == 10 and "10 characters" in status["password_rule"]
    assert client.get("/api/auth/me").status_code == 401
    no_accounts = client.post("/api/auth/login", json={"username": "imran", "password": "x" * 12})
    assert no_accounts.status_code == 409

    short = client.post(
        "/api/auth/first-run",
        json={"username": "imran", "display_name": "Imran", "password": "short"},
    )
    assert short.status_code == 422 and "10 characters" in short.json()["detail"]
    bad_name = client.post(
        "/api/auth/first-run",
        json={"username": "no spaces", "display_name": "Imran", "password": "long-enough-1"},
    )
    assert bad_name.status_code == 422

    created = client.post(
        "/api/auth/first-run",
        json={"username": "Imran", "display_name": "Imran", "password": TEST_ADMIN["password"]},
    )
    assert created.status_code == 200, created.text
    user = created.json()
    assert CONTRACT_USER_FIELDS <= set(user) and "hash" not in user and "salt" not in user
    assert (user["username"], user["role"], user["must_change_password"]) == (
        "imran", "admin", False
    )
    cookie = session_cookie(created)
    assert cookie.value and cookie["httponly"] is True and cookie["samesite"] == "strict"
    assert cookie["path"] == "/api" and not cookie["secure"] and not cookie["max-age"]
    assert client.get("/api/auth/me").json()["username"] == "imran"

    again = client.post(
        "/api/auth/first-run",
        json={"username": "mallory", "display_name": "M", "password": "another-password"},
    )
    assert again.status_code == 409
    status = client.get("/api/auth/status").json()
    assert status["needs_first_run"] is False and status["signed_in"] is True
    assert status["user"]["username"] == "imran" and status["roster_source"] == "shared"
    assert TEST_ADMIN["password"] not in app_env.account_file("imran").read_text(encoding="utf-8")
    assert actions(app_env) == ["user.create", "login"]


@pytest.mark.signed_out
def test_a_new_laptop_joins_the_team_folder_instead_of_making_an_admin(
    client: TestClient, app_env: AppEnv, tmp_path: Path
) -> None:
    team_folder = tmp_path / "team-drive"
    team_folder.mkdir()
    team = UserStore(team_folder, tmp_path / "team-laptop")
    team.create_first_admin("imran", "Imran", "the-team-admin-pass")
    _sara, sara_password = team.add("sara", "Sara", "editor", must_change_password=False)
    status = client.get("/api/auth/status").json()
    assert status["needs_first_run"] is True

    def choose(folder: object) -> httpx.Response:
        return client.post("/api/auth/first-run/shared-folder", json={"shared_dir": folder})

    assert choose("relative/folder").status_code == 422
    missing = choose(str(tmp_path / "not-there"))
    assert missing.status_code == 422 and "not found" in missing.json()["detail"]
    syncing = tmp_path / "syncing"
    (syncing / "team" / "logins").mkdir(parents=True)
    (syncing / "team" / "logins" / "laptop-a.json").write_text("{}", encoding="utf-8")
    waiting = choose(str(syncing))
    assert waiting.status_code == 422 and "Google Drive" in waiting.json()["detail"]
    assert client.get("/api/settings").status_code == 401  # still nobody signed in

    joined = choose(str(team_folder))
    assert joined.status_code == 200, joined.text
    body = joined.json()
    assert body["needs_first_run"] is False and body["roster_source"] == "shared"
    saved = json.loads(app_env.settings_file.read_text(encoding="utf-8"))
    assert saved["shared_dir"] == str(team_folder)
    assert sign_in(client, "sara", sara_password).status_code == 200
    assert not (app_env.shared_dir / "team").exists()  # no stray local admin
    rows = audit_log.query(app_env.app_data_dir, action="settings.update")
    assert rows[0].detail["before_first_sign_in"] is True and rows[0].user == ""
    # Once the team's accounts are in use, the folder is changed in Settings only.
    sign_out(client)
    again = choose(str(tmp_path))
    assert again.status_code == 409


@pytest.mark.signed_out
def test_the_first_admin_of_a_folder_with_channels_needs_a_confirmation(
    client: TestClient, app_env: AppEnv
) -> None:
    channel = app_env.channels_dir / "kind-ledger"
    channel.mkdir(parents=True)
    (channel / "channel.json").write_text("{}", encoding="utf-8")
    status = client.get("/api/auth/status").json()
    assert status["needs_first_run"] is True and "Google Drive" in status["first_run_warning"]
    body = {"username": "imran", "display_name": "Imran", "password": TEST_ADMIN["password"]}
    asked = client.post("/api/auth/first-run", json=body)
    assert asked.status_code == 409 and asked.json()["detail"] == status["first_run_warning"]
    created = client.post("/api/auth/first-run", json=body | {"confirm_team_folder": True})
    assert created.status_code == 200, created.text
    assert client.get("/api/auth/status").json()["first_run_warning"] is None


@pytest.mark.signed_out
def test_every_api_route_but_the_sign_in_ones_needs_a_session(client: TestClient) -> None:
    for path in (
        "/api",
        "/api/channels",
        "/api/channels/kind-ledger",
        "/api/projects",
        "/api/settings",
        "/api/doctor",
        "/api/jobs/abc",
        "/api/auth/me",
        "/api/users",
        "/api/audit",
        "/api/library/frameworks",
        "/api/production-log",
        "/api/nothing-here",
    ):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.json() == SIGN_IN
    for method, path in (
        ("POST", "/api/channels"),
        ("PUT", "/api/settings"),
        ("POST", "/api/projects"),
        ("DELETE", "/api/channels/kind-ledger"),
        ("POST", "/api/auth/logout"),
        ("POST", "/api/auth/change-password"),
        ("GET", "/api/auth/login"),
        ("POST", "/api/auth/status"),
        ("POST", "/api/system/info"),
    ):
        response = client.request(method, path, json={})
        assert response.status_code == 401, (method, path)
    # The routes the sign-in page needs, the API docs and the page itself are public.
    assert client.get("/api/auth/status").status_code == 200
    info = client.get("/api/system/info").json()
    assert info["auth_required"] is True and info["machine"] == machine_name()
    assert client.get("/api/openapi.json").status_code == 200
    assert client.get("/api/docs").status_code == 200
    assert client.get("/").status_code == 200
    # A made-up cookie is no session.
    client.cookies.set("ccs_session", "made-up.value", path="/api")
    assert client.get("/api/channels").status_code == 401


@pytest.mark.signed_out
def test_the_event_stream_refuses_a_handshake_without_a_session(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/api/ws"):
            pytest.fail("The handshake was accepted without a session.")
    assert refused.value.code == 4401
    created = client.post(
        "/api/auth/first-run",
        json={"username": "imran", "display_name": "Imran", "password": TEST_ADMIN["password"]},
    )
    assert created.status_code == 200
    with client.websocket_connect("/api/ws") as ws:
        ws.send_text("ping")
    # A page on another site still gets the origin guard's answer first.
    with pytest.raises(WebSocketDisconnect) as foreign:
        with client.websocket_connect("/api/ws", headers={"Origin": "https://evil.example"}):
            pytest.fail("A foreign page was let in.")
    assert foreign.value.code == 1008


def test_an_unknown_name_and_a_wrong_password_get_the_same_answer(
    anon_client: TestClient,
) -> None:
    answers = [
        sign_in(anon_client, "imran", "wrong-password-1"),
        sign_in(anon_client, "nobody.here", "wrong-password-1"),
        sign_in(anon_client, "", ""),
    ]
    for response in answers:
        assert response.status_code == 401
        assert response.json() == {"detail": "Wrong username or password."}
        assert "set-cookie" not in response.headers
    assert anon_client.get("/api/auth/me").status_code == 401


def test_turned_off_and_laptop_locked_accounts(
    client: TestClient, app_env: AppEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    password = create_user(client, "maria", "editor", display_name="Maria")
    assert client.put("/api/users/maria", json={"disabled": True}).status_code == 200
    off = sign_in(client, "maria", password)
    assert off.status_code == 423 and "turned off" in off.json()["detail"]

    locked_to = {"disabled": False, "machine": "LAPTOP-ELSE"}
    assert client.put("/api/users/maria", json=locked_to).status_code == 200
    elsewhere = sign_in(client, "maria", password)
    assert elsewhere.status_code == 403 and "LAPTOP-ELSE" in elsewhere.json()["detail"]

    monkeypatch.setenv("COMPUTERNAME", "laptop-else")  # computer names ignore case
    assert sign_in(client, "maria", password).status_code == 200
    assert client.get("/api/auth/me").json()["machine"] == "LAPTOP-ELSE"
    # The same cookie on another laptop is no session.
    monkeypatch.setenv("COMPUTERNAME", "LAPTOP-HOME")
    assert client.get("/api/auth/me").status_code == 401
    monkeypatch.setenv("COMPUTERNAME", "LAPTOP-ELSE")
    assert client.get("/api/auth/me").status_code == 200
    # Turning the account off (on any laptop) ends its session at once.
    UserStore(app_env.shared_dir, app_env.app_data_dir).set_disabled("maria", True)
    assert client.get("/api/auth/me").status_code == 401


def test_five_wrong_passwords_hold_the_account_back(anon_client: TestClient) -> None:
    clock = [5000.0]
    anon_client.app.state.ccs_login_limiter = LoginLimiter(clock=lambda: clock[0])
    for attempt in range(5):
        assert sign_in(anon_client, "IMRAN", f"wrong-password-{attempt}").status_code == 401
    held = sign_in(anon_client, "imran", TEST_ADMIN["password"])
    assert held.status_code == 429
    assert "Too many wrong passwords" in held.json()["detail"]
    assert 1 <= int(held.headers["retry-after"]) <= 600
    assert "set-cookie" not in held.headers
    assert sign_in(anon_client, "someone.else", "wrong-password-x").status_code == 401
    clock[0] += 601
    assert sign_in(anon_client, "imran", TEST_ADMIN["password"]).status_code == 200


def test_keep_me_signed_in_and_remember_my_login_details(
    anon_client: TestClient, app_env: AppEnv
) -> None:
    kept = sign_in(anon_client, "imran", TEST_ADMIN["password"], remember=True,
                   remember_username=True)
    assert kept.status_code == 200
    assert session_cookie(kept)["max-age"] == str(30 * 24 * 3600)
    status = anon_client.get("/api/auth/status").json()
    assert status["signed_in"] is True and status["saved_username"] == "imran"
    assert TEST_ADMIN["password"] not in app_env.saved_login_file.read_text(encoding="utf-8")

    sign_out(anon_client)
    status = anon_client.get("/api/auth/status").json()
    assert status["signed_in"] is False and status["saved_username"] == "imran"

    plain = sign_in(anon_client, "imran", TEST_ADMIN["password"])
    assert plain.status_code == 200 and not session_cookie(plain)["max-age"]
    assert anon_client.get("/api/auth/status").json()["saved_username"] is None
    assert not app_env.saved_login_file.exists()


def test_signing_out_ends_the_session(client: TestClient, app_env: AppEnv) -> None:
    response = client.post("/api/auth/logout")
    assert response.status_code == 204
    cleared = session_cookie(response)
    assert cleared.value == "" and cleared["max-age"] == "0" and cleared["path"] == "/api"
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/logout").status_code == 401
    assert actions(app_env, action="logout") == ["logout"]


def test_people_change_their_own_password(client: TestClient, app_env: AppEnv) -> None:
    first = create_user(client, "maria", "editor", display_name="Maria")
    assert sign_in(client, "maria", first).status_code == 200
    me = client.get("/api/auth/me").json()
    assert CONTRACT_USER_FIELDS <= set(me) and me["must_change_password"] is True

    def change(current: str, new: str) -> httpx.Response:
        return client.post(
            "/api/auth/change-password", json={"current_password": current, "new_password": new}
        )

    wrong = change("not-the-password", "brand-new-pass")
    assert wrong.status_code == 400 and "current password" in wrong.json()["detail"]
    short = change(first, "short")
    assert short.status_code == 422 and "10 characters" in short.json()["detail"]
    assert change(first, first).status_code == 422
    assert change(first, "brand-new-pass").status_code == 204
    assert client.get("/api/auth/me").json()["must_change_password"] is False
    sign_out(client)
    assert sign_in(client, "maria", first).status_code == 401
    assert sign_in(client, "maria", "brand-new-pass").status_code == 200

    rows = audit_log.query(app_env.app_data_dir, action="user.update", user="maria")
    assert rows[0].detail == {"username": "maria", "changed": ["password"], "by_self": True}
    dumped = json.dumps([r.model_dump(mode="json") for r in audit_log.query(app_env.app_data_dir)])
    assert first not in dumped and "brand-new-pass" not in dumped


# Accounts and roles --------------------------------------------------------------------------


def test_admins_manage_the_team_accounts(client: TestClient, app_env: AppEnv) -> None:
    listed = client.get("/api/users").json()
    assert [user["username"] for user in listed] == ["imran"]
    assert {"disabled", "machine", "last_login_at", "created_at"} <= set(listed[0])
    assert listed[0]["last_login_at"] is not None

    created = client.post(
        "/api/users",
        json={"username": "Maria", "display_name": "Maria K", "role": "viewer",
              "machine": " LAPTOP-7Q2M "},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == {"user", "password"} and len(body["password"]) == 14
    new_user = body["user"]
    assert (new_user["username"], new_user["role"], new_user["machine"]) == (
        "maria", "viewer", "LAPTOP-7Q2M"
    )
    assert new_user["must_change_password"] is True and new_user["created_by"] == "imran"
    assert client.post("/api/users", json={"username": "maria"}).status_code == 409
    assert client.post("/api/users", json={"username": "no spaces"}).status_code == 422
    assert client.post("/api/users", json={"username": "valid", "role": "owner"}).status_code == 422

    changed = client.put(
        "/api/users/maria", json={"role": "editor", "display_name": "Maria Khan", "machine": ""}
    )
    assert changed.status_code == 200
    assert (changed.json()["role"], changed.json()["display_name"], changed.json()["machine"]) == (
        "editor", "Maria Khan", None
    )
    assert client.put("/api/users/maria", json={}).json()["role"] == "editor"
    assert client.put("/api/users/ghost", json={"role": "viewer"}).status_code == 404

    reset = client.post("/api/users/maria/reset-password")
    assert reset.status_code == 200 and set(reset.json()) == {"password"}
    assert reset.json()["password"] != body["password"]
    assert client.post("/api/users/ghost/reset-password").status_code == 404

    demote = client.put("/api/users/imran", json={"role": "editor"})
    assert demote.status_code == 409 and "last admin" in demote.json()["detail"]
    assert client.put("/api/users/imran", json={"disabled": True}).status_code == 409
    assert client.put("/api/users/maria", json={"disabled": True}).status_code == 200

    everything = json.dumps(client.get("/api/users").json())
    assert '"hash"' not in everything and '"salt"' not in everything
    assert actions(app_env) == [
        "user.create", "login", "user.create", "user.update", "user.reset", "user.disable"
    ]


def test_editors_cannot_manage_channels_settings_or_accounts(
    client: TestClient, app_env: AppEnv
) -> None:
    created = client.post("/api/channels", json=channel_payload("Kind Ledger"))
    assert created.status_code == 201
    editor = sign_in_as(client, "editor")
    assert editor["role"] == "editor"
    for path in ("/api/channels", "/api/channels/kind-ledger", "/api/settings", "/api/doctor"):
        assert client.get(path).status_code == 200, path
    for method, path, kwargs in (
        ("post", "/api/channels", {"json": channel_payload("Other Channel")}),
        ("put", "/api/channels/kind-ledger", {"json": created.json()}),
        ("delete", "/api/channels/kind-ledger", {}),
        (
            "post",
            "/api/channels/kind-ledger/frameworks/upload",
            {"files": {"file": ("t.txt", b"x", "text/plain")}, "data": {"type": "other"}},
        ),
        ("put", "/api/settings", {"json": {"keys": {"FAL_KEY": "fal-12345678"}}}),
        ("get", "/api/users", {}),
        ("post", "/api/users", {"json": {"username": "sneaky", "role": "admin"}}),
        ("put", "/api/users/imran", {"json": {"role": "viewer"}}),
        ("post", "/api/users/imran/reset-password", {}),
    ):
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 403, (method, path, response.text)
        assert "Only an admin" in response.json()["detail"]
    assert not app_env.env_file.exists()


def test_viewers_can_look_but_not_change_anything(
    studio: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cashcow_studio.api import projects as projects_api

    monkeypatch.setattr(projects_api, "open_folder", lambda folder: None)
    project_id = studio.post("/api/projects", json=PROJECT_BODY).json()["id"]
    wait_for(studio, project_id, "title", "awaiting_review")
    assert sign_in_as(studio, "viewer")["role"] == "viewer"
    for path in ("/api/projects", f"/api/projects/{project_id}", "/api/channels", "/api/audit"):
        assert studio.get(path).status_code == 200, path
    stage = f"/api/projects/{project_id}/stage/title"
    for method, path, kwargs in (
        ("post", "/api/projects", {"json": PROJECT_BODY}),
        ("post", f"{stage}/approve", {"json": {}}),
        ("post", f"{stage}/redo", {"json": {"notes": "again"}}),
        ("post", f"{stage}/skip", {"json": {}}),
        ("put", f"{stage}/mode", {"json": {"mode": "auto"}}),
        ("post", f"/api/projects/{project_id}/run", {}),
        ("delete", f"/api/projects/{project_id}", {}),
        ("post", "/api/channels/kind-ledger/research/scan", {"json": {}}),
        (
            "post",
            f"/api/projects/{project_id}/upload/voice",
            {"files": {"file": ("v.wav", b"RIFF", "audio/wav")}},
        ),
        ("put", "/api/settings", {"json": {}}),
        ("post", "/api/users", {"json": {"username": "sneaky"}}),
    ):
        response = getattr(studio, method)(path, **kwargs)
        assert response.status_code == 403, (method, path, response.text)
        assert "not change anything" in response.json()["detail"]
    assert studio.post(f"/api/projects/{project_id}/open-folder").status_code == 200
    wrong = studio.post(
        "/api/auth/change-password",
        json={"current_password": "not-the-password", "new_password": "something-new-1"},
    )
    assert wrong.status_code == 400
    assert studio.post("/api/auth/logout").status_code == 204


def test_approvals_are_stamped_with_the_signed_in_person(
    studio: TestClient, app_env: AppEnv
) -> None:
    sign_in_as(studio, "editor")
    created = studio.post("/api/projects", json=PROJECT_BODY)
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    wait_for(studio, project_id, "title", "awaiting_review")

    approved = studio.post(
        f"/api/projects/{project_id}/stage/title/approve",
        json={"by": "Mallory", "notes": "Good one"},
    )
    assert approved.status_code == 200, approved.text
    title = approved.json()["stages"]["title"]
    assert title["approved_by"] == "Test Editor"
    assert "Approved by Test Editor." in " ".join(title["history"])
    assert "Mallory" not in approved.text

    wait_for(studio, project_id, "script", "awaiting_review")
    redo = studio.post(
        f"/api/projects/{project_id}/stage/script/redo", json={"by": "Mallory", "notes": "Shorter"}
    )
    assert redo.status_code == 200
    assert "Redo requested by Test Editor: Shorter" in " ".join(
        redo.json()["stages"]["script"]["history"]
    )
    wait_for(studio, project_id, "script", "awaiting_review")
    skipped = studio.post(f"/api/projects/{project_id}/stage/script/skip", json={"notes": "Ok"})
    assert skipped.status_code == 200
    assert "Skipped by Test Editor: Ok" in " ".join(skipped.json()["stages"]["script"]["history"])
    mode = studio.put(
        f"/api/projects/{project_id}/stage/storyboard/mode", json={"mode": "manual"}
    )
    assert mode.status_code == 200
    assert studio.delete(f"/api/projects/{project_id}").status_code == 204

    stages = studio.app.state.engine.stages
    assert stages[StageName.title].users == ["test-editor"]
    assert stages[StageName.script].users == ["test-editor", "test-editor"]
    rows = list(reversed(audit_log.query(app_env.app_data_dir, project_id=project_id)))
    assert [row.action for row in rows] == [
        "project.create", "stage.approve", "stage.redo", "stage.skip", "stage.mode",
        "project.archive",
    ]
    for row in rows:
        assert (row.user, row.machine, row.channel_slug) == (
            "test-editor", machine_name(), "kind-ledger"
        )
    assert rows[0].detail == {"format": "long", "source": "own_topic"}
    assert rows[1].detail == {"stage": "title", "notes": "Good one"}
    assert rows[4].detail == {"stage": "storyboard", "mode": "manual"}
    assert rows[5].detail["archived_to"]


def test_the_activity_log_shows_admins_everything_and_others_their_own(
    client: TestClient,
) -> None:
    password = create_user(client, "maria", "editor", display_name="Maria")
    rows = client.get("/api/audit").json()
    assert set(rows[0]) == {
        "id", "at", "user", "machine", "action", "project_id", "channel_slug", "detail"
    }
    assert rows[0]["action"] == "user.create" and rows[0]["detail"]["username"] == "maria"
    assert {row["action"] for row in rows} == {"user.create", "login"}
    assert all(row["machine"] == machine_name() for row in rows)
    logins = client.get("/api/audit", params={"action": "login"}).json()
    assert [row["action"] for row in logins] == ["login"]
    assert len(client.get("/api/audit", params={"limit": 1}).json()) == 1
    assert client.get("/api/audit", params={"limit": 0}).status_code == 422
    assert client.get("/api/audit", params={"user": "nobody"}).json() == []

    assert sign_in(client, "maria", password).status_code == 200
    mine = client.get("/api/audit", params={"user": "imran"}).json()
    assert mine and {row["user"] for row in mine} == {"maria"}


def test_settings_and_key_changes_are_logged_without_the_values(
    client: TestClient, app_env: AppEnv
) -> None:
    secret = "fal-secret-value-12345678"
    keys = client.put("/api/settings", json={"keys": {"FAL_KEY": secret, "GEMINI_API_KEY": None}})
    assert keys.status_code == 200
    render = client.put("/api/settings", json={"render": {"default_presets": ["720p"]}})
    assert render.status_code == 200
    assert client.put("/api/settings", json={"keys": {"bad name": "x"}}).status_code == 422

    key_rows = audit_log.query(app_env.app_data_dir, action="keys.update")
    assert len(key_rows) == 1 and key_rows[0].user == "imran"
    assert key_rows[0].detail == {"set": ["FAL_KEY"], "cleared": ["GEMINI_API_KEY"]}
    settings_rows = audit_log.query(app_env.app_data_dir, action="settings.update")
    assert [row.detail for row in settings_rows] == [{"sections": ["render"]}]
    everything = audit_log.query(app_env.app_data_dir, limit=None)
    assert secret not in json.dumps([row.model_dump(mode="json") for row in everything])


def test_channel_changes_are_logged(client: TestClient, app_env: AppEnv) -> None:
    created = client.post("/api/channels", json=channel_payload("Kind Ledger")).json()
    assert client.put("/api/channels/kind-ledger", json=created).status_code == 200
    upload = client.post(
        "/api/channels/kind-ledger/frameworks/upload",
        files={"file": ("title.txt", b"Write short titles", "text/plain")},
        data={"type": "title"},
    )
    assert upload.status_code == 200
    assert client.delete("/api/channels/kind-ledger").status_code == 204
    rows = [
        row for row in reversed(audit_log.query(app_env.app_data_dir, limit=None))
        if row.action.startswith("channel.")
    ]
    assert [row.action for row in rows] == [
        "channel.create", "channel.update", "channel.update", "channel.archive"
    ]
    assert all(row.channel_slug == "kind-ledger" and row.user == "imran" for row in rows)
    assert rows[0].detail == {"name": "Kind Ledger"}
    assert rows[2].detail["framework_uploaded"].endswith("title.txt")
    assert rows[2].detail["type"] == "title"
    assert rows[3].detail["archived_to"]


# The roster in the shared folder ---------------------------------------------------------------


def test_choosing_another_shared_folder_keeps_the_team_signed_in(
    client: TestClient, app_env: AppEnv, tmp_path: Path
) -> None:
    new_drive = tmp_path / "drive" / "CashCow"
    moved = client.put("/api/settings", json={"shared_dir": str(new_drive)})
    assert moved.status_code == 200, moved.text
    assert (new_drive / "team" / "users" / "imran.json").is_file()
    assert client.get("/api/auth/me").json()["username"] == "imran"
    assert client.get("/api/auth/status").json()["roster_source"] == "shared"

    # A folder that already holds a team's accounts: asked first, then those apply from the
    # next request on.
    other_team = tmp_path / "other-team"
    other_team.mkdir()
    UserStore(other_team, app_env.app_data_dir).create_first_admin(
        "boss", "Boss", "boss-password-1"
    )
    asked = client.put("/api/settings", json={"shared_dir": str(other_team)})
    assert asked.status_code == 409 and "signed out" in asked.json()["detail"]
    assert client.get("/api/settings").json()["shared_dir"] == str(new_drive)
    confirmed = client.put(
        "/api/settings", json={"shared_dir": str(other_team), "confirm_team_folder": True}
    )
    assert confirmed.status_code == 200, confirmed.text
    assert client.get("/api/auth/me").status_code == 401
    assert sign_in(client, "boss", "boss-password-1").status_code == 200

    # A folder whose account list cannot be read is refused and nothing changes.
    broken = tmp_path / "broken"
    (broken / "team" / "users").mkdir(parents=True)
    (broken / "team" / "users" / "someone.json").write_text("{", encoding="utf-8")
    refused = client.put("/api/settings", json={"shared_dir": str(broken)})
    assert refused.status_code == 422 and "cannot be read" in refused.json()["detail"]
    assert client.get("/api/settings").json()["shared_dir"] == str(other_team)

    # So is a team folder whose accounts Google Drive has not delivered yet.
    syncing = tmp_path / "syncing"
    (syncing / "team" / "logins").mkdir(parents=True)
    (syncing / "team" / "logins" / "laptop-a.json").write_text("{}", encoding="utf-8")
    waiting = client.put(
        "/api/settings", json={"shared_dir": str(syncing), "confirm_team_folder": True}
    )
    assert waiting.status_code == 422 and "Google Drive" in waiting.json()["detail"]
    assert not (syncing / "team" / "users").exists()


def test_a_folder_with_channels_but_no_accounts_is_joined_only_when_confirmed(
    client: TestClient, tmp_path: Path
) -> None:
    team_folder = tmp_path / "team-drive"
    (team_folder / "channels" / "kind-ledger").mkdir(parents=True)
    (team_folder / "channels" / "kind-ledger" / "channel.json").write_text("{}", encoding="utf-8")
    asked = client.put("/api/settings", json={"shared_dir": str(team_folder)})
    assert asked.status_code == 409 and "Google Drive" in asked.json()["detail"]
    assert not (team_folder / "team").exists()
    joined = client.put(
        "/api/settings", json={"shared_dir": str(team_folder), "confirm_team_folder": True}
    )
    assert joined.status_code == 200, joined.text
    assert (team_folder / "team" / "users" / "imran.json").is_file()
    assert client.get("/api/auth/me").json()["username"] == "imran"


def test_a_namesake_in_another_folder_does_not_inherit_the_session(
    client: TestClient, app_env: AppEnv, tmp_path: Path
) -> None:
    # Someone made a local "imran" on this laptop; the team's folder has the real imran.
    team_folder = tmp_path / "team-drive"
    team_folder.mkdir()
    team = UserStore(team_folder, tmp_path / "team-laptop")
    team.create_first_admin("imran", "Imran (team)", "the-real-admin-password")
    team.add("maria", "Maria", "editor")
    asked = client.put("/api/settings", json={"shared_dir": str(team_folder)})
    assert asked.status_code == 409 and "your account (imran) is not in it" in asked.json()[
        "detail"
    ]
    switched = client.put(
        "/api/settings", json={"shared_dir": str(team_folder), "confirm_team_folder": True}
    )
    assert switched.status_code == 200
    # The cookie of the local imran is no session for the team's imran.
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/users").status_code == 401
    assert sign_in(client, "imran", TEST_ADMIN["password"]).status_code == 401
    real = sign_in(client, "imran", "the-real-admin-password")
    assert real.status_code == 200 and real.json()["display_name"] == "Imran (team)"


def test_signing_out_ends_the_session_on_the_server(client: TestClient) -> None:
    kept = sign_in(client, "imran", TEST_ADMIN["password"], remember=True)
    assert kept.status_code == 200
    copied = session_cookie(kept).value
    assert client.post("/api/auth/logout").status_code == 204
    client.cookies.set("ccs_session", copied, path="/api")  # someone kept a copy
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/users", json={"username": "ghost", "role": "admin"}).status_code == 401
    assert UserStore(*_folders(client)).get("ghost") is None


def _folders(client: TestClient) -> tuple[Path, Path]:
    settings = client.app.state.settings
    return settings.resolved_shared_dir, settings.app_data_dir


def test_a_password_change_ends_every_other_session(client: TestClient) -> None:
    first = create_user(client, "maria", "editor", display_name="Maria")
    elsewhere = sign_in(client, "maria", first, remember=True)
    assert elsewhere.status_code == 200
    other_browser = session_cookie(elsewhere).value
    here = sign_in(client, "maria", first)
    assert here.status_code == 200
    this_browser = session_cookie(here).value

    changed = client.post(
        "/api/auth/change-password",
        json={"current_password": first, "new_password": "maria-new-password"},
    )
    assert changed.status_code == 204
    fresh = session_cookie(changed)
    assert fresh.value and fresh.value not in (this_browser, other_browser)
    assert not fresh["max-age"]  # this browser keeps its kind of session
    assert client.get("/api/auth/me").json()["must_change_password"] is False
    for old in (other_browser, this_browser):
        client.cookies.set("ccs_session", old, path="/api")
        assert client.get("/api/auth/me").status_code == 401


def test_an_admin_reset_ends_every_session_of_that_person(app_env: AppEnv) -> None:
    with TestClient(create_app()) as admin, TestClient(create_app()) as other:
        password = create_user(admin, "maria", "editor", display_name="Maria")
        assert sign_in(other, "maria", password, remember=True).status_code == 200
        assert other.get("/api/auth/me").status_code == 200
        assert admin.post("/api/users/maria/reset-password").status_code == 200
        assert other.get("/api/auth/me").status_code == 401
        # An admin's own password is changed with Change password, never reset here.
        self_reset = admin.post("/api/users/imran/reset-password")
        assert self_reset.status_code == 409 and "Change password" in self_reset.json()["detail"]
        assert admin.get("/api/auth/me").status_code == 200


def test_the_last_admin_who_can_sign_in_here_cannot_be_locked_away(
    client: TestClient, app_env: AppEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPUTERNAME", "LAPTOP-HERE")
    assert sign_in(client, "imran", TEST_ADMIN["password"]).status_code == 200
    own = client.put("/api/users/imran", json={"machine": "LAPTOP-TYPO"})
    assert own.status_code == 409 and "your own account" in own.json()["detail"]
    assert client.get("/api/auth/me").status_code == 200
    assert client.put("/api/users/imran", json={"machine": "laptop-here"}).status_code == 200
    assert client.put("/api/users/imran", json={"machine": None}).status_code == 200

    create_user(client, "boss2", "admin", display_name="Boss Two", machine="SOME-OTHER-PC")
    demote = client.put("/api/users/imran", json={"role": "editor"})
    assert demote.status_code == 409 and "last admin" in demote.json()["detail"]
    assert client.get("/api/auth/me").json()["role"] == "admin"
    assert client.put("/api/users/boss2", json={"machine": None}).status_code == 200
    assert client.put("/api/users/imran", json={"role": "editor"}).status_code == 200


def test_accounts_this_version_cannot_read_never_reopen_the_first_run(
    client: TestClient, app_env: AppEnv
) -> None:
    saved_copy = app_env.users_cache_file.read_text(encoding="utf-8")
    data = json.loads(app_env.account_file("imran").read_text(encoding="utf-8"))
    data["role"] = "owner"  # written by a newer version
    app_env.account_file("imran").write_text(json.dumps(data), encoding="utf-8")
    client.cookies.clear()
    status = client.get("/api/auth/status").json()
    assert status["needs_first_run"] is False
    assert "newer version" in status["roster_notice"]
    real = client.post(
        "/api/auth/login", json={"username": "imran", "password": TEST_ADMIN["password"]}
    )
    assert real.status_code == 503 and "Update CashCow Studio" in real.json()["detail"]
    stranger = client.post(
        "/api/auth/first-run",
        json={"username": "mallory", "display_name": "M", "password": "mallory-password"},
    )
    assert stranger.status_code == 503 and "newer version" in stranger.json()["detail"]
    assert sorted(p.name for p in app_env.users_dir.iterdir()) == ["imran.json"]
    assert app_env.users_cache_file.read_text(encoding="utf-8") == saved_copy
    assert check_team_roster(client.app.state.settings).status == "fail"


def test_a_damaged_roster_falls_back_to_this_laptops_copy(
    client: TestClient, app_env: AppEnv
) -> None:
    app_env.account_file("imran").write_text("{ damaged", encoding="utf-8")
    status = client.get("/api/auth/status").json()
    assert status["roster_source"] == "cache" and status["roster_notice"]
    assert status["needs_first_run"] is False
    assert client.get("/api/auth/me").status_code == 200
    added = client.post("/api/users", json={"username": "maria"})
    assert added.status_code == 503 and "cannot be changed" in added.json()["detail"]
    sign_out(client)
    assert sign_in(client, "imran", TEST_ADMIN["password"]).status_code == 200
    assert app_env.account_file("imran").read_text(encoding="utf-8") == "{ damaged"
    assert check_team_roster(client.app.state.settings).status == "warn"


def test_the_doctor_reports_the_team_accounts(
    app_env: AppEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPUTERNAME", "LAPTOP-HERE")
    settings = load_settings()
    settings.ensure_dirs()
    fresh = check_team_roster(settings)
    assert fresh.status == "warn" and "first admin" in fresh.fix_hint
    store = UserStore(settings.resolved_shared_dir, settings.app_data_dir)
    store.create_first_admin("imran", "Imran", TEST_ADMIN["password"])
    ready = check_team_roster(settings)
    assert ready.status == "ok" and "1 account(s), 1 active admin(s)" in ready.detail

    # Every admin locked to one laptop: nobody could manage the accounts if it were lost.
    store.set_machine("imran", "LAPTOP-HERE")
    locked = check_team_roster(settings)
    assert locked.status == "warn" and "locked to one laptop" in locked.detail
    assert "ccs users unlock" in locked.fix_hint
    store.set_machine("imran", None)

    # No admin turned on: the hint names the command that turns one back on.
    store.add("maria", "Maria", "admin")
    store.set_disabled("imran", True)
    data = json.loads(app_env.account_file("maria").read_text(encoding="utf-8"))
    data["disabled"] = True  # (the app never allows this; a hand-edited file might)
    app_env.account_file("maria").write_text(json.dumps(data), encoding="utf-8")
    nobody = check_team_roster(settings)
    assert nobody.status == "warn" and "ccs users enable" in nobody.fix_hint
    store.update("imran", disabled=False, fields={"disabled"})

    app_env.account_file("imran").write_text("{", encoding="utf-8")
    app_env.account_file("maria").write_text("{", encoding="utf-8")
    assert check_team_roster(settings).status == "warn"  # this laptop's copy
    app_env.users_cache_file.unlink()
    lost = check_team_roster(settings)
    assert lost.status == "fail" and lost.fix_hint


# Who acted, inside the engine ------------------------------------------------------------------


async def _settle(engine: PipelineEngine, project_id: str, timeout: float = 90.0) -> None:
    task = engine.tasks.get(project_id)
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), timeout)
    await asyncio.sleep(0)


def test_the_engine_hands_the_acting_person_to_the_stages(app_env: AppEnv) -> None:
    settings = load_settings()
    settings.ensure_dirs()
    channel = ChannelStore(settings.resolved_shared_dir).create(
        Channel.model_validate(channel_payload("Kind Ledger") | {"slug": "kind-ledger"})
    )
    engine = PipelineEngine(settings)
    capture = {name: CaptureStage(name) for name in ("title", "script")}
    for stage in capture.values():
        engine.register(stage)

    async def scenario() -> None:
        body = ProjectCreate(
            channel_slug="kind-ledger",
            format="long",
            source=ProjectSource(kind="own_topic", topic_text="Kind strangers"),
            stage_mode_overrides={StageName.title: "review", StageName.script: "review"},
        )
        project = engine.create_project(body, channel)
        await engine.run(project.id, user="maria")
        await _settle(engine, project.id)
        await engine.approve(project.id, StageName.title, "John", user="john")
        await _settle(engine, project.id)
        await engine.redo(project.id, StageName.script, "Maria K", notes="again")
        await _settle(engine, project.id)
        await engine.shutdown()

    asyncio.run(scenario())
    assert capture["title"].users == ["maria"]
    assert capture["script"].users == ["john", "Maria K"]  # without a username: the name given


def test_export_done_names_the_person_who_approved_the_export(
    app_env: AppEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cashcow_studio.export import thumbnail as thumbs
    from test_export_stage import create_project as new_export_project
    from test_export_stage import make_engine

    monkeypatch.setattr(thumbs, "SUBJECT_SIZE", "1K")
    engine, channel = make_engine(app_env)

    async def scenario() -> str:
        project = new_export_project(engine, channel, export="review")
        await engine.run(project.id, user="maria")
        await _settle(engine, project.id)
        assert engine.get(project.id).stages[StageName.export].status == "awaiting_review"
        assert audit_log.query(app_env.app_data_dir, action="export.done") == []
        await engine.approve(project.id, StageName.export, "Maria", user="maria")
        await _settle(engine, project.id)
        await engine.shutdown()
        return project.id

    project_id = asyncio.run(scenario())
    rows = audit_log.query(app_env.app_data_dir, action="export.done")
    assert len(rows) == 1
    row = rows[0]
    assert (row.user, row.machine, row.project_id, row.channel_slug) == (
        "maria", machine_name(), project_id, "kind-ledger"
    )
    detail = row.detail
    folder = Path(detail["export_folder"])
    videos = sorted(folder.glob("*.mp4"))
    assert detail["presets"] == ["720p"] and len(videos) == 1
    assert detail["size_bytes"] == videos[0].stat().st_size
    assert detail["duration_s"] == 96.0
    assert (detail["format"], detail["language"]) == ("long", "English")
    assert detail["title"] and detail["files"] >= 3
