"""The team roster: one file per account in ``<shared_dir>/team/users/``, copied to
``users.cache.json``.

Every laptop of the team reads the same files through the synced Google Drive folder, so an
account made on one laptop works on all of them. Sessions stay per laptop (see
:mod:`.sessions`). Inside the shared folder::

    team/users/<username>.json   one account: name, role, password hash and salt, revision
    team/logins/<laptop>.json    when people last signed in on that laptop (one writer each)
    team/users.lock              held while this laptop (or its command line) changes accounts

Why one file per account
    Google Drive copies whole files and keeps the copy it received last, and a lock file only
    works on one disk. If the whole team lived in one file, a laptop whose copy was a few
    minutes behind would write every account back the way it last saw them and silently undo
    changes made on another laptop. With one file per account a change only ever touches the
    account being changed, so a change another laptop made to any *other* account is never
    thrown away. Two admins changing the *same* account on two laptops at the same moment can
    still overwrite each other (the copy Google Drive receives last wins). Signing in never
    writes an account file: the sign-in time goes to this laptop's own file in ``team/logins``.

Reading
    The shared files are the truth. When they cannot be read (the drive is offline, the folder
    is gone, every account file is damaged), the copy this laptop saved from the same folder
    last time is used *read-only* (``source == "cache"``): people can still sign in, but
    accounts cannot change until the shared files are back.

    "Create the first admin account" is offered only when the folder holds no account at all
    and nothing else of the team either. When ``team/`` already holds files but no account
    (``state == "incomplete"``), Google Drive is most likely still downloading the accounts,
    so the app waits instead of starting a second team. When the folder already holds channels
    but no account, the first admin needs a confirmation (:attr:`Roster.first_run_warning`):
    that is how a team that used an earlier version starts, and also how a laptop looks whose
    Google Drive has not finished downloading.

    Account files this version cannot read (saved by a newer version, or damaged) are left
    exactly as they are. When no account at all can be read, the app says so (and that it may
    need an update); it never treats the team as empty.

Writing
    Every change re-reads the account files under a thread lock and the lock file, applies
    the change and writes only the accounts that changed, each in one step. Right before an
    account is written its revision is checked again; when another laptop's change to it
    arrived in between, the change is applied again to the newer account.

Nothing here logs or returns a password, hash or salt.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from ..storage.settings_store import atomic_write_text
from . import machine_name, same_machine
from .passwords import (
    PBKDF2_ITERATIONS,
    PasswordRuleError,
    burn_time,
    check_password_rule,
    generate_password,
    hash_password,
    verify_password,
)

log = logging.getLogger(__name__)

TEAM_DIR = "team"
USERS_DIR = "users"
LOGINS_DIR = "logins"
ACCOUNT_SUFFIX = ".json"
LOCK_FILE = "users.lock"
CACHE_FILE = "users.cache.json"
CHANNELS_DIR = "channels"
CHANNEL_FILE = "channel.json"
LOGINS_VERSION = 1
CACHE_VERSION = 1
MAX_MIRRORS = 5
"""users.cache.json keeps one copy per shared folder this laptop used (the newest five), so
looking at another folder (Settings > Paths) never replaces the copy of the one in use."""
IGNORED_FILES = frozenset({"desktop.ini", "thumbs.db", ".ds_store"})
"""Files Windows, Google Drive or a Mac may drop into any folder; they are not team data."""

_CACHE_LOCK = threading.Lock()
_LOGINS_LOCK = threading.Lock()

LOCK_WAIT_S = 10.0
LOCK_STALE_S = 30.0
WRITE_RETRIES = 3
CHANGE_ATTEMPTS = 3
LAST_LOGIN_EVERY = timedelta(hours=1)
"""A sign-in time is written at most once an hour per account and laptop: every write in the
shared folder is a Drive sync, and the Users table only needs to say roughly when someone was
here."""

Role = Literal["admin", "editor", "viewer"]
ROLES: tuple[Role, ...] = ("admin", "editor", "viewer")
ROLE_RANK: dict[str, int] = {"viewer": 0, "editor": 1, "admin": 2}
RosterSource = Literal["shared", "cache", "none"]
SharedState = Literal["ok", "missing", "incomplete", "unreachable", "unreadable"]

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
USERNAME_RULE = (
    "A username has 3 to 32 characters: small letters, digits, dots, dashes or underscores "
    "(for example maria or maria.k)."
)
MAX_DISPLAY_NAME = 64
MAX_MACHINE = 64

NEWER_VERSION_PROBLEM = (
    "the team's account list was saved by a newer version of CashCow Studio, which this "
    "version cannot read"
)
INCOMPLETE_PROBLEM = (
    "the shared folder already holds the team's files but not the team's accounts yet; "
    "Google Drive may still be downloading them"
)
FIRST_RUN_CHANNELS_WARNING = (
    "This shared folder already holds channels, but no accounts. If your team already signs "
    "in to CashCow Studio, Google Drive may still be downloading the accounts: wait until it "
    "has finished syncing, then reload this page. Only create the first admin if nobody on "
    "your team has an account yet (for example, you are updating from an earlier version)."
)
CARRY_OVER_CHANNELS_WARNING = (
    "The folder {folder} already holds channels, but no team accounts. If your team already "
    "signs in to CashCow Studio, Google Drive may still be downloading the accounts: wait "
    "until it has finished syncing, then try again. If nobody on your team has an account "
    "there yet, confirm, and the accounts of this laptop are copied into that folder."
)
CONFLICT_MESSAGE = (
    "Someone changed the same account on another laptop at the same moment. Try again."
)
LAST_ADMIN_MESSAGE = (
    "This is the last admin account, so it cannot be turned off or given a lower role. Make "
    "another person an admin first."
)

T = TypeVar("T")


# Errors (plain English, safe to show) ------------------------------------------------------


class UserStoreError(Exception):
    """Base class; the message is plain English and never holds a secret."""


class InvalidUserData(UserStoreError):
    """A username, name, role, laptop name or password that cannot be used (a 422)."""


class UserExists(UserStoreError):
    """The username is taken (a 409)."""


class UserNotFound(UserStoreError):
    """No such account (a 404)."""


class LastAdmin(UserStoreError):
    """The change would leave the team without an admin who can sign in (a 409)."""


class FirstRunDone(UserStoreError):
    """The first admin exists already (a 409)."""


class NeedsConfirmation(UserStoreError):
    """The shared folder needs a second look before going on (a 409); the message says why
    and the request may be sent again with the confirmation."""


class WrongPassword(UserStoreError):
    """The current password typed to change it is not right."""


class RosterReadOnly(UserStoreError):
    """The shared files cannot be read or written right now (a 503)."""


class RosterBusy(RosterReadOnly):
    """Another program holds the lock file for too long, or another laptop keeps changing
    the same account."""


class _Unchanged(Exception):  # noqa: N818 - a control-flow signal, not an error
    """Raised inside a change to skip the write and return ``result`` as it is."""

    def __init__(self, result: Any) -> None:
        super().__init__("unchanged")
        self.result = result


class _Conflict(Exception):  # noqa: N818 - a control-flow signal, not an error
    """An account file changed between reading it and writing it (another laptop's change
    arrived through Google Drive): read everything again and apply the change again."""


# Models -----------------------------------------------------------------------------------


class User(BaseModel):
    """One account (one file in ``team/users``). ``hash`` and ``salt`` never leave the server.

    ``revision`` goes up by one with every write of the account; ``last_login_at`` is not
    stored in the account file (see :meth:`UserStore.list`).
    """

    model_config = ConfigDict(extra="allow")

    username: str
    display_name: str
    role: Role = "editor"
    hash: str
    salt: str
    iterations: int = PBKDF2_ITERATIONS
    created_at: datetime
    created_by: str = ""
    disabled: bool = False
    machine: str | None = None
    must_change_password: bool = False
    last_login_at: datetime | None = None
    revision: int = 0

    @property
    def is_active_admin(self) -> bool:
        return self.role == "admin" and not self.disabled


class PublicUser(BaseModel):
    """What the API shows about an account: never the hash or the salt.

    ``machine`` is the laptop the account is locked to (``null`` when it works on any laptop).
    """

    username: str
    display_name: str
    role: Role
    machine: str | None = None
    must_change_password: bool = False
    disabled: bool = False
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    created_by: str = ""

    @classmethod
    def of(cls, user: User) -> PublicUser:
        return cls(
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            machine=user.machine,
            must_change_password=user.must_change_password,
            disabled=user.disabled,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
            created_by=user.created_by,
        )


@dataclass(frozen=True)
class Roster:
    """The accounts as last read, and where they came from."""

    users: dict[str, User] = field(default_factory=dict)
    source: RosterSource = "none"
    state: SharedState = "missing"
    """What happened to the shared files: read (``ok``), no account yet (``missing``), team
    files but no account yet (``incomplete``), folder not reachable (``unreachable``) or no
    account could be read (``unreadable``)."""
    problem: str = ""
    extra: tuple[str, ...] = ()
    """Account files this version could not use (saved by a newer version, or damaged); they
    are left untouched."""
    newer: bool = False
    """Some of ``extra`` look like accounts saved by a newer version of the app."""
    has_channels: bool = False
    """No account exists yet, but the shared folder already holds channels."""

    @property
    def needs_first_run(self) -> bool:
        """True only when no account exists anywhere this laptop can see."""
        return (
            not self.users
            and not self.extra
            and self.source != "cache"
            and self.state == "missing"
        )

    @property
    def first_run_warning(self) -> str | None:
        """What to confirm before the first admin is made here, or ``None``."""
        return FIRST_RUN_CHANNELS_WARNING if self.needs_first_run and self.has_channels else None

    @property
    def active_admins(self) -> list[User]:
        return [user for user in self.users.values() if user.is_active_admin]

    def admins_usable_on(self, machine: str | None = None) -> list[User]:
        """Active admins who can sign in on ``machine`` (this laptop by default)."""
        here = machine if machine is not None else machine_name()
        return [user for user in self.users.values() if can_manage_on(user, here)]

    @property
    def notice(self) -> str | None:
        """A plain-English notice for the screen, or ``None`` when all is well."""
        if self.source == "cache":
            return (
                "The team's account list in the shared folder cannot be read right now, so "
                "this laptop is using the copy it saved last time. You can sign in and work, "
                "but accounts cannot be added or changed until the shared folder is back."
            )
        if self.state == "incomplete":
            return (
                "This laptop cannot see the team's accounts yet. The shared folder already "
                "holds the team's files, so Google Drive is probably still downloading the "
                "accounts. Wait until Google Drive has finished syncing, then try again."
            )
        if self.state == "unreadable" and self.newer:
            return (
                "The team's account list was saved by a newer version of CashCow Studio, "
                "which this version cannot read. Update CashCow Studio on this laptop, then "
                "sign in."
            )
        if self.state in ("unreachable", "unreadable"):
            return (
                "The team's account list cannot be read"
                + (f": {self.problem}." if self.problem else ".")
                + " Check that the shared Google Drive folder is available."
            )
        if self.extra:
            count = len(self.extra)
            accounts = "account" if count == 1 else "accounts"
            why = (
                "they were saved by a newer version of CashCow Studio, so update CashCow "
                "Studio on this laptop"
                if self.newer
                else "their files are damaged or still being downloaded by Google Drive"
            )
            return (
                f"{count} of the team's {accounts} cannot be read on this laptop, so those "
                f"people cannot sign in here: {why}."
            )
        return None


# Helpers ----------------------------------------------------------------------------------


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def normalize_username(raw: object) -> str:
    """Lower-case and check a username; raises :class:`InvalidUserData`."""
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidUserData(f"Type a username. {USERNAME_RULE}")
    name = raw.strip().lower()
    if not USERNAME_RE.match(name):
        raise InvalidUserData(f"That cannot be used as a username. {USERNAME_RULE}")
    return name


def clean_display_name(raw: object, fallback: str) -> str:
    text = " ".join(str(raw or "").split())
    if not text:
        return fallback
    if len(text) > MAX_DISPLAY_NAME:
        raise InvalidUserData(f"The name is too long (at most {MAX_DISPLAY_NAME} characters).")
    if _has_control(text):
        raise InvalidUserData("The name contains a hidden control character. Type it again.")
    return text


def clean_machine(raw: object) -> str | None:
    """A laptop (Windows computer) name, or ``None`` for "any laptop"."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if len(text) > MAX_MACHINE:
        raise InvalidUserData(
            f"The laptop name is too long (at most {MAX_MACHINE} characters). Use the "
            "Windows computer name, for example LAPTOP-7Q2M."
        )
    if _has_control(text) or any(ch in text for ch in "\\/"):
        raise InvalidUserData(
            "The laptop name can only hold the Windows computer name, for example LAPTOP-7Q2M."
        )
    return text


