"""Password hashing and generated passwords.

The scheme is the one the team already knows from SceneForge (``sceneforge/auth.py``):
PBKDF2-HMAC-SHA256, 240 000 iterations, a 16-byte random salt per user and a 32-byte key,
salt and hash stored base64-encoded next to the iteration count. Passwords themselves are
never stored, logged or returned; a generated password is shown once to the admin.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from dataclasses import dataclass

PBKDF2_ITERATIONS = 240_000
"""Slow enough that guessing a copied account file is costly, fast enough that a sign-in on a
modest laptop still feels instant."""
SALT_BYTES = 16
KEY_BYTES = 32
MAX_ITERATIONS = 10_000_000
"""A hand-edited or damaged account file cannot make one check run for minutes."""

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 1024
PASSWORD_RULE = f"Use at least {MIN_PASSWORD_LENGTH} characters."

# Characters an editor has to read off a note and type correctly: no 0/O or 1/l/I. Length
# carries the strength instead of symbols.
PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
GENERATED_LENGTH = 14


class PasswordRuleError(ValueError):
    """A new password that does not follow the rule; the message is plain English."""


@dataclass(frozen=True)
class PasswordHash:
    hash: str
    salt: str
    iterations: int


def check_password_rule(password: object) -> str:
    """Return the password when it may be used, else raise :class:`PasswordRuleError`."""
    if not isinstance(password, str) or not password.strip():
        raise PasswordRuleError(f"Type a password. {PASSWORD_RULE}")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordRuleError(
            f"The password is too short. {PASSWORD_RULE}"
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordRuleError(
            f"The password is too long (at most {MAX_PASSWORD_LENGTH} characters)."
        )
    return password


def derive(password: str, salt: bytes, iterations: int) -> bytes:
    data = password.encode("utf-8", "surrogatepass")
    return hashlib.pbkdf2_hmac("sha256", data, salt, iterations, dklen=KEY_BYTES)


def hash_password(password: str, *, iterations: int | None = None) -> PasswordHash:
    """A fresh salt and the PBKDF2 key for ``password``."""
    count = int(iterations or PBKDF2_ITERATIONS)
    salt = secrets.token_bytes(SALT_BYTES)
    key = derive(password, salt, count)
    return PasswordHash(hash=_b64(key), salt=_b64(salt), iterations=count)


def verify_password(password: str, hash_b64: str, salt_b64: str, iterations: int) -> bool:
    """True when ``password`` matches; compared in constant time.

    A record that cannot be decoded still costs one full derivation, so a broken entry does
    not answer faster than a real one.
    """
    try:
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(hash_b64, validate=True)
        count = int(iterations)
    except (ValueError, TypeError, binascii.Error):
        burn_time(password)
        return False
    if not salt or not expected or not 1 <= count <= MAX_ITERATIONS:
        burn_time(password)
        return False
    candidate = derive(password, salt, count)
    return hmac.compare_digest(candidate, expected)


def burn_time(password: str) -> None:
    """Spend the effort of a real check (for an unknown username), so the time a sign-in
    takes does not tell whether the account exists."""
    derive(password if isinstance(password, str) else "", b"\x00" * SALT_BYTES, PBKDF2_ITERATIONS)


def generate_password(length: int = GENERATED_LENGTH) -> str:
    """A password strong enough to be worth using and plain enough to type: letters and
    digits without look-alikes, at least one capital, one small letter and one digit."""
    size = max(int(length), MIN_PASSWORD_LENGTH)
    while True:
        candidate = "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(size))
        if (
            any(ch.isupper() for ch in candidate)
            and any(ch.islower() for ch in candidate)
            and any(ch.isdigit() for ch in candidate)
        ):
            return candidate


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
