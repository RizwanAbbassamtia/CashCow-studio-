"""``/api/users`` (admin only) and ``/api/audit``: the team's accounts and the activity log.

See docs/M5A-CONTRACT.md section 1. A new or reset password is generated here and returned
once, for the admin to hand over; the person must change it at the next sign-in, and every
session they had ends. The team always keeps an admin who can sign in on this laptop (see
``UserStore.update``). An admin cannot turn off the account they are signed in with, lock it
to another laptop, or reset its password here (Change password is for that: a reset would
sign them out before they could copy the new password). Every change is written to the
activity log.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..auth import machine_name, same_machine
from ..auth.audit import AuditRow
from ..auth.audit import query as audit_query
from ..auth.sessions import end_sessions_of
from ..auth.users import (
    InvalidUserData,
    LastAdmin,
    PublicUser,
    Role,
    RosterReadOnly,
    UserExists,
    UserNotFound,
    UserStoreError,
    clean_machine,
)
from .deps import AdminUser, CurrentUser, SettingsDep, UserStoreDep, audit

router = APIRouter(prefix="/api", tags=["users"])

UPDATABLE = ("display_name", "role", "machine", "disabled")
SELF_RESET_MESSAGE = (
    "To change your own password, use Change password in the menu under your name (top "
    "right). Reset password is for other people's accounts."
)
SELF_LOCK_MESSAGE = (
    "You cannot lock your own account to another laptop ({machine}): you would be signed out "
    "here at once. Lock it to this laptop ({here}), or ask another admin."
)


class NewUserRequest(BaseModel):
    username: str = Field(max_length=256)
    display_name: str = Field(default="", max_length=256)
    role: Role = "editor"
    machine: str | None = Field(default=None, max_length=256)
    """Lock the account to one laptop (its Windows computer name); empty for any laptop."""


class CreatedUser(BaseModel):
    user: PublicUser
    password: str
    """Shown once; it is not stored anywhere in a readable form."""


class UserChangeRequest(BaseModel):
    """Only the fields sent are changed. ``machine: null`` (or "") unlocks the account."""

    display_name: str | None = Field(default=None, max_length=256)
    role: Role | None = None
    machine: str | None = Field(default=None, max_length=256)
    disabled: bool | None = None


class NewPassword(BaseModel):
    password: str
    """Shown once; the person must choose their own at the next sign-in."""


def _store_error(exc: Exception) -> HTTPException:
    if isinstance(exc, InvalidUserData):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, UserExists | LastAdmin):
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


@router.get("/users", response_model=list[PublicUser])
def list_users(_admin: AdminUser, store: UserStoreDep) -> list[PublicUser]:
    return [PublicUser.of(user) for user in store.list()]


@router.post("/users", response_model=CreatedUser, status_code=201)
def create_user(
    body: NewUserRequest, request: Request, admin: AdminUser, store: UserStoreDep
) -> CreatedUser:
    try:
        user, password = store.add(
            body.username,
            body.display_name,
            body.role,
            body.machine,
            created_by=admin.username,
        )
    except (UserStoreError, OSError) as exc:
        raise _store_error(exc) from exc
    audit(
        request,
        admin,
        "user.create",
        detail={"username": user.username, "role": user.role, "machine": user.machine},
    )
    return CreatedUser(user=PublicUser.of(user), password=password)


@router.put("/users/{username}", response_model=PublicUser)
def update_user(
    username: str,
    body: UserChangeRequest,
    request: Request,
    admin: AdminUser,
    settings: SettingsDep,
    store: UserStoreDep,
) -> PublicUser:
    key = username.strip().lower()
    # null means "leave as is", except for machine where it means "any laptop".
    fields = {
        name
        for name in body.model_fields_set & set(UPDATABLE)
        if name == "machine" or getattr(body, name) is not None
    }
    if body.disabled and key == admin.username:
        raise HTTPException(
            status_code=409,
            detail="You cannot turn off the account you are signed in with. Ask another admin.",
        )
    if "machine" in fields and key == admin.username:
        try:
            wanted = clean_machine(body.machine)
        except InvalidUserData as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        here = machine_name()
        if wanted and not same_machine(wanted, here):
            raise HTTPException(
                status_code=409, detail=SELF_LOCK_MESSAGE.format(machine=wanted, here=here)
            )
    if not fields:
        user = store.get(key)
        if user is None:
            raise HTTPException(status_code=404, detail="There is no such account.")
        return PublicUser.of(user)
    try:
        user, changes = store.update(
            key,
            display_name=body.display_name,
            role=body.role,
            machine=body.machine,
            disabled=body.disabled,
            fields=fields,
        )
    except (UserStoreError, OSError) as exc:
        raise _store_error(exc) from exc
    if changes:
        turned_off = changes.get("disabled", [None, None])[1] is True
        if turned_off:
            end_sessions_of(settings.app_data_dir, user.username)
        detail: dict[str, Any] = {"username": user.username, "changes": changes}
        audit(request, admin, "user.disable" if turned_off else "user.update", detail=detail)
    return PublicUser.of(user)


@router.post("/users/{username}/reset-password", response_model=NewPassword)
def reset_password(
    username: str,
    request: Request,
    admin: AdminUser,
    settings: SettingsDep,
    store: UserStoreDep,
) -> NewPassword:
    """A new one-time password for someone else; every session they had ends (new salt)."""
    if username.strip().lower() == admin.username:
        raise HTTPException(status_code=409, detail=SELF_RESET_MESSAGE)
    try:
        user, password = store.reset_password(username)
    except (UserStoreError, OSError) as exc:
        raise _store_error(exc) from exc
    end_sessions_of(settings.app_data_dir, user.username)
    audit(request, admin, "user.reset", detail={"username": user.username})
    return NewPassword(password=password)


@router.get("/audit", response_model=list[AuditRow])
def audit_rows(
    user: CurrentUser,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    user_filter: Annotated[str | None, Query(alias="user", max_length=64)] = None,
    action: Annotated[str | None, Query(max_length=64)] = None,
    project_id: Annotated[str | None, Query(max_length=128)] = None,
) -> list[AuditRow]:
    """Newest first. An admin sees everyone's rows; anyone else only their own."""
    who = (user_filter or "").strip().lower() or None
    if not user.at_least("admin"):
        who = user.username
    return audit_query(
        settings.app_data_dir,
        limit=limit,
        user=who,
        action=(action or "").strip() or None,
        project_id=(project_id or "").strip() or None,
    )