def check_role(raw: object) -> Role:
    if raw not in ROLES:
        raise InvalidUserData("The role must be admin, editor or viewer.")
    return raw  # type: ignore[return-value]


def role_at_least(role: str, minimum: str) -> bool:
    return ROLE_RANK.get(role, -1) >= ROLE_RANK.get(minimum, 99)


def can_sign_in_on(user: User, machine: str) -> bool:
    """Turned on and not locked to another laptop."""
    return not user.disabled and (not user.machine or same_machine(user.machine, machine))


def can_manage_on(user: User, machine: str) -> bool:
    """An admin who can sign in on ``machine`` (and so can manage the team's accounts there)."""
    return user.role == "admin" and can_sign_in_on(user, machine)


def same_account(first: User, second: User) -> bool:
    """The same account and password, not just the same name: the username, when it was made
    and the salt of its current password all match (a copy made by
    :meth:`UserStore.carry_over_to` matches; a namesake in another team's folder does not)."""
    return (
        first.username == second.username
        and first.salt == second.salt
        and _aware(first.created_at) == _aware(second.created_at)
    )


def _has_control(text: str) -> bool:
    return any(ch < " " or ch == "\x7f" for ch in text)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _aware(datetime.fromisoformat(value))
    except ValueError:
        return None


def same_path(first: str | Path, second: str | Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(first)) == os.path.normcase(
            os.path.abspath(second)
        )
    except (TypeError, ValueError):
        return False


