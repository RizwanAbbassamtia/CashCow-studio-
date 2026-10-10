"""Accounts: PBKDF2 hashing, generated passwords and the team roster in the shared folder (one
file per account in ``team/users/``) with this laptop's read-only copy (``users.cache.json``),
the per-laptop sign-in times (``team/logins/``), the first-run guards and the last admin."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cashcow_studio.auth import machine_name, passwords
from cashcow_studio.auth import users as users_module
from cashcow_studio.auth.users import (
    FirstRunDone,
    InvalidUserData,
    LastAdmin,
    NeedsConfirmation,
    RosterBusy,
    RosterReadOnly,
    User,
    UserExists,
    UserNotFound,
    UserStore,
    WrongPassword,
    same_account,
)

ADMIN_PASSWORD = "first-admin-pass"
ACCOUNT_FIELDS = {
    "username", "display_name", "role", "hash", "salt", "iterations", "created_at",
    "created_by", "disabled", "machine", "must_change_password", "revision",
}


def make_store(tmp_path: Path, shared: str = "shared", app: str = "app") -> UserStore:
    (tmp_path / shared).mkdir(parents=True, exist_ok=True)
    (tmp_path / app).mkdir(parents=True, exist_ok=True)
    return UserStore(tmp_path / shared, tmp_path / app)


def with_admin(tmp_path: Path, shared: str = "shared", app: str = "app") -> UserStore:
    store = make_store(tmp_path, shared, app)
    store.create_first_admin("Imran", "Imran", ADMIN_PASSWORD)
    return store


def read_account(store: UserStore, name: str) -> dict:
    return json.loads(store.account_path(name).read_text(encoding="utf-8"))


def write_account(store: UserStore, name: str, data: object) -> None:
    store.users_dir.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data)
    store.account_path(name).write_text(text, encoding="utf-8")


# Passwords ------------------------------------------------------------------------------------


@pytest.mark.real_password_hashing
def test_passwords_are_pbkdf2_sha256_with_a_salt_per_user() -> None:
    hashed = passwords.hash_password("correct horse 1")
    assert hashed.iterations == 240_000
    salt = base64.b64decode(hashed.salt)
    key = base64.b64decode(hashed.hash)
    assert len(salt) == 16 and len(key) == 32
    assert key == hashlib.pbkdf2_hmac("sha256", b"correct horse 1", salt, 240_000, dklen=32)
    again = passwords.hash_password("correct horse 1")
    assert again.salt != hashed.salt and again.hash != hashed.hash
    assert passwords.verify_password("correct horse 1", hashed.hash, hashed.salt, 240_000)
    assert not passwords.verify_password("correct horse 2", hashed.hash, hashed.salt, 240_000)


def test_damaged_hash_records_never_match() -> None:
    good = passwords.hash_password("some-password-1")
    assert not passwords.verify_password("some-password-1", "not base64!", good.salt, 1000)
    assert not passwords.verify_password("some-password-1", good.hash, "", good.iterations)
    assert not passwords.verify_password("some-password-1", good.hash, good.salt, 0)
    assert not passwords.verify_password("some-password-1", good.hash, good.salt, 10**9)
    # A lone surrogate from a JSON body is hashed, not a crash.
    assert not passwords.verify_password("\ud800abc", good.hash, good.salt, good.iterations)


def test_generated_passwords_are_strong_but_easy_to_type() -> None:
    seen: set[str] = set()
    for _ in range(300):
        value = passwords.generate_password()
        assert len(value) == 14
        assert set(value) <= set(passwords.PASSWORD_ALPHABET)
        assert not set(value) & set("0O1lI")
        assert any(c.isupper() for c in value) and any(c.islower() for c in value)
        assert any(c.isdigit() for c in value)
        passwords.check_password_rule(value)
        seen.add(value)
    assert len(seen) == 300


def test_the_password_rule_is_ten_characters() -> None:
    for bad in ("", "          ", "short", "123456789"):
        with pytest.raises(passwords.PasswordRuleError) as info:
            passwords.check_password_rule(bad)
        assert "10 characters" in str(info.value)
    with pytest.raises(passwords.PasswordRuleError):
        passwords.check_password_rule("x" * 2000)
    assert passwords.check_password_rule("1234567890") == "1234567890"


# The roster -----------------------------------------------------------------------------------


def test_first_admin_only_while_no_account_exists(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    roster = store.roster()
    assert roster.needs_first_run and roster.source == "none" and roster.state == "missing"
    assert roster.first_run_warning is None
    with pytest.raises(InvalidUserData):
        store.create_first_admin("imran", "Imran", "short")
    admin = store.create_first_admin("  Imran ", "Imran", ADMIN_PASSWORD)
    assert (admin.username, admin.role, admin.must_change_password) == ("imran", "admin", False)
    assert store.roster().source == "shared" and not store.roster().needs_first_run
    with pytest.raises(FirstRunDone):
        store.create_first_admin("other", "Other", "another-password")

    assert [p.name for p in store.users_dir.iterdir()] == ["imran.json"]
    text = store.account_path("imran").read_text(encoding="utf-8")
    assert ADMIN_PASSWORD not in text
    entry = json.loads(text)
    assert set(entry) == ACCOUNT_FIELDS and entry["revision"] == 1
    assert store.cache_path.is_file()
    assert not store.lock_path.exists()


def test_accounts_get_a_one_time_password_and_names_ignore_case(tmp_path: Path) -> None:
    store = with_admin(tmp_path)
    user, password = store.add("Maria.K", "Maria K", "editor", created_by="imran")
    assert (user.username, user.display_name, user.role) == ("maria.k", "Maria K", "editor")
    assert user.must_change_password and user.created_by == "imran" and user.machine is None
    assert password not in store.account_path("maria.k").read_text(encoding="utf-8")
    assert store.verify("MARIA.K", password) is not None
    assert store.verify("maria.k", password + "x") is None
    assert store.verify("nobody", password) is None
    assert store.verify("x", password) is None
    with pytest.raises(UserExists):
        store.add("maria.k")
    for bad_name in ("ab", "has space", "a" * 33, "émile", ""):
        with pytest.raises(InvalidUserData):
            store.add(bad_name)
    with pytest.raises(InvalidUserData):
        store.add("valid.name", role="owner")
    with pytest.raises(InvalidUserData):
        store.add("valid.name", machine="C:\\Windows")
    assert [u.username for u in store.list()] == ["imran", "maria.k"]
    assert sorted(p.name for p in store.users_dir.iterdir()) == ["imran.json", "maria.k.json"]


def test_an_unknown_name_costs_as_much_as_a_wrong_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = with_admin(tmp_path)
    counts: list[int] = []
    original = passwords.derive

    def counting(password: str, salt: bytes, iterations: int) -> bytes:
        counts.append(iterations)
        return original(password, salt, iterations)

    monkeypatch.setattr(passwords, "derive", counting)
    assert store.verify("nobody", "whatever-123") is None
    assert store.verify("imran", "wrong-password") is None
    assert counts == [passwords.PBKDF2_ITERATIONS, passwords.PBKDF2_ITERATIONS]


def test_the_last_active_admin_cannot_be_turned_off_or_demoted(tmp_path: Path) -> None:
    store = with_admin(tmp_path)
    store.add("maria", "Maria", "editor")
    with pytest.raises(LastAdmin):
        store.set_disabled("imran", True)
    with pytest.raises(LastAdmin):
        store.set_role("imran", "viewer")
    store.set_role("maria", "admin")
    assert store.set_disabled("imran", True).disabled is True
    with pytest.raises(LastAdmin):  # maria is now the only admin who is turned on
        store.set_role("maria", "editor")
    assert store.set_disabled("imran", False).disabled is False
    assert store.set_role("maria", "editor").role == "editor"


def test_the_last_admin_who_can_sign_in_here_stays_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPUTERNAME", "LAPTOP-HERE")
    store = with_admin(tmp_path)
    # The only admin cannot be locked to another (or a mistyped) laptop...
    with pytest.raises(LastAdmin) as info:
        store.set_machine("imran", "LAPTOP-TYPO")
    assert "last admin" in str(info.value) and "LAPTOP-HERE" in str(info.value)
    assert store.get("imran").machine is None
    # ...but may be locked to this laptop, and unlocked again.
    assert store.set_machine("imran", "laptop-here").machine == "laptop-here"
    assert store.set_machine("imran", None).machine is None

    # An admin locked to another laptop does not count as an admin who can sign in here.
    store.add("boss2", "Boss Two", "admin", machine="SOME-OTHER-PC")
    for change in (
        lambda: store.set_role("imran", "editor"),
        lambda: store.set_disabled("imran", True),
        lambda: store.set_machine("imran", "SOME-OTHER-PC"),
    ):
        with pytest.raises(LastAdmin):
            change()
    assert store.get("imran").is_active_admin and store.get("imran").machine is None
    assert [u.username for u in store.roster().admins_usable_on("LAPTOP-HERE")] == ["imran"]
    # On the other laptop boss2 can sign in, so there imran may be changed.
    monkeypatch.setenv("COMPUTERNAME", "SOME-OTHER-PC")
    assert store.set_machine("imran", "LAPTOP-HERE").machine == "LAPTOP-HERE"
    # ...but boss2 is now the last admin who can sign in there.
    with pytest.raises(LastAdmin):
        store.set_machine("boss2", "LAPTOP-HERE")
    # And the last active admin of all can never be turned off, wherever it is locked.
    monkeypatch.setenv("COMPUTERNAME", "LAPTOP-HERE")
    store.set_role("boss2", "editor")
    with pytest.raises(LastAdmin):
        store.set_disabled("imran", True)


def test_update_reports_its_changes_and_none_unlocks_the_laptop(tmp_path: Path) -> None:
    store = with_admin(tmp_path)
    store.add("maria", "Maria", "editor")
    user, changes = store.update("maria", machine="LAPTOP-7Q2M", fields={"machine"})
    assert user.machine == "LAPTOP-7Q2M" and changes == {"machine": [None, "LAPTOP-7Q2M"]}
    user, changes = store.update("maria", machine=None, fields={"machine"})
    assert user.machine is None and changes == {"machine": ["LAPTOP-7Q2M", None]}
    assert read_account(store, "maria")["revision"] == 3
    before = store.account_path("maria").stat().st_mtime_ns
    _user, changes = store.update("maria", role="editor", display_name="Maria")
    assert changes == {} and store.account_path("maria").stat().st_mtime_ns == before
    with pytest.raises(UserNotFound):
        store.update("ghost", role="viewer")
    with pytest.raises(InvalidUserData):
        store.update("maria", display_name="   ", fields={"display_name"})


def test_reset_and_change_password(tmp_path: Path) -> None:
    store = with_admin(tmp_path)
    _user, first = store.add("maria", "Maria", "editor")
    old_salt = store.get("maria").salt
    user, second = store.reset_password("maria")
    assert user.must_change_password and second != first and user.salt != old_salt
    assert store.verify("maria", first) is None and store.verify("maria", second) is not None
    with pytest.raises(WrongPassword):
        store.change_password("maria", "not-the-password", "a-new-password")
    with pytest.raises(InvalidUserData):
        store.change_password("maria", second, "short")
    with pytest.raises(InvalidUserData):
        store.change_password("maria", second, second)
    changed = store.change_password("maria", second, "a-new-password")
    assert changed.must_change_password is False and changed.salt != user.salt
    assert store.verify("maria", "a-new-password") is not None
    with pytest.raises(UserNotFound):
        store.reset_password("ghost")


def test_the_copy_on_this_laptop_is_used_read_only_when_the_shared_files_are_damaged(
    tmp_path: Path,
) -> None:
    store = with_admin(tmp_path)
    write_account(store, "imran", "{ damaged")
    later = UserStore(store.shared_dir, store.app_data_dir)  # the next start of the app
    roster = later.roster()
    assert roster.source == "cache" and roster.state == "unreadable"
    assert "imran" in roster.users and not roster.needs_first_run
    assert roster.notice and "cannot be read" in roster.notice
    assert later.verify("imran", ADMIN_PASSWORD) is not None  # people can still sign in
    with pytest.raises(RosterReadOnly):
        later.add("maria")
    with pytest.raises(FirstRunDone):
        later.create_first_admin("other", "Other", "another-password")
    assert store.account_path("imran").read_text(encoding="utf-8") == "{ damaged"

    store.account_path("imran").unlink()  # gone from a reachable folder that held accounts
    roster = UserStore(store.shared_dir, store.app_data_dir).roster()
    assert roster.source == "cache" and roster.state == "missing" and not roster.needs_first_run


def test_no_first_run_when_the_folder_is_unreachable_or_the_files_damaged(tmp_path: Path) -> None:
    gone = UserStore(tmp_path / "G-drive" / "Team", tmp_path / "app")
    roster = gone.roster()
    assert (roster.source, roster.state, roster.needs_first_run) == ("none", "unreachable", False)
    assert roster.notice
    with pytest.raises(RosterReadOnly):
        gone.create_first_admin("imran", "Imran", ADMIN_PASSWORD)

    damaged = make_store(tmp_path, "damaged")
    write_account(damaged, "imran", "[]")
    roster = damaged.roster()
    assert (roster.source, roster.state, roster.needs_first_run) == ("none", "unreadable", False)
    assert roster.extra == ("imran.json",)
    with pytest.raises(RosterReadOnly):
        damaged.create_first_admin("imran", "Imran", ADMIN_PASSWORD)


def test_accounts_from_a_newer_version_never_reopen_the_first_run(tmp_path: Path) -> None:
    store = with_admin(tmp_path)
    store.add("maria", "Maria", "editor")
    saved_copy = store.cache_path.read_text(encoding="utf-8")
    for name in ("imran", "maria"):
        data = read_account(store, name)
        data["role"] = "owner"  # a role this version does not know
        write_account(store, name, data)
    later = UserStore(store.shared_dir, store.app_data_dir)
    roster = later.roster()
    assert roster.users == {} and set(roster.extra) == {"imran.json", "maria.json"}
    assert roster.newer and roster.state == "unreadable"
    # The accounts are current, just unreadable here: the older copy is not used instead.
    assert roster.source == "none" and not roster.needs_first_run
    assert roster.notice and "newer version" in roster.notice and "Update" in roster.notice
    with pytest.raises(RosterReadOnly) as info:
        later.create_first_admin("mallory", "M", "mallory-password")
    assert "newer version" in str(info.value)
    with pytest.raises(RosterReadOnly):
        later.add("mallory", role="admin")
    assert later.verify("imran", ADMIN_PASSWORD) is None
    assert store.cache_path.read_text(encoding="utf-8") == saved_copy  # not emptied
    assert sorted(p.name for p in store.users_dir.iterdir()) == ["imran.json", "maria.json"]
    assert read_account(store, "imran")["role"] == "owner"  # never rewritten

    # One readable account is enough to sign in; the other is reported, not lost.
    data = read_account(store, "imran")
    data["role"] = "admin"
    write_account(store, "imran", data)
    roster = later.roster()
    assert set(roster.users) == {"imran"} and roster.extra == ("maria.json",)
    assert roster.state == "ok" and not roster.needs_first_run
    assert roster.notice and "1 of the team's account" in roster.notice
    with pytest.raises(UserExists):  # the name of an unreadable account stays taken
        later.add("maria")


def test_each_shared_folder_has_its_own_copy(tmp_path: Path) -> None:
    first = with_admin(tmp_path, "team-a")
    second = make_store(tmp_path, "team-b")
    assert second.roster().needs_first_run  # A's copy is not B's roster
    second.create_first_admin("boss", "Boss", "boss-password-1")
    write_account(first, "imran", "{ damaged")
    write_account(second, "boss", "{ damaged")
    assert set(UserStore(first.shared_dir, first.app_data_dir).roster().users) == {"imran"}
    assert set(UserStore(second.shared_dir, second.app_data_dir).roster().users) == {"boss"}


def test_entries_this_version_cannot_read_are_kept(tmp_path: Path) -> None:
    store = with_admin(tmp_path)
    data = read_account(store, "imran")
    data["nickname"] = "Im"  # a field from a newer version
    write_account(store, "imran", data)
    future = dict(data, username="future", role="owner")
    write_account(store, "future", future)
    write_account(store, "renamed", dict(data, username="someone-else"))
    assert set(store.roster().users) == {"imran"}
    assert set(store.roster().extra) == {"future.json", "renamed.json"}
    store.add("maria", "Maria")
    store.update("imran", display_name="Imran A.")
    assert read_account(store, "imran")["nickname"] == "Im"
    assert read_account(store, "imran")["display_name"] == "Imran A."
    assert read_account(store, "future") == future  # untouched
    assert sorted(p.name for p in store.users_dir.iterdir()) == [
        "future.json", "imran.json", "maria.json", "renamed.json"
    ]


def test_a_change_made_on_another_laptop_is_kept(tmp_path: Path) -> None:
    laptop_a = with_admin(tmp_path)
    laptop_b = UserStore(laptop_a.shared_dir, tmp_path / "app-b")
    assert set(laptop_b.roster().users) == {"imran"}
    laptop_a.add("maria", "Maria")
    laptop_b.add("john", "John")  # re-reads the files inside the lock first
    assert set(laptop_a.refresh().users) == {"imran", "maria", "john"}


def test_an_account_changed_meanwhile_gets_the_change_applied_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    laptop_a = with_admin(tmp_path)
    laptop_a.add("maria", "Maria", "editor")
    real_write = UserStore._write_account
    arrivals = ["Maria Khan"]

    def change_arrives_first(self: UserStore, user: User, expected: int | None) -> None:
        # Google Drive delivers another laptop's change to the same account just before
        # this write (no lock file can stop that).
        if arrivals:
            data = read_account(self, "maria")
            data.update(display_name=arrivals.pop(), revision=data["revision"] + 1)
            write_account(self, "maria", data)
        real_write(self, user, expected)

    monkeypatch.setattr(UserStore, "_write_account", change_arrives_first)
    laptop_a.set_role("maria", "viewer")
    maria = laptop_a.refresh().users["maria"]
    assert (maria.role, maria.display_name) == ("viewer", "Maria Khan")  # both changes kept
    assert maria.revision == 3  # 1 when added, 2 from the other laptop, 3 from this change

    # When the account keeps changing under it, the change gives up with a plain message.
    def always_changing(self: UserStore, user: User, expected: int | None) -> None:
        raise users_module._Conflict

    monkeypatch.setattr(UserStore, "_write_account", always_changing)
    with pytest.raises(RosterBusy) as info:
        laptop_a.set_role("maria", "editor")
    assert "another laptop" in str(info.value)


def sync_replica(source: Path, target: Path, since_ns: int) -> list[str]:
    """Google Drive, simulated: every file of ``team/`` written on ``source`` after
    ``since_ns`` replaces the same file on ``target`` (the newer copy wins)."""
    copied = []
    for path in sorted((source / "team").rglob("*")):
        if path.is_file() and path.stat().st_mtime_ns > since_ns and path.name != "users.lock":
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            copied.append(path.relative_to(source).as_posix())
    return copied


def test_a_laptop_behind_on_drive_only_touches_the_account_it_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replica_a, replica_b = tmp_path / "drive-a", tmp_path / "drive-b"
    laptop_a = with_admin(tmp_path, "drive-a", "app-a")
    _maria, maria_password = laptop_a.add("maria", "Maria", "editor", must_change_password=False)
    laptop_a.add("john", "John", "editor")
    replica_b.mkdir()
    sync_replica(replica_a, replica_b, 0)
    laptop_b = UserStore(replica_b, tmp_path / "app-b")
    assert not laptop_b.roster().users["maria"].disabled
    time.sleep(0.02)
    mark = time.time_ns()
    time.sleep(0.02)

    # The admin on laptop A turns maria off; Google Drive has not delivered it to B yet.
    monkeypatch.setenv("COMPUTERNAME", "LAPTOP-A")
    laptop_a.set_disabled("maria", True)
    # On laptop B maria still signs in (B's copy is behind), and an admin there renames john.
    monkeypatch.setenv("COMPUTERNAME", "LAPTOP-B")
    assert laptop_b.verify("maria", maria_password) is not None
    laptop_b.record_login("maria")
    laptop_b.update("john", display_name="John B.")

    # Signing in wrote no account file, only laptop B's own sign-in times.
    from_b = sync_replica(replica_b, replica_a, mark)
    assert sorted(from_b) == ["team/logins/laptop-b.json", "team/users/john.json"]
    sync_replica(replica_a, replica_b, mark)
    for laptop in (laptop_a, laptop_b):
        roster = laptop.refresh()
        assert roster.users["maria"].disabled is True  # the turn-off was not undone
        assert roster.users["john"].display_name == "John B."
    assert laptop_a.last_logins()["maria"] is not None


def test_sign_in_times_are_kept_per_laptop_at_most_hourly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = with_admin(tmp_path)
    before = store.account_path("imran").read_text(encoding="utf-8")
    start = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
    monkeypatch.setenv("COMPUTERNAME", "LAPTOP-1")
    store.record_login("imran", when=start)
    assert store.last_logins() == {"imran": start}
    assert store.list()[0].last_login_at == start
    store.record_login("imran", when=start + timedelta(minutes=10))
    assert store.last_logins()["imran"] == start
    store.record_login("imran", when=start + timedelta(hours=2))
    assert store.last_logins()["imran"] == start + timedelta(hours=2)
    store.record_login("ghost", when=start)  # unknown: nothing happens
    # Another laptop keeps its own file; the newest time wins.
    monkeypatch.setenv("COMPUTERNAME", "LAPTOP-2")
    store.record_login("imran", when=start + timedelta(hours=5))
    assert sorted(p.name for p in store.logins_dir.iterdir()) == ["laptop-1.json", "laptop-2.json"]
    assert store.list()[0].last_login_at == start + timedelta(hours=5)
    assert store.account_path("imran").read_text(encoding="utf-8") == before  # never written
    assert machine_name() == "LAPTOP-2"


def test_first_run_waits_while_the_teams_files_are_still_arriving(tmp_path: Path) -> None:
    # Google Drive delivered other team files, but no account yet.
    syncing = make_store(tmp_path, "syncing")
    syncing.logins_dir.mkdir(parents=True)
    (syncing.logins_dir / "laptop-a.json").write_text("{}", encoding="utf-8")
    roster = syncing.roster()
    assert (roster.state, roster.source, roster.needs_first_run) == ("incomplete", "none", False)
    assert roster.notice and "Google Drive" in roster.notice
    with pytest.raises(RosterReadOnly) as info:
        syncing.create_first_admin("newbie", "Newbie", "newbie-password-1")
    assert "Google Drive" in str(info.value)
    assert not syncing.users_dir.exists()

    # A folder that already holds channels needs a confirmation for the first admin.
    channels = make_store(tmp_path, "channels")
    (channels.shared_dir / "channels" / "kind-ledger").mkdir(parents=True)
    (channels.shared_dir / "channels" / "kind-ledger" / "channel.json").write_text(
        "{}", encoding="utf-8"
    )
    roster = channels.roster()
    assert roster.needs_first_run and roster.first_run_warning
    with pytest.raises(NeedsConfirmation):
        channels.create_first_admin("newbie", "Newbie", "newbie-password-1")
    admin = channels.create_first_admin("newbie", "Newbie", "newbie-password-1", confirmed=True)
    assert admin.role == "admin"

    # Our own lock file, an empty folder or Windows' desktop.ini are not team files.
    fresh = make_store(tmp_path, "fresh")
    fresh.team_dir.mkdir()
    (fresh.team_dir / "users").mkdir()
    (fresh.team_dir / "desktop.ini").write_text("x", encoding="utf-8")
    fresh.lock_path.write_text("1 X", encoding="utf-8")
    assert fresh.roster().needs_first_run


def test_lock_file_waits_for_a_fresh_lock_and_breaks_a_stale_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = with_admin(tmp_path)
    store.lock_path.write_text("123 OTHER", encoding="utf-8")
    monkeypatch.setattr(users_module, "LOCK_WAIT_S", 0.2)
    with pytest.raises(RosterBusy):
        store.add("maria")
    old = time.time() - users_module.LOCK_STALE_S - 5
    os.utime(store.lock_path, (old, old))
    store.add("maria")
    assert not store.lock_path.exists() and store.get("maria") is not None


def test_carry_over_fills_an_empty_folder_only(tmp_path: Path) -> None:
    current = with_admin(tmp_path)
    current.add("maria", "Maria")
    empty = make_store(tmp_path, "new-drive")
    assert current.carry_over_to(empty) is True
    assert set(empty.refresh().users) == {"imran", "maria"}
    assert same_account(empty.get("imran"), current.get("imran"))

    other_team = with_admin(tmp_path, "other-team")
    other_team.add("boss", "Boss")
    assert current.carry_over_to(other_team) is False
    assert set(other_team.refresh().users) == {"imran", "boss"}  # its own imran, untouched
    assert not same_account(other_team.get("imran"), current.get("imran"))

    broken = make_store(tmp_path, "broken")
    write_account(broken, "x-user", "{")
    with pytest.raises(RosterReadOnly):
        current.carry_over_to(broken)
    assert current.carry_over_to(current) is False


def test_carry_over_waits_for_drive_and_asks_before_joining_channels(tmp_path: Path) -> None:
    current = with_admin(tmp_path)
    # The team's Drive folder on a new laptop: channels and team files are there, but the
    # accounts have not arrived yet. Copying this laptop's accounts would start a second team.
    replica = make_store(tmp_path, "drive-c")
    (replica.shared_dir / "channels" / "kind-ledger").mkdir(parents=True)
    (replica.shared_dir / "channels" / "kind-ledger" / "channel.json").write_text(
        "{}", encoding="utf-8"
    )
    replica.logins_dir.mkdir(parents=True)
    (replica.logins_dir / "laptop-a.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RosterReadOnly) as info:
        current.carry_over_to(replica)
    assert "Google Drive" in str(info.value)
    assert not replica.users_dir.exists()

    # Channels but no team files: only with a confirmation (a team from an earlier version).
    (replica.logins_dir / "laptop-a.json").unlink()
    with pytest.raises(NeedsConfirmation) as asked:
        current.carry_over_to(replica)
    assert "Google Drive" in str(asked.value)
    assert current.carry_over_to(replica, confirmed=True) is True
    # Accounts that arrive later from the real team sit next to the copies; none is replaced.
    team = with_admin(tmp_path, "cloud", "app-cloud")
    team.add("sara", "Sara")
    shutil.copy2(team.account_path("sara"), replica.account_path("sara"))
    assert set(replica.refresh().users) == {"imran", "sara"}
