"""Shared fixtures: every test gets a fresh app with its own temporary data folders.

The ``CCS_*`` environment variables are set before ``create_app()`` runs, which is how the
backend learns where to keep settings, keys and channels.

Signing in (docs/M5A-CONTRACT.md section 1): every API request needs a session. Unless a test
is marked ``@pytest.mark.signed_out``, each ``TestClient`` signs in as the test admin
(:data:`TEST_ADMIN`, display name "Imran") as soon as it starts: the first client of a test
creates that account through the first-run endpoint, later clients sign in with it. So the
shared ``client`` fixture, and every client a test module builds itself, is a signed-in admin.

* ``sign_in_as(client, "editor")`` / ``"viewer"`` / ``"admin"`` switch a client to that role
  (the editor and viewer accounts are made on first use); ``sign_in`` and ``sign_out`` are the
  plain steps; ``create_user`` adds an account and returns its one-time password.
* ``anon_client`` is a client without a session (the admin account exists);
  ``@pytest.mark.signed_out`` leaves every client of a test signed out with no account at all.
* Password hashing is made cheap (:data:`FAST_ITERATIONS`) unless a test is marked
  ``@pytest.mark.real_password_hashing``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from cashcow_studio.app import create_app
from cashcow_studio.auth import passwords
from cashcow_studio.auth.sessions import COOKIE_PATH, SESSION_COOKIE

# Short names keep the temp paths well under the Windows 260-character limit.
pytest_plugins: list[str] = []

TEST_ADMIN: dict[str, str] = {
    "username": "imran",
    "display_name": "Imran",
    "password": "test-admin-password-1",
}
ROLE_USERS: dict[str, tuple[str, str]] = {
    "editor": ("test-editor", "Test Editor"),
    "viewer": ("test-viewer", "Test Viewer"),
}
FAST_ITERATIONS = 1_000


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "signed_out: the TestClients of this test do not sign in by themselves"
    )
    config.addinivalue_line(
        "markers", "real_password_hashing: hash passwords with the real PBKDF2 cost"
    )


@dataclass
class AppEnv:
    app_data_dir: Path
    shared_dir: Path
    projects_dir: Path
    exports_dir: Path

    @property
    def channels_dir(self) -> Path:
        return self.shared_dir / "channels"

    @property
    def env_file(self) -> Path:
        return self.app_data_dir / ".env"

    @property
    def settings_file(self) -> Path:
        return self.app_data_dir / "settings.json"

    @property
    def users_dir(self) -> Path:
        """The team's accounts, one file each (``team/users/<username>.json``)."""
        return self.shared_dir / "team" / "users"

    def account_file(self, username: str) -> Path:
        return self.users_dir / f"{username}.json"

    @property
    def users_cache_file(self) -> Path:
        return self.app_data_dir / "users.cache.json"

    @property
    def session_secret_file(self) -> Path:
        return self.app_data_dir / "session.secret"

    @property
    def saved_login_file(self) -> Path:
        return self.app_data_dir / "saved-login.json"


@pytest.fixture
def app_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppEnv]:
    env = AppEnv(
        app_data_dir=tmp_path / "app",
        shared_dir=tmp_path / "shared",
        projects_dir=tmp_path / "projects",
        exports_dir=tmp_path / "exports",
    )
    monkeypatch.setenv("CCS_APP_DATA_DIR", str(env.app_data_dir))
    monkeypatch.setenv("CCS_SHARED_DIR", str(env.shared_dir))
    monkeypatch.setenv("CCS_PROJECTS_DIR", str(env.projects_dir))
    monkeypatch.setenv("CCS_EXPORTS_DIR", str(env.exports_dir))
    monkeypatch.delenv("CCS_PORT", raising=False)
    monkeypatch.delenv("CCS_FRONTEND_DIST", raising=False)
    # Keys written to .env are loaded into os.environ by the app; restore it afterwards.
    snapshot = os.environ.copy()
    yield env
    for name in list(os.environ):
        if name not in snapshot:
            del os.environ[name]
    os.environ.update(snapshot)


# Signing in -----------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fast_password_hashing(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("real_password_hashing") is None:
        monkeypatch.setattr(passwords, "PBKDF2_ITERATIONS", FAST_ITERATIONS)