def parse_users(data: Any) -> tuple[dict[str, User], list[dict[str, Any]]]:
    """The accounts of a ``{"users": [...]}`` document (this laptop's saved copy) and the
    entries that could not be read.

    Raises ``ValueError`` when the document is not a list of accounts at all.
    """
    if not isinstance(data, dict) or not isinstance(data.get("users"), list):
        raise ValueError("it does not hold a list of accounts")
    users: dict[str, User] = {}
    extra: list[dict[str, Any]] = []
    for position, entry in enumerate(data["users"], start=1):
        if not isinstance(entry, dict):
            log.warning("Saved account entry %d is not an object; it is ignored", position)
            continue
        try:
            user = User.model_validate(entry)
        except ValidationError:
            log.warning("Saved account entry %d cannot be read by this version", position)
            extra.append(entry)
            continue
        if not USERNAME_RE.match(user.username) or user.username in users:
            log.warning("Saved account entry %d has an unusable or repeated name", position)
            extra.append(entry)
            continue
        users[user.username] = user
    return users, extra


def _account_data(user: User) -> dict[str, Any]:
    """What an account file (and this laptop's copy) holds: everything but the sign-in time."""
    return user.model_dump(mode="json", exclude={"last_login_at"})


def _dump_users(users: dict[str, User]) -> list[dict[str, Any]]:
    return [_account_data(users[name]) for name in sorted(users)]


def _is_account_file(entry: os.DirEntry[str]) -> bool:
    name = entry.name
    if name.startswith(".") or not name.lower().endswith(ACCOUNT_SUFFIX):
        return False
    try:
        return entry.is_file()
    except OSError:
        return False


def _is_team_file(name: str) -> bool:
    lowered = name.lower()
    return not (lowered.startswith((".", "~")) or lowered in IGNORED_FILES or lowered == LOCK_FILE)


