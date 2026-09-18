"""
Single-flight TTL cache for an expensive device read.

Reading status costs a full STA reply: about 1750 bytes, or 0.3s of solid
transmission at 57600 baud, during which the device's MCU is formatting text
rather than tending the video path. That makes status the service's most
expensive operation and the one clients repeat most often, so the number of
STA reads should depend on how often the device is asked, not on how many
clients happen to be asking. A second poller, a stray curl loop, or a future
integration would otherwise multiply the load on the serial port.
"""

import threading
import time
from collections.abc import Callable

# Distinguishes "nothing cached" from a cached value that is itself falsy.
_MISSING = object()


class StatusCache[T]:
    """
    Caches `read`'s result for `ttl` seconds and collapses concurrent callers
    onto a single underlying read.

    Thread-safe: the service's endpoints are synchronous, so FastAPI runs them
    in a thread pool and several requests can arrive here at once.

    Failures are never cached. If `read` raises, the exception reaches the
    caller and the next caller tries the device again.
    """

    def __init__(self, read: Callable[[], T], ttl: float, clock: Callable[[], float] = time.monotonic):
        self._read = read
        self._ttl = ttl
        self._clock = clock
        # Held for the whole of a read, so concurrent callers queue behind one
        # device round trip instead of each starting their own.
        self._read_lock = threading.Lock()
        # Guards the fields below, and is never held across a read.
        self._state_lock = threading.Lock()
        self._value: object = _MISSING
        self._read_at = 0.0
        self._generation = 0
        self._value_generation = -1

    def invalidate(self) -> None:
        """
        Drop the cached value, and any read already in flight along with it.

        Commands call this: a read that started before the command cannot
        describe the device after it.
        """
        with self._state_lock:
            self._generation += 1

    def get(self) -> T:
        """Return the cached value, reading from the device if it is stale."""
        cached = self._fresh()
        if cached is not _MISSING:
            return cached  # type: ignore[return-value]

        with self._read_lock:
            # Another thread may have refreshed the value while we waited.
            cached = self._fresh()
            if cached is not _MISSING:
                return cached  # type: ignore[return-value]

            with self._state_lock:
                generation = self._generation

            value = self._read()

            with self._state_lock:
                # A command landed while the read was in flight, so this value
                # describes the device before that command. Return it to the
                # caller who waited for it, but do not serve it to anyone else.
                if self._generation == generation:
                    self._value = value
                    self._read_at = self._clock()
                    self._value_generation = generation
            return value

    def _fresh(self) -> object:
        """The cached value if it is still usable, else `_MISSING`."""
        with self._state_lock:
            if self._value_generation != self._generation:
                return _MISSING
            if self._clock() - self._read_at >= self._ttl:
                return _MISSING
            return self._value
