"""Sign-in sessions: the signed ``ccs_session`` cookie and "remember my login details".

The cookie holds ``{user, machine, issued_at, expires_at, remember, sid, account}`` as
base64url JSON and an HMAC-SHA256 signature made with a per-laptop secret,
``<app_data_dir>/session.secret`` (32 random bytes, readable by this Windows account only).

A cookie is only good while the server agrees with it:

* ``sid`` is a random id noted in this laptop's table ``auth_sessions`` (``db.sqlite``) at
  sign-in. Signing out deletes it, so a copied cookie value is worthless after Sign out;
  changing or resetting a password, or turning the account off, deletes every id of that
  account on this laptop.
* ``account`` is a fingerprint of the account (:func:`account_key`: the username, when it was
  made and the salt of its current password, keyed with this laptop's secret). A changed or
  reset password has a new salt, so every earlier session of the account ends on every
  laptop; an account of the same name in another shared folder never matches.
* The roster is checked on every request too, so turning an account off, locking it to
  another laptop or changing its role takes effect at once. Deleting ``session.secret`` signs
  everyone out of this laptop.

* ``HttpOnly`` (the page's scripts cannot read it), ``SameSite=Strict`` (another site cannot
  send it), not ``Secure`` because the server only listens on 127.0.0.1 over plain HTTP.
* 12 hours, or 30 days with "Keep me signed in on this laptop" (then the cookie also survives
  closing the app; without it the cookie ends with the app window).
* "Remember my login details" keeps only the username, in ``<app_data_dir>/saved-login.json``;
  a password is never written anywhere.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from starlette.responses import Response

from ..storage import db
from ..storage.settings_store import atomic_write_text
from .users import USERNAME_RE, User

log = logging.getLogger(__name__)

SESSION_COOKIE = "ccs_session"
COOKIE_PATH = "/api"
"""Only the API needs the cookie (the WebSocket is ``/api/ws``); the page itself is public."""
SESSION_SECONDS = 12 * 3600
REMEMBER_SECONDS = 30 * 24 * 3600
CLOCK_SKEW_SECONDS = 300
SECRET_FILE = "session.secret"
SECRET_BYTES = 32
SAVED_LOGIN_FILE = "saved-login.json"
MAX_TOKEN_LENGTH = 4096
SESSION_ID_BYTES = 16
_SIGNING_PREFIX = b"ccs_session.v2."
_ACCOUNT_PREFIX = b"ccs_account.v1."
_TOKEN_PART_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

_SECRET_LOCK = threading.Lock()
_SECRET_CACHE: dict[str, tuple[tuple[int, int], bytes]] = {}


@dataclass(frozen=True)
class Session:
    """What the cookie says: who signed in, on which laptop, until when, the session's id on
    this laptop and the fingerprint of the account it was made for."""

    user: str
    machine: str
    issued_at: int
    expires_at: int
    remember: bool
    sid: str
    account: str

    @property
    def max_age(self) -> int | None:
        """Cookie lifetime: persistent for "keep me signed in", else ends with the window."""
        return max(0, self.expires_at - self.issued_at) if self.remember else None


# The per-laptop secret ----------------------------------------------------------------------


def secret_path(app_data_dir: Path | str) -> Path:
    return Path(app_data_dir) / SECRET_FILE


def load_secret(app_data_dir: Path | str, *, create: bool) -> bytes | None:
    """The signing secret; made on first use when ``create`` (a sign-in), else ``None``.

    A damaged secret (wrong length) is replaced on the next sign-in, which ends every session
    on this laptop: the safe outcome.
    """
    path = secret_path(app_data_dir)
    key = str(path)
    with _SECRET_LOCK:
        signature = _file_signature(path)
        cached = _SECRET_CACHE.get(key)
        if signature is not None and cached is not None and cached[0] == signature:
            return cached[1]
        if signature is not None:
            try:
                data = path.read_bytes()
            except OSError:
                data = b""
            if len(data) == SECRET_BYTES:
                _SECRET_CACHE[key] = (signature, data)
                return data
        _SECRET_CACHE.pop(key, None)
        if not create:
            return None
        _write_secret(path, secrets.token_bytes(SECRET_BYTES))
        signature = _file_signature(path)
        data = path.read_bytes()
        if signature is not None and len(data) == SECRET_BYTES:
            _SECRET_CACHE[key] = (signature, data)
        return data


def _file_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _write_secret(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".tmp-{path.name}-{os.getpid()}-{secrets.token_hex(4)}")
    try:
        with open(temp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        restrict_to_owner(temp)  # before the file gets its real name
        os.replace(temp, path)
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass
        raise


def restrict_to_owner(path: Path | str) -> bool:
    """Let only this Windows account (no other user, not even an administrator group) open
    ``path``; ``chmod 600`` elsewhere. Best effort: returns False when it could not be done."""
    if sys.platform != "win32":
        try:
            os.chmod(path, 0o600)
        except OSError:
            return False
        return True
    try:
        return _windows_owner_only(str(path))
    except Exception:  # noqa: BLE001 - the file still sits in the user's own app folder
        log.debug("Could not narrow the permissions of %s", path, exc_info=True)
        return False


def _windows_owner_only(path: str) -> bool:
    """A protected DACL with one entry: full control for the current user's SID."""
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.ULONG),
    ]
    convert.restype = wintypes.BOOL
    advapi32.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    advapi32.SetFileSecurityW.restype = wintypes.BOOL

    token_query = 0x0008
    token_user = 1
    dacl_information = 0x00000004
    protected_dacl = 0x80000000
    sddl_revision_1 = 1

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), token_query, ctypes.byref(token)
    ):
        return False
    try:
        needed = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, token_user, None, 0, ctypes.byref(needed))
        if not needed.value:
            return False
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            token, token_user, buffer, needed, ctypes.byref(needed)
        ):
            return False
        sid_pointer = ctypes.c_void_p.from_buffer(buffer).value  # TOKEN_USER.User.Sid
        text = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(text)):
            return False
        try:
            sid = text.value
        finally:
            kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        kernel32.CloseHandle(token)
    if not sid:
        return False
    descriptor = ctypes.c_void_p()
    if not convert(f"D:P(A;;FA;;;{sid})", sddl_revision_1, ctypes.byref(descriptor), None):
        return False
    try:
        return bool(
            advapi32.SetFileSecurityW(path, dacl_information | protected_dacl, descriptor)
        )
    finally:
        kernel32.LocalFree(descriptor)


