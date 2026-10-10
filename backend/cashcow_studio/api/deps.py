"""Request-scoped helpers shared by the routers.

Signing in (docs/M5A-CONTRACT.md section 1): ``CurrentUser`` is the person the auth
middleware signed in for this request (401 without one); ``require_role("admin")`` /
``require_role("editor")`` build a dependency that also checks the role (403), with the
shortcuts ``AdminUser`` and ``EditorUser``. ``audit(...)`` writes one row of the activity log
for the signed-in person on this laptop.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request

from ..auth import audit as audit_log
from ..auth import machine_name
from ..auth.middleware import SIGN_IN_MESSAGE, SessionUser, current_user, user_store_for
from ..auth.users import Role, UserStore
from ..config import Settings
from ..storage.channel_store import ChannelStore
from ..storage.settings_store import SettingsStore

ROLE_MESSAGES: dict[str, str] = {
    "admin": "Only an admin can do this. Ask an admin on your team.",
    "editor": "Your account can look at everything but not change anything. Ask an admin if "
    "you need to make changes.",
    "viewer": SIGN_IN_MESSAGE,
}


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_settings_store(request: Request) -> SettingsStore:
    return SettingsStore(get_settings(request).app_data_dir)


def get_channel_store(request: Request) -> ChannelStore:
    # Built per request so a changed shared folder takes effect immediately.
    return ChannelStore(get_settings(request).resolved_shared_dir)


def get_current_user(request: Request) -> SessionUser:
    user = current_user(request.scope)
    if user is None:
        raise HTTPException(status_code=401, detail=SIGN_IN_MESSAGE)
    return user


def require_role(role: Role) -> Callable[..., SessionUser]:
    """A dependency that lets ``role`` and higher roles through (admin > editor > viewer)."""

    def dependency(user: Annotated[SessionUser, Depends(get_current_user)]) -> SessionUser:
        if not user.at_least(role):
            raise HTTPException(status_code=403, detail=ROLE_MESSAGES.get(role, SIGN_IN_MESSAGE))
        return user

    dependency.__name__ = f"require_{role}"
    return dependency


def get_user_store(request: Request) -> UserStore:
    """The team roster of the shared folder in use (one store per folder, kept on the app)."""
    return user_store_for(request.app)


def audit(
    request: Request,
    user: SessionUser | str,
    action: str,
    *,
    project_id: str | None = None,
    channel_slug: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """One row of the activity log for ``user`` on this laptop. Never raises."""
    username = user.username if isinstance(user, SessionUser) else str(user or "")
    audit_log.record(
        get_settings(request).app_data_dir,
        action,
        username,
        machine=machine_name(),
        project_id=project_id,
        channel_slug=channel_slug,
        detail=detail,
    )


SettingsDep = Annotated[Settings, Depends(get_settings)]
SettingsStoreDep = Annotated[SettingsStore, Depends(get_settings_store)]
ChannelStoreDep = Annotated[ChannelStore, Depends(get_channel_store)]
CurrentUser = Annotated[SessionUser, Depends(get_current_user)]
AdminUser = Annotated[SessionUser, Depends(require_role("admin"))]
EditorUser = Annotated[SessionUser, Depends(require_role("editor"))]
UserStoreDep = Annotated[UserStore, Depends(get_user_store)]
