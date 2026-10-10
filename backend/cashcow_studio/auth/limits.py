"""At most five failed sign-ins per account per ten minutes on this laptop.

The count lives in the running app (one per laptop) and is keyed by the lower-cased
username, whether or not that account exists, so the answer never tells which names are
real. A correct password while the account is held back is refused too; a successful
sign-in clears the count.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable

MAX_FAILED_LOGINS = 5
WINDOW_SECONDS = 600
MAX_TRACKED = 10_000
MAX_KEY_LENGTH = 64


class LoginLimiter:
    def __init__(
        self,
        max_failures: int = MAX_FAILED_LOGINS,
        window_s: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max(1, int(max_failures))
        self.window_s = float(window_s)
        self.clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(username: object) -> str:
        text = username if isinstance(username, str) else ""
        return text.strip().lower()[:MAX_KEY_LENGTH]

    def _recent(self, key: str, now: float) -> deque[float] | None:
        failures = self._failures.get(key)
        if failures is None:
            return None
        while failures and failures[0] <= now - self.window_s:
            failures.popleft()
        if not failures:
            del self._failures[key]
            return None
        return failures

    def retry_after(self, username: object) -> int:
        """Seconds until this account may try again; 0 when it may try now."""
        key = self.key(username)
        with self._lock:
            now = self.clock()
            failures = self._recent(key, now)
            if failures is None or len(failures) < self.max_failures:
                return 0
            oldest_that_counts = failures[len(failures) - self.max_failures]
            return max(1, math.ceil(oldest_that_counts + self.window_s - now))

    def failed(self, username: object) -> int:
        """Note a failure; returns how many tries are left in the window."""
        key = self.key(username)
        with self._lock:
            now = self.clock()
            failures = self._recent(key, now)
            if failures is None:
                if len(self._failures) >= MAX_TRACKED:
                    self._failures.pop(next(iter(self._failures)))
                failures = self._failures.setdefault(key, deque())
            failures.append(now)
            return max(0, self.max_failures - len(failures))

    def succeeded(self, username: object) -> None:
        with self._lock:
            self._failures.pop(self.key(username), None)


def wait_message(seconds: int) -> str:
    minutes = max(1, math.ceil(seconds / 60))
    unit = "minute" if minutes == 1 else "minutes"
    return (
        "Too many wrong passwords for this account on this laptop. "
        f"Wait {minutes} {unit} and try again, or ask an admin to reset the password."
    )