# Tokens -------------------------------------------------------------------------------------


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signature(secret: bytes, body: str) -> str:
    return _b64encode(hmac.new(secret, _SIGNING_PREFIX + body.encode("ascii"), hashlib.sha256)
                      .digest())


def account_key(secret: bytes, account: User) -> str:
    """The fingerprint of an account and its current password, keyed with this laptop's
    secret: the username, when the account was made and the salt of its password. A new
    password (changed or reset) has a new salt, so the fingerprint changes with it."""
    created = account.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    material = json.dumps(
        [account.username, int(created.timestamp()), account.salt], separators=(",", ":")
    )
    digest = hmac.new(secret, _ACCOUNT_PREFIX + material.encode("utf-8"), hashlib.sha256)
    return _b64encode(digest.digest()[:18])


def session_matches(app_data_dir: Path | str, session: Session, account: User) -> bool:
    """True when the session was made for this very account and password: not for a
    namesake in another shared folder, not before the password was changed or reset."""
    secret = load_secret(app_data_dir, create=False)
    if secret is None or session.user != account.username:
        return False
    expected = account_key(secret, account).encode("ascii")
    return hmac.compare_digest(expected, session.account.encode("ascii", "replace"))


def issue_session(
    app_data_dir: Path | str,
    account: User,
    machine: str,
    *,
    remember: bool,
    now: float | None = None,
) -> tuple[str, Session]:
    """A signed cookie value and the session it carries, for ``account`` on ``machine``.

    The server accepts it only once it is noted with :func:`register_session`."""
    secret = load_secret(app_data_dir, create=True)
    if secret is None:  # pragma: no cover - load_secret(create=True) always returns bytes
        raise OSError("The sign-in secret could not be created.")
    issued = int(now if now is not None else time.time())
    lifetime = REMEMBER_SECONDS if remember else SESSION_SECONDS
    session = Session(
        user=account.username,
        machine=machine,
        issued_at=issued,
        expires_at=issued + lifetime,
        remember=bool(remember),
        sid=_b64encode(secrets.token_bytes(SESSION_ID_BYTES)),
        account=account_key(secret, account),
    )
    payload = json.dumps(asdict(session), separators=(",", ":"), sort_keys=True)
    body = _b64encode(payload.encode("utf-8"))
    return f"{body}.{_signature(secret, body)}", session


