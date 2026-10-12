"""Process-wide admission control shared by AI image generation and editing.

Admission never queues or sleeps. Short synchronous critical sections make
duplicate requests atomic and cancellation cleanup independent of awaits.
"""

import math
import threading
import time
from collections import deque
from dataclasses import dataclass

USER_COOLDOWN = 60
MAX_CONCURRENT = 5
GLOBAL_WINDOW = 600
GLOBAL_REQUESTS = 20
OPERATION_TIMEOUT = 240


@dataclass(frozen=True)
class ImageLimitDenied:
    reason: str
    retry_after: int = 30


class ImageLease:
    def __init__(self, limiter, user_id):
        self._limiter = limiter
        self.user_id = user_id
        self.started = False
        self._confirmed_failure = False

    def mark_started(self):
        """Charge the rolling budget only immediately before an API request."""
        with self._limiter._lock:
            if self._limiter._active.get(self.user_id) is not self:
                raise RuntimeError("Image lease is no longer active")
            if not self.started:
                self.started = True
                self._limiter._recent.append(self._limiter._clock())

    def mark_failed(self):
        """Waive user cooldown only after a confirmed provider failure.

        The API attempt still consumes the shared rolling request budget.
        Timeouts, interrupted requests and photo delivery failures must not
        call this: the image may already have been generated remotely.
        """
        with self._limiter._lock:
            if self._limiter._active.get(self.user_id) is not self:
                raise RuntimeError("Image lease is no longer active")
            self._confirmed_failure = True

    def release(self):
        with self._limiter._lock:
            if self._limiter._active.get(self.user_id) is not self:
                return
            self._limiter._active.pop(self.user_id)
            if self.started and not self._confirmed_failure:
                # An uncertain outcome may already have generated an image.
                self._limiter._cooldowns[self.user_id] = (
                    self._limiter._clock() + USER_COOLDOWN
                )


class ImageLimiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._active: dict[int, ImageLease] = {}
        self._cooldowns: dict[int, float] = {}
        self._recent: deque[float] = deque()

    def acquire(self, user_id: int) -> ImageLease | ImageLimitDenied:
        if type(user_id) is not int or not user_id:
            return ImageLimitDenied("image_limit_identity")
        with self._lock:
            now = self._clock()
            while self._recent and self._recent[0] <= now - GLOBAL_WINDOW:
                self._recent.popleft()
            self._cooldowns = {
                user: expiry for user, expiry in self._cooldowns.items() if expiry > now
            }
            if user_id in self._active:
                return ImageLimitDenied("image_limit_user_busy")
            if user_id in self._cooldowns:
                return ImageLimitDenied(
                    "image_limit_cooldown",
                    max(1, math.ceil(self._cooldowns[user_id] - now)),
                )
            if len(self._active) >= MAX_CONCURRENT:
                return ImageLimitDenied("image_limit_bot_busy")
            # Active downloads reserve budget so simultaneous edits cannot all
            # slip through the last remaining request in the global window.
            reserved = sum(not lease.started for lease in self._active.values())
            if len(self._recent) + reserved >= GLOBAL_REQUESTS:
                retry = (
                    max(1, math.ceil(self._recent[0] + GLOBAL_WINDOW - now))
                    if self._recent
                    else 30
                )
                return ImageLimitDenied("image_limit_global", retry)
            lease = ImageLease(self, user_id)
            self._active[user_id] = lease
            return lease


image_limiter = ImageLimiter()
