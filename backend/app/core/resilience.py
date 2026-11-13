"""Outbound-call resilience: bounded retries with jittered backoff and a circuit breaker, for every call that leaves the system (AI provider, Meta Graph).

A dependency that is down must cost the user at most a couple of seconds once, then fail fast (the breaker is open) while the caller falls back to its deterministic,
labelled stub ("Simulated" / demo provider). Callers keep their own timeouts and error messages; this module only decides *whether* and *when* to try."""
from __future__ import annotations

import random
import threading
import time
from typing import Callable

import httpx

RETRY_STATUS = frozenset({429, 502, 503, 504})


class CircuitOpen(Exception):
    def __init__(self, name: str, retry_in_s: float):
        super().__init__(f"{name} is temporarily unavailable (retrying in about {int(retry_in_s) + 1} s)")
        self.name, self.retry_in_s = name, retry_in_s


class Breaker:
    """closed -> (threshold consecutive failures) -> open for reset_s -> half-open (one trial call) -> closed on success / open again on failure."""

    def __init__(self, name: str, threshold: int = 3, reset_s: float = 30.0):
        self.name, self.threshold, self.reset_s = name, threshold, reset_s
        self._fails, self._opened_at, self._trial = 0, 0.0, False
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._fails < self.threshold:
                return "closed"
            return "half-open" if time.monotonic() - self._opened_at >= self.reset_s else "open"

    def before(self) -> None:
        with self._lock:
            if self._fails < self.threshold:
                return
            wait = self.reset_s - (time.monotonic() - self._opened_at)
            if wait > 0:
                raise CircuitOpen(self.name, wait)
            if self._trial:                                           # someone is already probing; everyone else keeps failing fast
                raise CircuitOpen(self.name, 1.0)
            self._trial = True

    def success(self) -> None:
        with self._lock:
            self._fails, self._trial = 0, False

    def failure(self) -> None:
        with self._lock:
            self._fails += 1
            self._trial = False
            if self._fails >= self.threshold:
                self._opened_at = time.monotonic()


BREAKERS = {name: Breaker(name) for name in ("ai", "meta")}


def call(breaker: Breaker, fn: Callable[[], httpx.Response], *, retries: int = 2, backoff_s: float = 0.3, sleep: Callable[[float], None] = time.sleep) -> httpx.Response:
    """Run `fn` (one HTTP request). Transport errors, timeouts and 429/502/503/504 are retried `retries` times with 0.3 s, 0.9 s ... jittered backoff.
    4xx other than 429 are the caller's answer and neither retried nor counted against the breaker. Raises CircuitOpen immediately while the breaker is open."""
    breaker.before()
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = fn()
        except httpx.TransportError as e:                             # includes timeouts and connection errors
            last = e
        else:
            if r.status_code not in RETRY_STATUS and r.status_code < 500:
                breaker.success()
                return r
            if attempt == retries:
                breaker.failure()
                return r                                             # the caller turns the final 5xx/429 into its own message
        if attempt < retries:
            sleep(backoff_s * (3 ** attempt) * (0.75 + random.random() * 0.5))
    breaker.failure()
    assert last is not None
    raise last
