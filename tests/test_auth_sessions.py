"""Sessions: the signed ``ccs_session`` value (sign, verify, expiry, tampering), the account
fingerprint inside it, this laptop's list of sessions (sign-out ends a session on the server),
the per-laptop secret, "remember my login details" and the failed sign-in limit."""

from __future__ import annotations

import base64
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cashcow_studio.auth import limits, sessions
from cashcow_studio.auth.limits import LoginLimiter, wait_message
from cashcow_studio.auth.sessions import (
    REMEMBER_SECONDS,
    SESSION_SECONDS,
    account_key,
    end_session,
    end_sessions_of,
    forget_saved_login,
    issue_session,
    load_secret,
    read_saved_username,
    read_session,
    register_session,
    restrict_to_owner,
    save_username,
    session_is_live,
    session_matches,
)
from cashcow_studio.auth.users import User

NOW = 1_800_000_000


def account(name: str = "imran", salt: str = "c2FsdC1vbmU=") -> User:
    return User(
        username=name,
        display_name=name.title(),
        role="admin",
        hash="aGFzaA==",
        salt=salt,
        created_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
    )


def _payload(token: str) -> dict:
    body = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def test_a_session_round_trips_with_twelve_hours_or_thirty_days(tmp_path: Path) -> None:
    token, session = issue_session(tmp_path, account(), "LAPTOP-1", remember=False, now=NOW)
    payload = _payload(token)
    assert set(payload) == {
        "user", "machine", "issued_at", "expires_at", "remember", "sid", "account"
    }
    assert (payload["user"], payload["machine"]) == ("imran", "LAPTOP-1")
    assert payload["remember"] is False
    assert (payload["issued_at"], payload["expires_at"]) == (NOW, NOW + 12 * 3600)
    assert len(payload["sid"]) >= 20 and payload["sid"] == session.sid
    # The fingerprint never shows the salt or the hash.
    assert "c2FsdC1vbmU" not in token and "aGFzaA" not in token
    assert session.max_age is None  # the cookie ends with the app window
    assert read_session(tmp_path, token, now=NOW + 60) == session
    assert read_session(tmp_path, token, now=NOW + SESSION_SECONDS - 1) == session
    assert read_session(tmp_path, token, now=NOW + SESSION_SECONDS) is None

    kept, long_session = issue_session(tmp_path, account(), "LAPTOP-1", remember=True, now=NOW)
    assert long_session.expires_at - long_session.issued_at == 30 * 24 * 3600
    assert long_session.max_age == REMEMBER_SECONDS
    assert long_session.sid != session.sid  # every sign-in is a session of its own
    assert read_session(tmp_path, kept, now=NOW + 29 * 24 * 3600) == long_session
    assert read_session(tmp_path, kept, now=NOW + REMEMBER_SECONDS) is None


def test_tampered_forged_and_foreign_values_are_refused(tmp_path: Path) -> None:
    token, _session = issue_session(tmp_path, account("maria"), "LAPTOP-1", remember=False,
                                    now=NOW)
    body, signature = token.split(".")
    payload = _payload(token)

    def encode(data: dict) -> str:
        raw = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    promoted = encode(payload | {"user": "imran"})
    longer = encode(payload | {"expires_at": NOW + 10 * REMEMBER_SECONDS})
    for value in (
        f"{promoted}.{signature}",  # another user, same signature
        f"{longer}.{signature}",
        f"{body}.{signature[:-1]}{'B' if signature.endswith('A') else 'A'}",  # damaged
        f"{body}.",
        f"{body}{signature}",
        f"{body}.{signature}.extra",
        "",
        "not-a-session",
        "x" * 5000,
        "été.été",
    ):
        assert read_session(tmp_path, value, now=NOW + 1) is None, value[:40]
    # A value signed on another laptop (another secret) means nothing here.
    other_laptop = tmp_path / "other"
    foreign, _ = issue_session(other_laptop, account("maria"), "LAPTOP-1", remember=False,
                               now=NOW)
    assert read_session(tmp_path, foreign, now=NOW + 1) is None
    # Even a correctly signed value cannot outlive its kind of session.
    secret = load_secret(tmp_path, create=False)
    assert secret is not None
    stretched = encode(payload | {"expires_at": NOW + SESSION_SECONDS + 3600})
    forged = f"{stretched}.{sessions._signature(secret, stretched)}"
    assert read_session(tmp_path, forged, now=NOW + 1) is None
    # Issued in the future (a wrong clock) is refused too.
    future = encode(payload | {"issued_at": NOW + 3600, "expires_at": NOW + 7200})
    assert read_session(
        tmp_path, f"{future}.{sessions._signature(secret, future)}", now=NOW
    ) is None
    # A value of the old shape (no session id, no account fingerprint) is no session.
    old = {key: payload[key] for key in ("user", "machine", "issued_at", "expires_at", "remember")}
    old_body = encode(old)
    assert read_session(tmp_path, f"{old_body}.{sessions._signature(secret, old_body)}",
                        now=NOW + 1) is None


def test_the_fingerprint_follows_the_account_and_its_password(tmp_path: Path) -> None:
    imran = account()
    _token, session = issue_session(tmp_path, imran, "LAPTOP-1", remember=False, now=NOW)
    assert session_matches(tmp_path, session, imran)
    # The same name in another shared folder is another account.
    namesake = account(salt="b3RoZXItc2FsdA==")
    assert not session_matches(tmp_path, session, namesake)
    later = imran.model_copy(update={"created_at": datetime(2026, 10, 2, tzinfo=UTC)})
    assert not session_matches(tmp_path, session, later)
    # A copy of the same account (carried over to a new folder) still matches.
    assert session_matches(tmp_path, session, imran.model_copy(update={"revision": 7}))
    # The fingerprint is keyed with this laptop's secret.
    secret = load_secret(tmp_path, create=False)
    assert secret is not None
    assert account_key(secret, imran) == session.account
    assert account_key(b"x" * 32, imran) != session.account