def read_session(
    app_data_dir: Path | str, token: str | None, *, now: float | None = None
) -> Session | None:
    """The session in a cookie value, or ``None`` when it is missing, forged, damaged,
    expired, or was signed on another laptop (another secret).

    This checks the value alone; :func:`session_is_live` and :func:`session_matches` tell
    whether the server still accepts it."""
    if not token or not isinstance(token, str) or len(token) > MAX_TOKEN_LENGTH:
        return None
    body, dot, signature = token.partition(".")
    if not dot or not body or not signature or "." in signature:
        return None
    secret = load_secret(app_data_dir, create=False)
    if secret is None:
        return None
    try:
        expected = _signature(secret, body)
    except UnicodeEncodeError:
        return None
    if not hmac.compare_digest(expected.encode("ascii"), signature.encode("ascii", "replace")):
        return None
    try:
        data: Any = json.loads(_b64decode(body).decode("utf-8"))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    session = _session_from(data)
    if session is None:
        return None
    moment = int(now if now is not None else time.time())
    lifetime = REMEMBER_SECONDS if session.remember else SESSION_SECONDS
    if session.expires_at <= moment:
        return None
    if session.issued_at > moment + CLOCK_SKEW_SECONDS:
        return None
    if session.expires_at - session.issued_at > lifetime:
        return None
    return session


def _session_from(data: Any) -> Session | None:
    if not isinstance(data, dict):
        return None
    user, machine = data.get("user"), data.get("machine")
    issued, expires, remember = data.get("issued_at"), data.get("expires_at"), data.get("remember")
    sid, account = data.get("sid"), data.get("account")
    if not isinstance(user, str) or not USERNAME_RE.match(user):
        return None
    if not isinstance(machine, str) or not machine.strip():
        return None
    if type(issued) is not int or type(expires) is not int or not isinstance(remember, bool):
        return None
    if not isinstance(sid, str) or not _TOKEN_PART_RE.match(sid):
        return None
    if not isinstance(account, str) or not _TOKEN_PART_RE.match(account):
        return None
    return Session(user=user, machine=machine, issued_at=issued, expires_at=expires,
                   remember=remember, sid=sid, account=account)


# The sessions of this laptop (table ``auth_sessions`` in db.sqlite) ------------------------

CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS auth_sessions (
    "id" TEXT PRIMARY KEY,
    "user" TEXT NOT NULL,
    "issued_at" INTEGER NOT NULL,
    "expires_at" INTEGER NOT NULL
)
"""
CREATE_SESSIONS_INDEX = 'CREATE INDEX IF NOT EXISTS auth_sessions_user ON auth_sessions ("user")'

_SESSIONS_READY: set[str] = set()
_SESSIONS_READY_LOCK = threading.Lock()


def _ensure_sessions_table(app_data_dir: Path | str) -> None:
    key = str(Path(app_data_dir))
    with _SESSIONS_READY_LOCK:
        if key in _SESSIONS_READY and db.db_path(Path(app_data_dir)).is_file():
            return
    with db.connect(Path(app_data_dir)) as conn:
        conn.execute(CREATE_SESSIONS)
        conn.execute(CREATE_SESSIONS_INDEX)
    with _SESSIONS_READY_LOCK:
        _SESSIONS_READY.add(key)


def register_session(
    app_data_dir: Path | str, session: Session, *, now: float | None = None
) -> None:
    """Note a new session on this laptop (and forget the ones that have expired).

    Raises ``sqlite3.Error`` or ``OSError`` when the table cannot be written: a session that
    is not noted is never accepted, so the sign-in has to fail instead."""
    moment = int(now if now is not None else time.time())
    _ensure_sessions_table(app_data_dir)
    with db.connect(Path(app_data_dir)) as conn:
        conn.execute('DELETE FROM auth_sessions WHERE "expires_at" <= ?', (moment,))
        conn.execute(
            'INSERT OR REPLACE INTO auth_sessions ("id", "user", "issued_at", "expires_at") '
            "VALUES (?, ?, ?, ?)",
            (session.sid, session.user, session.issued_at, session.expires_at),
        )


def session_is_live(
    app_data_dir: Path | str, session: Session, *, now: float | None = None
) -> bool:
    """True while the session is noted on this laptop, for that account, and not expired.
    A table that cannot be read means "no": the person signs in again."""
    moment = int(now if now is not None else time.time())
    try:
        _ensure_sessions_table(app_data_dir)
        with db.connect(Path(app_data_dir)) as conn:
            row = conn.execute(
                'SELECT "user", "expires_at" FROM auth_sessions WHERE "id" = ?', (session.sid,)
            ).fetchone()
    except (sqlite3.Error, OSError) as exc:
        log.warning("The sessions of this laptop could not be checked: %s", exc)
        return False
    return row is not None and row["user"] == session.user and int(row["expires_at"]) > moment


def end_session(app_data_dir: Path | str, sid: str) -> None:
    """Sign one session out on this laptop (best effort)."""
    try:
        _ensure_sessions_table(app_data_dir)
        with db.connect(Path(app_data_dir)) as conn:
            conn.execute('DELETE FROM auth_sessions WHERE "id" = ?', (sid,))
    except (sqlite3.Error, OSError) as exc:
        log.warning("A session could not be ended on this laptop: %s", exc)


def end_sessions_of(app_data_dir: Path | str, username: str) -> int:
    """Sign every session of ``username`` out on this laptop; returns how many there were."""
    try:
        _ensure_sessions_table(app_data_dir)
        with db.connect(Path(app_data_dir)) as conn:
            cursor = conn.execute('DELETE FROM auth_sessions WHERE "user" = ?', (username,))
            return int(cursor.rowcount or 0)
    except (sqlite3.Error, OSError) as exc:
        log.warning("The sessions of an account could not be ended on this laptop: %s", exc)
        return 0


# The cookie ---------------------------------------------------------------------------------


def set_session_cookie(response: Response, token: str, session: Session) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=session.max_age,
        path=COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=False,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path=COOKIE_PATH, httponly=True, samesite="strict", secure=False
    )


# "Remember my login details" (the username only) ------------------------------------------


def saved_login_path(app_data_dir: Path | str) -> Path:
    return Path(app_data_dir) / SAVED_LOGIN_FILE


def read_saved_username(app_data_dir: Path | str) -> str | None:
    try:
        data = json.loads(saved_login_path(app_data_dir).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    name = data.get("username") if isinstance(data, dict) else None
    if not isinstance(name, str):
        return None
    name = name.strip().lower()
    return name if USERNAME_RE.match(name) else None


def save_username(app_data_dir: Path | str, username: str) -> None:
    """Keep the username for the next sign-in on this laptop (never the password)."""
    payload = {"username": username, "saved_at": datetime.now(UTC).isoformat(timespec="seconds")}
    try:
        atomic_write_text(
            saved_login_path(app_data_dir), json.dumps(payload, indent=2) + "\n", private=True
        )
    except OSError as exc:
        log.warning("The login details could not be remembered on this laptop: %s", exc)


def forget_saved_login(app_data_dir: Path | str) -> None:
    try:
        saved_login_path(app_data_dir).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("The remembered login details could not be removed: %s", exc)
