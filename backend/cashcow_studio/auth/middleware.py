"""The sign-in check in front of the API (pure ASGI, the same style as ``origin_guard.py``).

Every ``/api/*`` request and the ``/api/ws`` WebSocket needs a valid ``ccs_session`` cookie,
except the few routes the sign-in page itself needs (:data:`PUBLIC_ROUTES`). Without one a
request gets ``401 {"detail": "Please sign in."}`` and a WebSocket handshake is closed with
code 4401 *before* it is accepted, so no event is ever sent to it. The page itself (``/`` and
the built files) stays public: the app asks ``/api/auth/me`` and shows the sign-in page.

A cookie is good when its signature checks out with this laptop's secret, it has not
expired, it was made on this laptop, its account still exists in the team roster, is not
turned off and is not locked to another laptop, it was made for that very account and
password (not a namesake in another shared folder, not before a password change or reset),
and this laptop still has it on its list of sessions (Sign out removes it). The roster is
consulted on every request, so a role change or a disabled account takes effect at once.

The signed-in person reaches the endpoints as ``request.state.ccs_user`` (see
``api/deps.py``: ``CurrentUser`` and ``require_role``). Role checks are per endpoint; on top
of that a ``viewer`` can never send a request that changes something, apart from signing
out, changing their own password and opening a project folder in Explorer.

The middleware sits inside CORS (so the dev server's preflights are still answered) and
inside the same-origin guard (so a foreign page gets the guard's 403, not a 401).
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from . import machine_name, same_machine
from .limits import LoginLimiter
from .sessions import SESSION_COOKIE, Session, read_session, session_is_live, session_matches
from .users import PublicUser, Role, UserStore, role_at_least

log = logging.getLogger(__name__)

USER_STATE_KEY = "ccs_user"
SIGN_IN_MESSAGE = "Please sign in."
VIEWER_MESSAGE = (
    "Your account can look at everything but not change anything. Ask an admin if you need "
    "to make changes."
)
WS_SIGN_IN_CODE = 4401
"""Close code for a refused WebSocket handshake (4000-4999 are for applications; 4401 mirrors
HTTP 401). A browser only sees an abnormal close (1006) for a refused handshake, so the page
asks ``/api/auth/me`` to tell "signed out" from a dropped connection."""

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/first-run"),
        ("POST", "/api/auth/first-run/shared-folder"),
        ("GET", "/api/auth/status"),
        ("GET", "/api/system/info"),
        ("GET", "/api/docs"),
        ("GET", "/api/openapi.json"),
    }
)
VIEWER_WRITES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^/api/auth/(logout|change-password)$"),
    re.compile(r"^/api/projects/[^/]+/open-folder$"),
)


@dataclass(frozen=True)
class SessionUser:
    """The signed-in person as the endpoints see them (never a hash or a salt)."""

    username: str
    display_name: str
    role: Role
    machine: str | None
    """The laptop the account is locked to, or ``None``."""
    must_change_password: bool
    session: Session
    public: PublicUser

    @property
    def name(self) -> str:
        """The name people read in the app (the display name, else the username)."""
        return self.display_name or self.username

    def at_least(self, role: str) -> bool:
        return role_at_least(self.role, role)


def is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def is_public(method: str, path: str) -> bool:
    verb = method.upper()
    return ("GET" if verb == "HEAD" else verb, path) in PUBLIC_ROUTES


def viewer_may_send(method: str, path: str) -> bool:
    return method.upper() in SAFE_METHODS or any(p.match(path) for p in VIEWER_WRITES)


def current_user(scope: Scope) -> SessionUser | None:
    """The person the middleware signed in for this request, or ``None``."""
    state = scope.get("state")
    user = state.get(USER_STATE_KEY) if isinstance(state, dict) else None
    return user if isinstance(user, SessionUser) else None


# Per-app helpers (the app keeps one store per shared folder and one limiter) --------------

_HELPERS_LOCK = threading.Lock()


def user_store_for(app: Any) -> UserStore:
    """The roster of the shared folder in use right now (a changed folder gets a new store)."""
    settings = app.state.settings
    shared_dir, app_data_dir = settings.resolved_shared_dir, settings.app_data_dir
    key = (str(shared_dir), str(app_data_dir))
    with _HELPERS_LOCK:
        stores: dict[tuple[str, str], UserStore] | None = getattr(
            app.state, "ccs_user_stores", None
        )
        if stores is None:
            stores = {}
            app.state.ccs_user_stores = stores
        store = stores.get(key)
        if store is None:
            store = stores[key] = UserStore(shared_dir, app_data_dir)
        return store


def login_limiter_for(app: Any) -> LoginLimiter:
    with _HELPERS_LOCK:
        limiter: LoginLimiter | None = getattr(app.state, "ccs_login_limiter", None)
        if limiter is None:
            limiter = LoginLimiter()
            app.state.ccs_login_limiter = limiter
        return limiter


def resolve_user(app: Any, token: str | None) -> SessionUser | None:
    """The person a cookie value stands for right now, or ``None``. Never raises."""
    settings = getattr(app.state, "settings", None)
    if settings is None or not token:
        return None
    try:
        session = read_session(settings.app_data_dir, token)
        if session is None:
            return None
        here = machine_name()
        if not same_machine(session.machine, here):
            return None
        account = user_store_for(app).get(session.user)
        if account is None or account.disabled:
            return None
        if account.machine and not same_machine(account.machine, here):
            return None
        if not session_matches(settings.app_data_dir, session, account):
            return None  # another account of that name, or the password changed since
        if not session_is_live(settings.app_data_dir, session):
            return None  # signed out (or ended by a password change) on this laptop
    except Exception:  # noqa: BLE001 - a broken check means "not signed in", never a crash
        log.exception("A sign-in cookie could not be checked")
        return None
    return SessionUser(
        username=account.username,
        display_name=account.display_name,
        role=account.role,
        machine=account.machine,
        must_change_password=account.must_change_password,
        session=session,
        public=PublicUser.of(account),
    )


def session_cookie(scope: Scope) -> str | None:
    headers = Headers(scope=scope)
    raw = "; ".join(headers.getlist("cookie"))
    if not raw:
        return None
    return cookie_parser(raw).get(SESSION_COOKIE) or None


class AuthMiddleware:
    """Pure ASGI middleware; see the module docstring."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        path = scope.get("path", "")
        if kind not in ("http", "websocket") or not is_api_path(path):
            await self.app(scope, receive, send)
            return
        application = scope.get("app")
        token = session_cookie(scope)
        user: SessionUser | None = None
        if token and application is not None:
            user = await run_in_threadpool(resolve_user, application, token)
        state = dict(scope.get("state") or {})
        state[USER_STATE_KEY] = user
        scope = {**scope, "state": state}

        if kind == "websocket":
            if user is None:
                close = WebSocketClose(code=WS_SIGN_IN_CODE, reason=SIGN_IN_MESSAGE)
                await close(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET")).upper()
        if is_public(method, path):
            await self.app(scope, receive, send)
            return
        if user is None:
            response = JSONResponse(status_code=401, content={"detail": SIGN_IN_MESSAGE})
            await response(scope, receive, send)
            return
        if user.role == "viewer" and not viewer_may_send(method, path):
            response = JSONResponse(status_code=403, content={"detail": VIEWER_MESSAGE})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