def test_signing_out_ends_the_session_on_this_laptop(tmp_path: Path) -> None:
    _token, first = issue_session(tmp_path, account(), "LAPTOP-1", remember=True, now=NOW)
    _token, second = issue_session(tmp_path, account(), "LAPTOP-1", remember=False, now=NOW)
    _token, maria = issue_session(tmp_path, account("maria"), "LAPTOP-1", remember=False, now=NOW)
    assert not session_is_live(tmp_path, first, now=NOW + 1)  # not noted yet
    for session in (first, second, maria):
        register_session(tmp_path, session, now=NOW)
    assert all(session_is_live(tmp_path, s, now=NOW + 1) for s in (first, second, maria))
    assert not session_is_live(tmp_path, first, now=first.expires_at)

    end_session(tmp_path, first.sid)
    assert not session_is_live(tmp_path, first, now=NOW + 1)
    assert session_is_live(tmp_path, second, now=NOW + 1)
    assert end_sessions_of(tmp_path, "imran") == 1
    assert not session_is_live(tmp_path, second, now=NOW + 1)
    assert session_is_live(tmp_path, maria, now=NOW + 1)
    # A session id noted for one account never stands for another.
    impostor = maria.__class__(**{**maria.__dict__, "user": "imran"})
    assert not session_is_live(tmp_path, impostor, now=NOW + 1)
    # Expired sessions are forgotten at the next sign-in.
    _token, fresh = issue_session(tmp_path, account(), "LAPTOP-1", remember=False,
                                  now=maria.expires_at + 5)
    register_session(tmp_path, fresh, now=maria.expires_at + 5)
    assert end_sessions_of(tmp_path, "maria") == 0


def test_the_secret_is_32_random_bytes_made_on_the_first_sign_in(tmp_path: Path) -> None:
    assert load_secret(tmp_path, create=False) is None
    assert not (tmp_path / "session.secret").exists()
    token, _ = issue_session(tmp_path, account(), "LAPTOP-1", remember=False, now=NOW)
    secret_file = tmp_path / "session.secret"
    first = secret_file.read_bytes()
    assert len(first) == 32
    issue_session(tmp_path, account(), "LAPTOP-1", remember=False, now=NOW)
    assert secret_file.read_bytes() == first  # made once
    assert read_session(tmp_path, token, now=NOW + 1) is not None
    # Deleting the secret signs everyone on this laptop out.
    secret_file.unlink()
    assert read_session(tmp_path, token, now=NOW + 1) is None


def test_a_damaged_secret_is_replaced_at_the_next_sign_in(tmp_path: Path) -> None:
    token, _ = issue_session(tmp_path, account(), "LAPTOP-1", remember=False, now=NOW)
    (tmp_path / "session.secret").write_bytes(b"short")
    assert read_session(tmp_path, token, now=NOW + 1) is None
    fresh, _ = issue_session(tmp_path, account(), "LAPTOP-1", remember=False, now=NOW)
    assert len((tmp_path / "session.secret").read_bytes()) == 32
    assert read_session(tmp_path, fresh, now=NOW + 1) is not None


def test_the_secret_file_is_narrowed_to_this_account(tmp_path: Path) -> None:
    issue_session(tmp_path, account(), "LAPTOP-1", remember=False, now=NOW)
    secret_file = tmp_path / "session.secret"
    assert restrict_to_owner(secret_file) is True
    assert len(secret_file.read_bytes()) == 32  # still readable by us
    if sys.platform != "win32":
        assert secret_file.stat().st_mode & 0o077 == 0


def test_remember_my_login_details_keeps_only_the_username(tmp_path: Path) -> None:
    assert read_saved_username(tmp_path) is None
    save_username(tmp_path, "maria")
    saved = json.loads((tmp_path / "saved-login.json").read_text(encoding="utf-8"))
    assert set(saved) == {"username", "saved_at"} and saved["username"] == "maria"
    assert read_saved_username(tmp_path) == "maria"
    forget_saved_login(tmp_path)
    assert read_saved_username(tmp_path) is None
    forget_saved_login(tmp_path)  # nothing to forget is fine
    (tmp_path / "saved-login.json").write_text('{"username": "Not Valid!"}', encoding="utf-8")
    assert read_saved_username(tmp_path) is None


def test_five_failures_hold_an_account_back_for_ten_minutes() -> None:
    clock = [1000.0]
    limiter = LoginLimiter(clock=lambda: clock[0])
    for left in (4, 3, 2, 1, 0):
        assert limiter.retry_after("Imran") == 0
        assert limiter.failed("imran ") == left
    wait = limiter.retry_after("IMRAN")
    assert 590 <= wait <= 600
    assert limiter.retry_after("maria") == 0  # per account
    clock[0] += 300
    assert 290 <= limiter.retry_after("imran") <= 300
    clock[0] += 301
    assert limiter.retry_after("imran") == 0
    limiter.failed("imran")
    limiter.succeeded("imran")
    assert limiter.retry_after("imran") == 0
    assert "Wait 10 minutes" in wait_message(600)
    assert "Wait 1 minute " in wait_message(20)


def test_the_limiter_forgets_old_names_when_it_is_full(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_TRACKED", 3)
    limiter = LoginLimiter()
    for name in ("a1", "b1", "c1", "d1"):
        limiter.failed(name)
    assert len(limiter._failures) == 3