def machine_file_name(machine: str) -> str:
    """A laptop name as a safe file name (``LAPTOP 7Q/2M`` -> ``laptop-7q-2m``)."""
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", machine.casefold()).strip(".-")
    return cleaned[:MAX_MACHINE] or "laptop"


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_only_message(state: str, problem: str, *, newer: bool = False) -> str:
    if state == "unreadable" and newer:
        return (
            "Accounts cannot be changed on this laptop: the team's account list was saved by a "
            "newer version of CashCow Studio. Update CashCow Studio on this laptop first."
        )
    if state == "incomplete":
        return (
            "Accounts cannot be changed yet: the shared folder already holds the team's files "
            "but not the team's accounts. Google Drive is probably still downloading them; "
            "wait until it has finished syncing, then try again."
        )
    if state == "unreachable":
        reason = problem or "the shared folder cannot be reached"
    elif state == "unreadable":
        reason = problem or "the account list in the shared folder cannot be read"
    elif state == "missing":
        reason = "the account list is missing from the shared folder"
    else:
        reason = problem or "the account list cannot be written"
    return (
        f"Accounts cannot be changed right now: {reason}. Try again when the shared Google "
        "Drive folder is available."
    )


@dataclass
class _Snapshot:
    """One fresh read of the account files."""

    state: SharedState
    problem: str = ""
    users: dict[str, User] = field(default_factory=dict)
    extra: list[str] = field(default_factory=list)
    newer: bool = False
    has_channels: bool = False


class _Missing:
    """No account file of that name."""


class _Damaged:
    """An account file that cannot be read."""


_MISSING = _Missing()
_DAMAGED = _Damaged()


# The store --------------------------------------------------------------------------------


