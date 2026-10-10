"""``/api/auth``: the first admin, signing in and out, who is signed in, changing a password.

See docs/M5A-CONTRACT.md section 1. ``status``, ``first-run``, ``first-run/shared-folder`` and
``login`` are public (the sign-in page needs them); the rest need a session. A wrong password
and an unknown username get the same answer, five wrong passwords for one account hold it back
for ten minutes on this laptop, and nothing here ever returns or logs a password or a hash.

Sessions end on the server, not only in the browser: Sign out removes the session from this
laptop's list, and a changed password ends every other session of the account (the browser
that changed it gets a fresh one).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..auth import machine_name, same_machine
from ..auth.limits import wait_message
from ..auth.middleware import current_user, login_limiter_for, user_store_for
from ..auth.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, PASSWORD_RULE
from ..auth.sessions import (
    Session,
    clear_session_cookie,
    end_session,
    end_sessions_of,
    forget_saved_login,
    issue_session,
    read_saved_username,
    register_session,
    save_username,
    set_session_cookie,
)
from ..auth.users import (
    FirstRunDone,
    InvalidUserData,
    NeedsConfirmation,
    PublicUser,
    Roster,
    RosterReadOnly,
    RosterSource,
    User,
    UserNotFound,
    UserStore,
    UserStoreError,
    WrongPassword,
    same_path,
)
from ..config import Settings
from ..storage import settings_store
from ..storage.settings_store import SettingsStore
from .deps import CurrentUser, SettingsDep, UserStoreDep, audit
from .settings import folder_value

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["auth"])

WRONG_CREDENTIALS = "Wrong username or password."
DISABLED_MESSAGE = "This account has been turned off. Ask an admin to turn it back on."
LOCKED_MESSAGE = (
    "This account can only be used on the laptop {machine}. Ask an admin if you need it on "
    "this laptop ({here})."
)
NO_ACCOUNTS_MESSAGE = "No account exists yet. Create the first admin account first."
SESSION_NOT_SAVED = (
    "Signing in could not be finished: this laptop could not save the sign-in in its own "
    "data folder. Close CashCow Studio, open it again and try once more."
)
FOLDER_ALREADY_SET = (
    "This laptop already uses a shared folder with the team's accounts. Sign in; an admin can "
    "change the folder in Settings > Folders."
)


class AuthStatus(BaseModel):
    """What the sign-in page needs before anyone is signed in."""

    needs_first_run: bool
    signed_in: bool
    user: PublicUser | None = None
    machine: str
    """This laptop's Windows computer name."""
    roster_source: RosterSource
    roster_notice: str | None = None
    """Plain-English notice when the team's account list cannot be used as it is (read from
    this laptop's copy, still downloading, saved by a newer version, partly unreadable)."""
    first_run_warning: str | None = None
    """When the first admin needs a confirmation: the shared folder already holds channels."""
    saved_username: str | None = None
    """The username kept by "Remember my login details" on this laptop (never a password)."""
    password_rule: str = PASSWORD_RULE
    min_password_length: int = MIN_PASSWORD_LENGTH


class FirstRunRequest(BaseModel):
    username: str = Field(max_length=256)
    display_name: str = Field(default="", max_length=256)
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)
    confirm_team_folder: bool = False
    """The person read ``first_run_warning`` and goes ahead anyway."""


class TeamFolderRequest(BaseModel):
    shared_dir: str = Field(max_length=1024)
    """The team's shared Google Drive folder, as a full path."""


class LoginRequest(BaseModel):
    username: str = Field(default="", max_length=256)
    password: str = Field(default="", max_length=MAX_PASSWORD_LENGTH)
    remember: bool = False
    """Keep me signed in on this laptop (30 days instead of 12 hours)."""
    remember_username: bool = False
    """Remember my login details: fill in the username next time."""


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(max_length=MAX_PASSWORD_LENGTH)


