"""Client-side rate limit for the DB Timetables API (free plan: 60 calls/min; we use 50)."""

import time
from collections import deque
from collections.abc import Callable

WINDOW_S = 60.0


class RateLimiter:
    """Sliding 60-second window: at most ``calls_per_min`` calls start inside any window."""

    def __init__(
        self,
        calls_per_min: int = 50,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._limit = calls_per_min
        self._clock = clock
        self._sleep = sleep
        self._starts: deque[float] = deque()

    def _forget_old(self, now: float) -> None:
        while self._starts and self._starts[0] <= now - WINDOW_S:
            self._starts.popleft()

    def acquire(self) -> None:
        """Block until one more call fits into the window, then count it."""
        now = self._clock()
        self._forget_old(now)
        if len(self._starts) >= self._limit:
            self._sleep(self._starts[0] + WINDOW_S - now)
            now = self._clock()
            self._forget_old(now)
        self._starts.append(now)