class UserStore:
    """The roster of one shared folder, as seen from this laptop."""

    def __init__(self, shared_dir: Path | str, app_data_dir: Path | str) -> None:
        self.shared_dir = Path(shared_dir)
        self.app_data_dir = Path(app_data_dir)
        self.team_dir = self.shared_dir / TEAM_DIR
        self.users_dir = self.team_dir / USERS_DIR
        self.logins_dir = self.team_dir / LOGINS_DIR
        self.lock_path = self.team_dir / LOCK_FILE
        self.cache_path = self.app_data_dir / CACHE_FILE
        self._lock = threading.RLock()
        self._roster: Roster | None = None
        self._signature: tuple[Any, ...] | None = None

    def account_path(self, username: str) -> Path:
        return self.users_dir / f"{username}{ACCOUNT_SUFFIX}"

    # Reading ------------------------------------------------------------------------------

    def roster(self) -> Roster:
        """The current roster; the account files are read again whenever they changed."""
        with self._lock:
            signature = self._signature_now()
            if self._roster is None or signature != self._signature:
                self._roster = self._load()
                self._signature = signature
            return self._roster

    def refresh(self) -> Roster:
        """Read everything again, whatever the file dates say."""
        with self._lock:
            self._roster = None
            return self.roster()

    def get(self, username: str | None) -> User | None:
        key = (username or "").strip().lower() if isinstance(username, str) else ""
        if not key:
            return None
        return self.roster().users.get(key)

    def list(self) -> list[User]:
        """Everyone, by username, with when they last signed in on any laptop."""
        users = self.roster().users
        logins = self.last_logins() if users else {}
        result: list[User] = []
        for name in sorted(users):
            user = users[name]
            seen = [t for t in (logins.get(name), _aware(user.last_login_at)) if t is not None]
            latest = max(seen) if seen else None
            if latest != user.last_login_at:
                user = user.model_copy(update={"last_login_at": latest})
            result.append(user)
        return result

    def verify(self, username: str, password: str) -> User | None:
        """The account when the password is right, else ``None``.

        An unknown username costs the same work as a real check, and the comparison runs in
        constant time, so neither the answer nor its timing tells whether the account exists.
        """
        key = username.strip().lower() if isinstance(username, str) else ""
        user = self.get(key) if USERNAME_RE.match(key) else None
        if not isinstance(password, str):
            password = ""
        if user is None:
            burn_time(password)
            return None
        if not verify_password(password, user.hash, user.salt, user.iterations):
            return None
        return user

    def _signature_now(self) -> tuple[Any, ...]:
        files: list[tuple[str, int, int]] = []
        try:
            with os.scandir(self.users_dir) as listing:
                for entry in listing:
                    if _is_account_file(entry):
                        stat = entry.stat()
                        files.append((entry.name, stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            pass
        except OSError as exc:
            return ("error", type(exc).__name__)
        if files:
            return ("accounts", tuple(sorted(files)))
        # No account yet: what else the folder holds decides between first run and waiting.
        return ("none", self.shared_dir.is_dir(), self._has_team_files(), self._has_channels())

    def _read_accounts(self) -> _Snapshot:
        unreachable = f"the shared folder {self.shared_dir} cannot be reached"
        try:
            with os.scandir(self.users_dir) as listing:
                names = sorted(entry.name for entry in listing if _is_account_file(entry))
        except FileNotFoundError:
            if not self.shared_dir.is_dir():
                return _Snapshot("unreachable", unreachable)
            names = []
        except OSError as exc:
            if not self.shared_dir.is_dir():
                return _Snapshot("unreachable", unreachable)
            return _Snapshot("unreadable", f"the folder {self.users_dir} could not be read ({exc})")
        if not names:
            if self._has_team_files():
                return _Snapshot("incomplete", INCOMPLETE_PROBLEM)
            return _Snapshot("missing", has_channels=self._has_channels())
        snapshot = _Snapshot("ok")
        for name in names:
            user, newer = self._read_account_file(name)
            if user is None or user.username in snapshot.users:
                snapshot.extra.append(name)
                snapshot.newer = snapshot.newer or newer
                continue
            snapshot.users[user.username] = user
        if not snapshot.users:
            snapshot.state = "unreadable"
            snapshot.problem = (
                NEWER_VERSION_PROBLEM
                if snapshot.newer
                else f"none of the account files in {self.users_dir} can be read (they may be "
                "damaged, or Google Drive may still be downloading them)"
            )
        return snapshot

    def _read_account_file(self, name: str) -> tuple[User | None, bool]:
        """The account in one file, or ``(None, newer)`` when it cannot be used; ``newer`` is
        True when the file looks like an account saved by a newer version."""
        path = self.users_dir / name
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            log.warning("The account file %s cannot be read", name)
            return None, False
        if not isinstance(data, dict):
            log.warning("The account file %s does not hold an account", name)
            return None, False
        try:
            user = User.model_validate(data)
        except ValidationError:
            log.warning("The account file %s cannot be read by this version", name)
            return None, True
        stem = name[: -len(ACCOUNT_SUFFIX)].lower()
        if user.username != stem or not USERNAME_RE.match(user.username):
            log.warning("The account file %s does not match the username inside it", name)
            return None, False
        return user, False

    def _has_team_files(self) -> bool:
        """Files of the team in ``team/`` (besides the lock file): accounts are on their way."""
        try:
            with os.scandir(self.team_dir) as listing:
                entries = list(listing)
        except FileNotFoundError:
            return False
        except OSError:
            return True  # cannot look inside: do not assume it is empty
        for entry in entries:
            try:
                if entry.is_file():
                    if _is_team_file(entry.name):
                        return True
                elif entry.is_dir():
                    with os.scandir(entry.path) as inner:
                        if any(e.is_file() and _is_team_file(e.name) for e in inner):
                            return True
            except OSError:
                return True
        return False

    def _has_channels(self) -> bool:
        channels = self.shared_dir / CHANNELS_DIR
        try:
            with os.scandir(channels) as listing:
                for entry in listing:
                    if entry.name.startswith((".", "_")) or not entry.is_dir():
                        continue
                    if os.path.isfile(os.path.join(entry.path, CHANNEL_FILE)):
                        return True
        except OSError:
            return False
        return False

    def _load(self) -> Roster:
        snapshot = self._read_accounts()
        if snapshot.state == "ok":
            roster = Roster(
                users=snapshot.users,
                source="shared",
                state="ok",
                extra=tuple(snapshot.extra),
                newer=snapshot.newer,
            )
            self._mirror(roster)
            return roster
        if snapshot.problem:
            log.warning("The team roster could not be used: %s", snapshot.problem)
        # Accounts saved by a newer version are current, not lost: the saved copy is older
        # than them, so it is not used in their place.
        if not (snapshot.state == "unreadable" and snapshot.newer):
            cached = self._read_cache()
            if cached:
                return Roster(
                    users=cached,
                    source="cache",
                    state=snapshot.state,
                    problem=snapshot.problem,
                    extra=tuple(snapshot.extra),
                )
        return Roster(
            users={},
            source="none",
            state=snapshot.state,
            problem=snapshot.problem,
            extra=tuple(snapshot.extra),
            newer=snapshot.newer,
            has_channels=snapshot.has_channels,
        )

    def _read_mirrors(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return []
        mirrors = data.get("mirrors") if isinstance(data, dict) else None
        return [m for m in mirrors if isinstance(m, dict)] if isinstance(mirrors, list) else []

    def _read_cache(self) -> dict[str, User] | None:
        """The copy saved from *this* shared folder, or ``None``."""
        for mirror in self._read_mirrors():
            if same_path(str(mirror.get("source_path", "")), self.users_dir):
                try:
                    users, _extra = parse_users(mirror)
                except ValueError:
                    return None
                return users or None
        return None

    def _mirror(self, roster: Roster) -> None:
        """Keep this laptop's copy of this shared folder's accounts in step (best effort).

        A read that found no usable account never replaces the copy."""
        if not roster.users:
            return
        users = _dump_users(roster.users)
        with _CACHE_LOCK:
            mirrors = self._read_mirrors()
            others = [
                m for m in mirrors if not same_path(str(m.get("source_path", "")), self.users_dir)
            ]
            mine = [m for m in mirrors if m not in others]
            if mine and mine[0].get("users") == users:
                return
            entry = {
                "source_path": str(self.users_dir),
                "mirrored_at": utc_now().isoformat(),
                "users": users,
            }
            payload = {"version": CACHE_VERSION, "mirrors": [entry, *others][:MAX_MIRRORS]}
            try:
                atomic_write_text(
                    self.cache_path,
                    json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    private=True,
                )
            except OSError as exc:
                log.warning("This laptop's copy of the team roster could not be saved: %s", exc)

    # Sign-in times ------------------------------------------------------------------------

    def logins_path(self, machine: str | None = None) -> Path:
        """This laptop's file of sign-in times (only this laptop writes it)."""
        return self.logins_dir / f"{machine_file_name(machine or machine_name())}.json"

    def last_logins(self) -> dict[str, datetime]:
        """When each person last signed in, on any laptop of the team (best effort)."""
        latest: dict[str, datetime] = {}
        try:
            with os.scandir(self.logins_dir) as listing:
                paths = [Path(entry.path) for entry in listing if _is_account_file(entry)]
        except OSError:
            return latest
        for path in paths:
            data = _read_json_object(path)
            logins = data.get("logins") if data else None
            if not isinstance(logins, dict):
                continue
            for name, value in logins.items():
                moment = _parse_time(value)
                if isinstance(name, str) and moment is not None:
                    if name not in latest or moment > latest[name]:
                        latest[name] = moment
        return latest

    def record_login(self, username: str, when: datetime | None = None) -> None:
        """Note the sign-in time in this laptop's own file in ``team/logins`` (best effort:
        a read-only roster, an offline folder or a busy file is fine). Account files are never
        written for a sign-in."""
        moment = _aware(when) or utc_now()
        user = self.get(username)
        if user is None or self.roster().source != "shared":
            return
        machine = machine_name()
        path = self.logins_path(machine)
        with _LOGINS_LOCK:
            data = _read_json_object(path) or {}
            raw = data.get("logins")
            logins = (
                {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}
                if isinstance(raw, dict)
                else {}
            )
            last = _parse_time(logins.get(user.username))
            if last is not None and timedelta(0) <= moment - last < LAST_LOGIN_EVERY:
                return
            logins[user.username] = moment.isoformat()
            payload = {
                "version": LOGINS_VERSION,
                "machine": machine,
                "updated_at": utc_now().isoformat(),
                "logins": dict(sorted(logins.items())),
            }
            try:
                atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            except OSError as exc:
                log.warning("The sign-in time could not be saved to the shared folder: %s", exc)

    # Writing ------------------------------------------------------------------------------

    def _mutate(self, change: Callable[[dict[str, User]], T], *, create: bool = False) -> T:
        """Apply ``change`` to a fresh read of the account files and write the accounts it
        changed. ``create`` allows a folder without any account (the first admin, a copy)."""
        with self._lock:
            if not self.shared_dir.is_dir():
                raise RosterReadOnly(
                    read_only_message(
                        "unreachable", f"the shared folder {self.shared_dir} cannot be reached"
                    )
                )
            for attempt in range(CHANGE_ATTEMPTS):
                with self._file_lock():
                    snapshot = self._read_accounts()
                    if not (snapshot.state == "ok" or (create and snapshot.state == "missing")):
                        raise RosterReadOnly(
                            read_only_message(
                                snapshot.state, snapshot.problem, newer=snapshot.newer
                            )
                        )
                    before = snapshot.users
                    users = {name: user.model_copy(deep=True) for name, user in before.items()}
                    try:
                        result = change(users)
                    except _Unchanged as same:
                        return same.result
                    changed = [
                        name
                        for name in sorted(users)
                        if name not in before
                        or _account_data(users[name]) != _account_data(before[name])
                    ]
                    unreadable = {
                        file_name[: -len(ACCOUNT_SUFFIX)].lower() for file_name in snapshot.extra
                    }
                    for name in changed:
                        if name not in before and name in unreadable:
                            raise UserExists(
                                f"The username '{name}' is already used by an account this "
                                "version of CashCow Studio cannot read. Choose another one."
                            )
                    try:
                        for name in changed:
                            old = before.get(name)
                            self._write_account(users[name], None if old is None else old.revision)
                    except _Conflict:
                        if attempt + 1 == CHANGE_ATTEMPTS:
                            raise RosterBusy(CONFLICT_MESSAGE) from None
                        continue
                break
            self._roster = None
            self.roster()  # read back what was written (and refresh this laptop's copy)
            return result

    def _current_revision(self, path: Path) -> int | _Missing | _Damaged:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return _MISSING
        except (OSError, ValueError):
            return _DAMAGED
        revision = data.get("revision", 0) if isinstance(data, dict) else None
        return revision if isinstance(revision, int) else _DAMAGED

    def _write_account(self, user: User, expected: int | None) -> None:
        """Write one account file, if it is still the revision this change was based on."""
        path = self.account_path(user.username)
        current = self._current_revision(path)
        if (expected is None and current is not _MISSING) or (
            expected is not None and current != expected
        ):
            raise _Conflict
        user.revision = (expected or 0) + 1
        text = json.dumps(_account_data(user), indent=2, ensure_ascii=False) + "\n"
        for attempt in range(WRITE_RETRIES):
            try:
                atomic_write_text(path, text)
                return
            except PermissionError:
                # Drive, an antivirus or Explorer's preview can hold the file for a moment.
                if attempt + 1 == WRITE_RETRIES:
                    raise
                time.sleep(0.2)

    @contextlib.contextmanager
    def _file_lock(self) -> Iterator[None]:
        try:
            self.team_dir.mkdir(exist_ok=True)
        except OSError as exc:
            raise RosterReadOnly(
                read_only_message(
                    "unreachable", f"the folder {self.team_dir} cannot be made ({exc})"
                )
            ) from exc
        deadline = time.monotonic() + LOCK_WAIT_S
        while True:
            try:
                handle = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            except PermissionError as exc:
                # On Windows a lock file that is being deleted refuses a new one for a moment;
                # without any lock file this is a real permission problem.
                if not self.lock_path.exists():
                    raise RosterReadOnly(read_only_message("unreadable", str(exc))) from exc
            except OSError as exc:
                raise RosterReadOnly(read_only_message("unreadable", str(exc))) from exc
            else:
                with contextlib.suppress(OSError):
                    os.write(handle, f"{os.getpid()} {machine_name()}\n".encode())
                os.close(handle)
                break
            if self._lock_is_stale():
                with contextlib.suppress(OSError):
                    os.unlink(self.lock_path)
                continue
            if time.monotonic() >= deadline:
                raise RosterBusy(
                    "Someone else is changing the team's accounts on this laptop right now. "
                    "Try again in a moment."
                )
            time.sleep(0.05)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                os.unlink(self.lock_path)

    def _lock_is_stale(self) -> bool:
        try:
            age = time.time() - os.stat(self.lock_path).st_mtime
        except OSError:
            return False
        return age > LOCK_STALE_S

    # Accounts -----------------------------------------------------------------------------

    def create_first_admin(
        self, username: str, display_name: str, password: str, *, confirmed: bool = False
    ) -> User:
        """The very first account (an admin), only while no account exists at all.

        ``confirmed`` answers :attr:`Roster.first_run_warning` (the folder already holds
        channels); without it such a folder raises :class:`NeedsConfirmation`.
        """
        key = normalize_username(username)
        name = clean_display_name(display_name, key)
        try:
            check_password_rule(password)
        except PasswordRuleError as exc:
            raise InvalidUserData(str(exc)) from exc
        roster = self.roster()
        if not roster.needs_first_run:
            if roster.users or roster.source == "cache":
                raise FirstRunDone("The first admin account exists already. Sign in instead.")
            raise RosterReadOnly(
                roster.notice or read_only_message(roster.state, roster.problem, newer=roster.newer)
            )
        warning = roster.first_run_warning
        if warning and not confirmed:
            raise NeedsConfirmation(warning)
        hashed = hash_password(password)

        def change(users: dict[str, User]) -> User:
            if users:
                raise FirstRunDone("The first admin account exists already. Sign in instead.")
            users[key] = User(
                username=key,
                display_name=name,
                role="admin",
                hash=hashed.hash,
                salt=hashed.salt,
                iterations=hashed.iterations,
                created_at=utc_now(),
                created_by=key,
                must_change_password=False,
            )
            return users[key]

        return self._mutate(change, create=True)

    def add(
        self,
        username: str,
        display_name: str = "",
        role: str = "editor",
        machine: str | None = None,
        *,
        created_by: str = "",
        password: str | None = None,
        must_change_password: bool = True,
    ) -> tuple[User, str]:
        """Create an account. Returns it with its password, which is shown to the admin once."""
        key = normalize_username(username)
        name = clean_display_name(display_name, key)
        checked_role = check_role(role)
        locked_to = clean_machine(machine)
        if password is None:
            password = generate_password()
        else:
            try:
                check_password_rule(password)
            except PasswordRuleError as exc:
                raise InvalidUserData(str(exc)) from exc
        hashed = hash_password(password)

        def change(users: dict[str, User]) -> User:
            if key in users:
                raise UserExists(f"The username '{key}' is taken. Choose another one.")
            users[key] = User(
                username=key,
                display_name=name,
                role=checked_role,
                hash=hashed.hash,
                salt=hashed.salt,
                iterations=hashed.iterations,
                created_at=utc_now(),
                created_by=created_by,
                machine=locked_to,
                must_change_password=must_change_password,
            )
            return users[key]

        return self._mutate(change), password

    def change_password(self, username: str, current_password: str, new_password: str) -> User:
        """The signed-in person's own change; clears ``must_change_password``.

        The new password gets a new salt, which ends every earlier session of the account
        (see :func:`.sessions.account_key`)."""
        try:
            check_password_rule(new_password)
        except PasswordRuleError as exc:
            raise InvalidUserData(str(exc)) from exc
        user = self.get(username)
        if user is None:
            raise UserNotFound("Your account no longer exists. Ask an admin.")
        if not verify_password(current_password or "", user.hash, user.salt, user.iterations):
            raise WrongPassword("Your current password is not right.")
        if current_password == new_password:
            raise InvalidUserData("Choose a new password that is different from the current one.")
        hashed = hash_password(new_password)
        key = user.username

        def change(users: dict[str, User]) -> User:
            target = users.get(key)
            if target is None:
                raise UserNotFound("Your account no longer exists. Ask an admin.")
            target.hash = hashed.hash
            target.salt = hashed.salt
            target.iterations = hashed.iterations
            target.must_change_password = False
            return target

        return self._mutate(change)

    def reset_password(self, username: str) -> tuple[User, str]:
        """A new generated password (shown to the admin once); the person must change it.
        Every session of the account ends (new salt)."""
        key = username.strip().lower() if isinstance(username, str) else ""
        password = generate_password()
        hashed = hash_password(password)

        def change(users: dict[str, User]) -> User:
            target = users.get(key)
            if target is None:
                raise UserNotFound("There is no such account.")
            target.hash = hashed.hash
            target.salt = hashed.salt
            target.iterations = hashed.iterations
            target.must_change_password = True
            return target

        return self._mutate(change), password

    def update(
        self,
        username: str,
        *,
        display_name: str | None = None,
        role: str | None = None,
        machine: str | None = None,
        disabled: bool | None = None,
        fields: set[str] | frozenset[str] | None = None,
    ) -> tuple[User, dict[str, list[Any]]]:
        """Change the given fields of an account in one write.

        ``fields`` names the fields to change (so ``machine=None`` can mean "unlock"); by
        default every argument that is not ``None``. Returns the account and the changes as
        ``{field: [before, after]}``.

        The team always keeps an admin who can sign in *on this laptop*: the last active
        admin can be neither turned off nor given a lower role, and the last admin who is not
        locked to another laptop can be neither turned off, given a lower role nor locked to
        another laptop.
        """
        key = username.strip().lower() if isinstance(username, str) else ""
        given = {
            "display_name": display_name,
            "role": role,
            "machine": machine,
            "disabled": disabled,
        }
        wanted = (
            set(fields) & set(given)
            if fields is not None
            else {name for name, value in given.items() if value is not None}
        )
        new_values: dict[str, Any] = {}
        if "display_name" in wanted:
            new_values["display_name"] = clean_display_name(display_name, "")
            if not new_values["display_name"]:
                raise InvalidUserData("Type the person's name.")
        if "role" in wanted:
            new_values["role"] = check_role(role)
        if "machine" in wanted:
            new_values["machine"] = clean_machine(machine)
        if "disabled" in wanted:
            new_values["disabled"] = bool(disabled)
        here = machine_name()

        def change(users: dict[str, User]) -> tuple[User, dict[str, list[Any]]]:
            target = users.get(key)
            if target is None:
                raise UserNotFound("There is no such account.")
            after = target.model_copy(update=new_values)
            others = [user for user in users.values() if user.username != key]
            if target.is_active_admin and not after.is_active_admin:
                if not any(user.is_active_admin for user in others):
                    raise LastAdmin(LAST_ADMIN_MESSAGE)
            if can_manage_on(target, here) and not can_manage_on(after, here):
                if not any(can_manage_on(user, here) for user in others):
                    raise LastAdmin(_last_admin_here_message(after, here))
            changes: dict[str, list[Any]] = {}
            for name, value in new_values.items():
                previous = getattr(target, name)
                if previous != value:
                    changes[name] = [previous, value]
                    setattr(target, name, value)
            if not changes:
                raise _Unchanged((target, changes))
            return target, changes

        return self._mutate(change)

    def set_role(self, username: str, role: str) -> User:
        return self.update(username, role=role, fields={"role"})[0]

    def set_disabled(self, username: str, disabled: bool) -> User:
        return self.update(username, disabled=disabled, fields={"disabled"})[0]

    def set_machine(self, username: str, machine: str | None) -> User:
        return self.update(username, machine=machine, fields={"machine"})[0]

    def carry_over_to(self, target: UserStore, *, confirmed: bool = False) -> bool:
        """Copy this roster into ``target`` (a newly chosen shared folder) when that folder has
        no accounts yet, so choosing the team's Drive folder never locks the admin out.

        Returns True when the accounts were copied, False when ``target`` keeps its own
        accounts (or there was nothing to copy). Raises :class:`RosterReadOnly` when the new
        folder's accounts cannot be read, when it holds team files but no account yet (Google
        Drive is still downloading), or when this laptop knew accounts there that are missing
        now; :class:`NeedsConfirmation` when it holds channels but no account and
        ``confirmed`` is False; ``OSError`` when the accounts cannot be written.
        """
        if same_path(self.users_dir, target.users_dir):
            return False
        source = self.roster()
        existing = target.refresh()
        if existing.state == "ok":
            return False
        if existing.state in ("unreachable", "unreadable"):
            raise RosterReadOnly(
                "The account list in the new shared folder cannot be read"
                + (f" ({existing.problem})" if existing.problem else "")
                + ", so the folder was not changed."
            )
        if existing.state == "incomplete" or existing.source == "cache":
            raise RosterReadOnly(
                "The new shared folder already holds the team's files but not the team's "
                "accounts yet. Google Drive is probably still downloading them, so the folder "
                "was not changed. Wait until Google Drive has finished syncing, then try again."
            )
        if not source.users:
            return False
        if existing.has_channels and not confirmed:
            raise NeedsConfirmation(CARRY_OVER_CHANNELS_WARNING.format(folder=target.shared_dir))
        copies = {
            name: user.model_copy(deep=True, update={"revision": 0, "last_login_at": None})
            for name, user in source.users.items()
        }

        def change(users: dict[str, User]) -> bool:
            if any(name not in copies or not same_account(user, copies[name])
                   for name, user in users.items()):
                raise _Unchanged(False)  # another team's accounts arrived meanwhile
            missing = [name for name in copies if name not in users]
            if not missing:
                raise _Unchanged(True)
            for name in missing:
                users[name] = copies[name]
            return True

        return target._mutate(change, create=True)


def _last_admin_here_message(after: User, here: str) -> str:
    if after.role == "admin" and not after.disabled and after.machine:
        return (
            f"This is the last admin account that can sign in on this laptop ({here}), so it "
            "cannot be locked to another laptop: nobody could manage the team's accounts here "
            "any more. Make another person an admin first, or lock it to this laptop."
        )
    return (
        f"This is the last admin account that can sign in on this laptop ({here}): every other "
        "admin is turned off or locked to another laptop. Make another person an admin, or "
        "remove the laptop lock of another admin, first."
    )