def _store_error(exc: Exception) -> HTTPException:
    if isinstance(exc, InvalidUserData):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, FirstRunDone | NeedsConfirmation):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, UserNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, RosterReadOnly):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, UserStoreError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(
        status_code=500,
        detail=f"The team's account list could not be saved: {exc}. If the shared folder is "
        "syncing, wait a moment and try again.",
    )


def _start_session(
    response: Response, settings: Settings, user: User, *, remember: bool
) -> Session:
    """Sign ``user`` in on this laptop: note the session, then hand the browser its cookie."""
    try:
        token, session = issue_session(
            settings.app_data_dir, user, machine_name(), remember=remember
        )
        register_session(settings.app_data_dir, session)
    except (sqlite3.Error, OSError) as exc:
        log.warning("A sign-in could not be saved on this laptop: %s", exc)
        raise HTTPException(status_code=500, detail=SESSION_NOT_SAVED) from exc
    set_session_cookie(response, token, session)
    return session


def _fresh(store: UserStore, user: User) -> PublicUser:
    return PublicUser.of(store.get(user.username) or user)


def _status(request: Request, settings: Settings, roster: Roster) -> AuthStatus:
    user = current_user(request.scope)
    saved = read_saved_username(settings.app_data_dir)
    if saved is not None and roster.source != "none" and saved not in roster.users:
        saved = None  # the account is gone
    return AuthStatus(
        needs_first_run=roster.needs_first_run,
        signed_in=user is not None,
        user=user.public if user is not None else None,
        machine=machine_name(),
        roster_source=roster.source,
        roster_notice=roster.notice,
        first_run_warning=roster.first_run_warning,
        saved_username=saved,
    )


@router.get("/auth/status", response_model=AuthStatus)
def auth_status(request: Request, settings: SettingsDep, store: UserStoreDep) -> AuthStatus:
    return _status(request, settings, store.roster())


@router.post("/auth/first-run", response_model=PublicUser)
def first_run(
    body: FirstRunRequest,
    request: Request,
    response: Response,
    settings: SettingsDep,
    store: UserStoreDep,
) -> PublicUser:
    """Create the first admin account (only while no account exists) and sign it in."""
    try:
        user = store.create_first_admin(
            body.username, body.display_name, body.password, confirmed=body.confirm_team_folder
        )
    except (UserStoreError, OSError) as exc:
        raise _store_error(exc) from exc
    audit(
        request,
        user.username,
        "user.create",
        detail={"username": user.username, "role": user.role, "first_run": True},
    )
    _start_session(response, settings, user, remember=False)
    store.record_login(user.username)
    audit(request, user.username, "login", detail={"remember": False, "first_run": True})
    return _fresh(store, user)


@router.post("/auth/first-run/shared-folder", response_model=AuthStatus)
def choose_team_folder(
    body: TeamFolderRequest, request: Request, settings: SettingsDep, store: UserStoreDep
) -> AuthStatus:
    """Before the first account exists on this laptop: use the team's shared folder, so the
    team's accounts (and channels) apply here instead of a separate first admin.

    Only while this laptop sees no account at all. The folder must exist; it is used when it
    holds the team's accounts, or no account at all (a new team folder: the first admin is
    then made there). A folder whose accounts cannot be read, or that holds team files but no
    account yet (Google Drive still downloading), is refused and nothing changes.
    """
    if not store.roster().needs_first_run:
        raise HTTPException(status_code=409, detail=FOLDER_ALREADY_SET)
    value = folder_value("shared_dir", body.shared_dir)
    if value is None:
        raise HTTPException(status_code=422, detail="Type the full path of the team's folder.")
    folder = Path(value)
    if not folder.is_dir():
        raise HTTPException(
            status_code=422,
            detail=f"The folder {folder} was not found on this laptop. Check the path, or wait "
            "until Google Drive shows the folder, then try again.",
        )
    if not same_path(folder, settings.resolved_shared_dir):
        theirs = UserStore(folder, settings.app_data_dir).refresh()
        if theirs.state not in ("ok", "missing") or theirs.source == "cache":
            raise HTTPException(
                status_code=422,
                detail=(theirs.notice or "The team's account list in that folder cannot be read.")
                + " The folder was not changed.",
            )
        with settings_store.LOCK:
            settings.shared_dir = folder
            settings.ensure_dirs()
            try:
                SettingsStore(settings.app_data_dir).write_paths(settings.paths_as_dict())
            except OSError as exc:
                raise HTTPException(
                    status_code=500, detail=f"The settings file could not be written: {exc}"
                ) from exc
        audit(
            request,
            "",
            "settings.update",
            detail={"folders": {"shared_dir": str(folder)}, "before_first_sign_in": True},
        )
    return _status(request, settings, user_store_for(request.app).roster())