@pytest.fixture(autouse=True)
def _signed_in_test_clients(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every TestClient of the test signs in as the test admin when it starts."""
    if request.node.get_closest_marker("signed_out") is not None:
        return
    original_enter = TestClient.__enter__

    def enter_signed_in(self: TestClient) -> TestClient:
        entered = original_enter(self)
        sign_in_test_admin(entered)
        return entered

    monkeypatch.setattr(TestClient, "__enter__", enter_signed_in)


def _is_studio_app(client: TestClient) -> bool:
    state = getattr(client.app, "state", None)
    return getattr(state, "settings", None) is not None


def _share_cookie_with_websockets(client: TestClient) -> None:
    """The test client opens every WebSocket at ``ws://testserver`` whatever its
    ``base_url``; a client on another host carries its session cookie for every host."""
    if client.base_url.host == "testserver":
        return
    token = next((c.value for c in client.cookies.jar if c.name == SESSION_COOKIE), None)
    if token:
        client.cookies.delete(SESSION_COOKIE)
        client.cookies.set(SESSION_COOKIE, token, domain="", path=COOKIE_PATH)


def sign_in(
    client: TestClient,
    username: str,
    password: str,
    *,
    remember: bool = False,
    remember_username: bool = False,
) -> httpx.Response:
    """``POST /api/auth/login``; on success the client carries the new session."""
    if client.base_url.host != "testserver":
        client.cookies.delete(SESSION_COOKIE)
    response = client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": password,
            "remember": remember,
            "remember_username": remember_username,
        },
    )
    if response.status_code == 200:
        _share_cookie_with_websockets(client)
    return response


def sign_out(client: TestClient) -> None:
    """End the client's session (``POST /api/auth/logout``) and drop its cookie."""
    client.post("/api/auth/logout")
    client.cookies.delete(SESSION_COOKIE)


def sign_in_test_admin(client: TestClient) -> None:
    """Create the test admin through first-run (fresh app) or sign in with it."""
    if not _is_studio_app(client):
        return
    status = client.get("/api/auth/status")
    if status.status_code != 200:
        return
    if status.json().get("needs_first_run"):
        body: dict[str, Any] = {
            key: TEST_ADMIN[key] for key in ("username", "display_name", "password")
        }
        # Tests that put channels in the shared folder first confirm the first-run warning.
        body["confirm_team_folder"] = True
        response = client.post("/api/auth/first-run", json=body)
        if response.status_code == 200:
            _share_cookie_with_websockets(client)
        return
    sign_in(client, TEST_ADMIN["username"], TEST_ADMIN["password"])


def create_user(
    client: TestClient,
    username: str,
    role: str = "editor",
    *,
    display_name: str = "",
    machine: str | None = None,
) -> str:
    """Add an account (the client must be signed in as an admin); returns its password."""
    response = client.post(
        "/api/users",
        json={
            "username": username,
            "display_name": display_name,
            "role": role,
            "machine": machine,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["password"]


def sign_in_as(client: TestClient, role: str) -> dict[str, Any]:
    """Switch ``client`` to an account with ``role``; returns that account (PublicUser)."""
    admin = sign_in(client, TEST_ADMIN["username"], TEST_ADMIN["password"])
    assert admin.status_code == 200, admin.text
    if role == "admin":
        return admin.json()
    username, display_name = ROLE_USERS[role]
    known = {user["username"] for user in client.get("/api/users").json()}
    if username in known:
        reset = client.post(f"/api/users/{username}/reset-password")
        assert reset.status_code == 200, reset.text
        password = reset.json()["password"]
    else:
        password = create_user(client, username, role, display_name=display_name)
    response = sign_in(client, username, password)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def client(app_env: AppEnv) -> Iterator[TestClient]:
    """A fresh app and a client signed in as the test admin."""
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def anon_client(client: TestClient) -> TestClient:
    """The same app with a client that is not signed in (the test admin exists)."""
    client.cookies.clear()
    return client


@pytest.fixture
def editor_client(client: TestClient) -> TestClient:
    sign_in_as(client, "editor")
    return client


@pytest.fixture
def viewer_client(client: TestClient) -> TestClient:
    sign_in_as(client, "viewer")
    return client


def channel_payload(name: str = "Kind Ledger", **overrides: Any) -> dict[str, Any]:
    """A valid channel body as the Channel Setup form would post it (no slug)."""
    body: dict[str, Any] = {
        "channel": {
            "name": name,
            "url": "https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx",
            "language": "English",
            "niche": "Kindness and emotional stories",
            "formats": "both",
            "brand_colors": ["#1F3864", "#ffc000"],
            "owner": "Imran",
        },
        "competitors": [
            {
                "name": "Human Ember",
                "url": "https://www.youtube.com/channel/UCFjva5hxOFoj2ViNgSuEQJg",
                "priority": 1,
                "why": "Same niche, 3x our views",
            },
            {"name": "The Gentle Hour", "url": "https://www.youtube.com/@thegentlehour"},
        ],
        "frameworks": [
            {
                "type": "title",
                "name": "Title Writing Prompt",
                "path": "channels/kind-ledger/frameworks/title.txt",
            }
        ],
        "voice": {"tool": "fish_audio", "api_key_env": "FISH_AUDIO_API_KEY"},
        "images": {"tool": "google_gemini", "api_key_env": "GEMINI_API_KEY"},
        "reviewer": "Imran",
    }
    body.update(overrides)
    return body
