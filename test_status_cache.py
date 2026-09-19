"""
Tests for the single-flight TTL status cache.

Run without the project environment:
    PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest \
        pytest -p no:cacheprovider test_status_cache.py
"""

import threading

import pytest

from status_cache import StatusCache


class FakeClock:
    """A monotonic clock the test advances by hand."""

    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float):
        self.now += seconds


class CountingReader:
    """Returns a new value on each call and records how many times it ran."""

    def __init__(self):
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return f"reading {self.calls}"


def test_reads_within_the_ttl_are_served_from_the_cache():
    reader, clock = CountingReader(), FakeClock()
    cache = StatusCache(reader, ttl=1.0, clock=clock)

    assert cache.get() == "reading 1"
    clock.advance(0.9)
    assert cache.get() == "reading 1"
    assert reader.calls == 1


def test_the_device_is_read_again_once_the_ttl_has_passed():
    reader, clock = CountingReader(), FakeClock()
    cache = StatusCache(reader, ttl=1.0, clock=clock)

    cache.get()
    clock.advance(1.0)
    assert cache.get() == "reading 2"
    assert reader.calls == 2


def test_invalidate_forces_the_next_read_to_reach_the_device():
    reader, clock = CountingReader(), FakeClock()
    cache = StatusCache(reader, ttl=60.0, clock=clock)

    cache.get()
    cache.invalidate()
    assert cache.get() == "reading 2"
    assert reader.calls == 2


def test_failures_are_not_cached():
    calls = []

    def reader():
        calls.append(None)
        if len(calls) == 1:
            raise RuntimeError("serial read failed")
        return "reading"

    cache = StatusCache(reader, ttl=60.0, clock=FakeClock())

    with pytest.raises(RuntimeError):
        cache.get()
    assert cache.get() == "reading"
    assert len(calls) == 2


def test_callers_arriving_during_a_read_share_its_result():
    started = threading.Event()
    finish = threading.Event()
    reader = CountingReader()

    def blocking_read() -> str:
        started.set()
        assert finish.wait(timeout=5), "test did not release the read"
        return reader()

    cache = StatusCache(blocking_read, ttl=60.0, clock=FakeClock())
    results: list[str] = []
    lock = threading.Lock()

    def call():
        value = cache.get()
        with lock:
            results.append(value)

    threads = [threading.Thread(target=call) for _ in range(5)]
    for t in threads:
        t.start()
    assert started.wait(timeout=5), "no read started"
    finish.set()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()

    assert reader.calls == 1
    assert results == ["reading 1"] * 5


def test_a_command_during_a_read_keeps_that_reading_out_of_the_cache():
    # The reading describes the device before the command, so the caller that
    # waited for it still gets it, but nobody else may be served it.
    reader = CountingReader()
    cache: StatusCache[str] = None  # type: ignore[assignment]

    def read_then_invalidate() -> str:
        value = reader()
        if reader.calls == 1:
            cache.invalidate()
        return value

    cache = StatusCache(read_then_invalidate, ttl=60.0, clock=FakeClock())

    assert cache.get() == "reading 1"
    assert cache.get() == "reading 2"
    assert reader.calls == 2


def test_freshness_metadata_and_revision_track_observations():
    clock = FakeClock()
    value = ['same']
    def reader():
        clock.advance(.3)
        return value[0]
    cache = StatusCache(reader, ttl=.2, clock=clock)
    first = cache.observation()
    assert cache.age_ms(first) == 300
    clock.advance(.1)
    assert cache.observation() is first
    clock.advance(.11)
    second = cache.observation()
    assert second.version == first.version
    assert second.observed_monotonic > first.observed_monotonic
    value[0] = 'changed remotely'
    clock.advance(.21)
    assert cache.observation().version > second.version


def test_conditional_command_refreshes_expired_status_before_writing():
    from status_cache import VersionConflict
    clock = FakeClock()
    reader = CountingReader()
    cache = StatusCache(reader, ttl=.2, clock=clock)
    version = cache.observation().version
    clock.advance(.3)
    with pytest.raises(VersionConflict):
        with cache.command(version):
            pytest.fail('stale command reached device')
    assert reader.calls == 2


def test_failed_command_invalidates_and_rejects_old_version():
    from status_cache import VersionConflict
    cache = StatusCache(lambda: 'unchanged', ttl=60)
    version = cache.observation().version
    with pytest.raises(OSError):
        with cache.command(version):
            raise OSError('lost acknowledgement')
    with pytest.raises(VersionConflict):
        with cache.command(version):
            pytest.fail('reused version after uncertain write')


def test_simultaneous_conditional_commands_cannot_both_write():
    from status_cache import VersionConflict
    cache = StatusCache(lambda: 'same', ttl=60)
    version = cache.observation().version
    gate = threading.Barrier(2)
    outcomes = []
    def apply():
        gate.wait(timeout=5)
        try:
            with cache.command(version):
                outcomes.append('written')
        except VersionConflict:
            outcomes.append('conflict')
    threads = [threading.Thread(target=apply) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()
    assert sorted(outcomes) == ['conflict', 'written']


def test_unchanged_refresh_allows_conditional_command():
    clock = FakeClock()
    cache = StatusCache(lambda: 'same', ttl=.2, clock=clock)
    before = cache.observation()
    clock.advance(.3)
    with cache.command(before.version):
        pass
    assert cache.observation().version > before.version


def test_failed_conditional_refresh_never_writes():
    clock = FakeClock()
    def read():
        if clock.now > 0:
            raise OSError('status unavailable')
        return 'initial'
    cache = StatusCache(read, ttl=.2, clock=clock)
    version = cache.observation().version
    clock.advance(.3)
    with pytest.raises(OSError):
        with cache.command(version):
            pytest.fail('wrote without a usable observation')
