"""Single-flight observations and serialized, optionally conditional commands."""

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from copy import deepcopy


class VersionConflict(Exception):
    """No command was sent: the caller's observation is no longer current."""


@dataclass(frozen=True)
class Observation[T]:
    value: T
    observed_at: datetime
    observed_monotonic: float
    version: int


@dataclass(frozen=True)
class RememberedState[T]:
    observation: Observation[T] | None
    requested: dict[str, dict]
    uncertain: bool
    version: int


class StatusCache[T]:
    """Collapse concurrent reads and guard version-check/write transactions.

    A revision advances on changed observations and on every attempted command,
    including commands whose outcome is unknown. Identical fresh readings keep
    the revision but advance observed_at. Versions are scoped to this service;
    a microsecond epoch seed avoids reusing small counters after a restart.
    """

    def __init__(self, read: Callable[[], T], ttl: float, clock: Callable[[], float] = time.monotonic):
        self._read = read
        self._ttl = ttl
        self._clock = clock
        self._read_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._snapshot: Observation[T] | None = None
        self._read_at = 0.0
        self._generation = 0
        self._value_generation = -1
        self._version = time.time_ns() // 1000
        self._requested: dict[str, dict] = {}
        self._uncertain = False
        self._last_read_attempt = float('-inf')

    def remembered(self) -> RememberedState[T]:
        """Return display state without performing serial I/O."""
        with self._state_lock:
            return RememberedState(self._snapshot, deepcopy(self._requested),
                                   self._uncertain, self._version)

    def refresh_display(self, minimum_interval: float = 5.0) -> bool:
        """Read on explicit refresh, sharing a cooldown across UI clients.

        Failed reads also consume the cooldown. API verification retains its
        existing max_age contract, but its reads count toward this UI limit.
        """
        with self._read_lock:
            if self._clock() - self._last_read_attempt < minimum_interval:
                return False
            self.observation(max_age=0)
            return True

    def invalidate(self) -> None:
        with self._state_lock:
            self._generation += 1
            self._version += 1

    def get(self) -> T:
        return self.observation().value

    def age_ms(self, observation: Observation[T]) -> int:
        return max(0, int((self._clock() - observation.observed_monotonic) * 1000))

    def observation(self, max_age: float | None = None) -> Observation[T]:
        """Read only when the observation exceeds the caller's reuse budget.

        Ordinary consumers share the long default TTL. Command verification
        can request a shorter budget without shortening everybody else's.
        """
        ttl = self._ttl if max_age is None else min(self._ttl, max_age)
        # Also taken by command(): a status read cannot sneak between a
        # version check, a write, and invalidation. Waiting readers share the
        # result even when the underlying read takes longer than the TTL.
        with self._read_lock:
            with self._state_lock:
                if (self._snapshot is not None and
                        self._value_generation == self._generation and
                        self._clock() - self._read_at < ttl):
                    return self._snapshot
                generation = self._generation
                version = self._version
            observed_monotonic = self._clock()
            observed_at = datetime.now(timezone.utc)
            self._last_read_attempt = observed_monotonic
            value = self._read()
            with self._state_lock:
                if generation == self._generation:
                    if self._snapshot is None or value != self._snapshot.value:
                        self._version += 1
                    snapshot = Observation(value, observed_at, observed_monotonic, self._version)
                    self._snapshot = snapshot
                    self._read_at = self._clock()
                    self._value_generation = generation
                    self._requested.clear()
                    self._uncertain = False
                    return snapshot
                # An external invalidation during the read makes it unsuitable
                # for reuse or a subsequent conditional write.
                return Observation(value, observed_at, observed_monotonic, version)

    @contextmanager
    def command(self, if_version: int | None = None,
                requested: dict[str, dict] | None = None) -> Iterator[None]:
        with self._read_lock:
            if if_version is not None:
                # Conditional writes must not accept the long display-cache TTL.
                observation = self.observation(max_age=0.2)
                with self._state_lock:
                    matches = (observation.version == if_version == self._version and
                               self._value_generation == self._generation)
                if not matches:
                    raise VersionConflict('status changed; refresh before applying')
            succeeded = False
            try:
                yield
                succeeded = True
            finally:
                # Even a timeout can follow an applied (or partial) command.
                self.invalidate()
                with self._state_lock:
                    if succeeded and requested:
                        for output, route in requested.items():
                            self._requested[output] = dict(route)
                    else:
                        self._uncertain = True