@router.post("/auth/login", response_model=PublicUser)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    settings: SettingsDep,
    store: UserStoreDep,
) -> PublicUser:
    limiter = login_limiter_for(request.app)
    wait = limiter.retry_after(body.username)
    if wait:
        raise HTTPException(
            status_code=429, detail=wait_message(wait), headers={"Retry-After": str(wait)}
        )
    roster = store.roster()
    if not roster.users:
        if roster.needs_first_run:
            raise HTTPException(status_code=409, detail=NO_ACCOUNTS_MESSAGE)
        raise HTTPException(
            status_code=503,
            detail=roster.notice or "The team's account list cannot be read right now.",
        )
    user = store.verify(body.username, body.password)
    if user is None:
        limiter.failed(body.username)
        # Never the typed name: people sometimes type their password into the name field.
        log.info("A sign-in on this laptop failed (wrong username or password)")
        raise HTTPException(status_code=401, detail=WRONG_CREDENTIALS)
    if user.disabled:
        raise HTTPException(status_code=423, detail=DISABLED_MESSAGE)
    here = machine_name()
    if user.machine and not same_machine(user.machine, here):
        raise HTTPException(
            status_code=403, detail=LOCKED_MESSAGE.format(machine=user.machine, here=here)
        )
    limiter.succeeded(body.username)
    _start_session(response, settings, user, remember=body.remember)
    if body.remember_username:
        save_username(settings.app_data_dir, user.username)
    else:
        forget_saved_login(settings.app_data_dir)
    store.record_login(user.username)
    audit(request, user.username, "login", detail={"remember": body.remember})
    return _fresh(store, user)


@router.post("/auth/logout", status_code=204, response_class=Response)
def logout(request: Request, user: CurrentUser, settings: SettingsDep) -> Response:
    """End this session on this laptop (a copy of the cookie stops working too). "Remember my
    login details" stays as it was."""
    end_session(settings.app_data_dir, user.session.sid)
    response = Response(status_code=204)
    clear_session_cookie(response)
    audit(request, user, "logout")
    return response


@router.get("/auth/me", response_model=PublicUser)
def me(user: CurrentUser) -> PublicUser:
    return user.public


@router.post("/auth/change-password", status_code=204, response_class=Response)
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    user: CurrentUser,
    settings: SettingsDep,
    store: UserStoreDep,
) -> Response:
    """Change the signed-in person's password. Every other session of the account ends (on
    every laptop); this browser carries on with a fresh session."""
    limiter = login_limiter_for(request.app)
    wait = limiter.retry_after(user.username)
    if wait:
        raise HTTPException(
            status_code=429, detail=wait_message(wait), headers={"Retry-After": str(wait)}
        )
    try:
        changed = store.change_password(user.username, body.current_password, body.new_password)
    except WrongPassword as exc:
        limiter.failed(user.username)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (UserStoreError, OSError) as exc:
        raise _store_error(exc) from exc
    limiter.succeeded(user.username)
    end_sessions_of(settings.app_data_dir, user.username)
    response = Response(status_code=204)
    _start_session(response, settings, changed, remember=user.session.remember)
    audit(
        request,
        user,
        "user.update",
        detail={"username": user.username, "changed": ["password"], "by_self": True},
    )
    return response
