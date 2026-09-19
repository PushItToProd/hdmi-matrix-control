"""
Tests for the serial layer, against a fake port.

`HDMIMatrix` is built with `__new__` because `__init__` opens a real serial
port. Only the attributes the methods under test touch are set.

Run without the project environment:
    PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with pyserial \
        pytest -p no:cacheprovider test_hdmi_matrix.py
"""

import base64
import threading
import time

import pytest

from hdmi_matrix import HDMIMatrix


class FakeSerial:
    """
    A port that answers the nth write with the nth scripted reply, after that
    reply's delay. A reply is delivered whole, which is enough to test when
    bytes are read; how the device splits a reply across packets is a question
    for a real capture, not for this fake.
    """

    def __init__(self, replies: list[tuple[float, bytes]]):
        self._replies = list(replies)
        self._queue: list[tuple[float, bytes]] = []
        self._buffer = b''
        self.timeout = 1.0
        self.writes: list[bytes] = []
        self.resets = 0

    def reset_input_buffer(self):
        self.resets += 1
        self._queue.clear()
        self._buffer = b''

    def write(self, data: bytes):
        self.writes.append(data)
        if self._replies:
            delay, reply = self._replies.pop(0)
            self._queue.append((time.monotonic() + delay, reply))

    def _deliver(self):
        now = time.monotonic()
        while self._queue and self._queue[0][0] <= now:
            self._buffer += self._queue.pop(0)[1]

    @property
    def in_waiting(self) -> int:
        self._deliver()
        return len(self._buffer)

    def read(self, size: int = 1) -> bytes:
        deadline = time.monotonic() + (self.timeout or 0.0)
        while True:
            self._deliver()
            if self._buffer:
                taken, self._buffer = self._buffer[:size], self._buffer[size:]
                return taken
            if time.monotonic() >= deadline:
                return b''
            time.sleep(0.001)


def matrix_with(serial: FakeSerial) -> HDMIMatrix:
    matrix = HDMIMatrix.__new__(HDMIMatrix)
    matrix._serial = serial
    matrix._lock = threading.Lock()
    matrix._read_delay = 0.1
    return matrix


A_REPLY = b'spoasi02\r\n\r<s>SPOASI02</s><user>Set Output A to Video Input 02</user>\r'
B_REPLY = b'spobsi04\r\n\r<s>SPOBSI04</s><user>Set Output B to Video Input 04</user>\r'


def kinds(events):
    return [e['kind'] for e in events]


def reads(events):
    return [e for e in events if e['kind'] == 'read']


def test_capture_records_each_write_and_the_bytes_that_follow_it():
    serial = FakeSerial([(0.01, A_REPLY), (0.01, B_REPLY)])
    events = matrix_with(serial).send_raw(['spoasi02', 'spobsi04'], gap=0.05, read_seconds=0.1)

    assert serial.writes == [b'spoasi02\r', b'spobsi04\r']
    assert kinds(events) == ['write', 'read', 'write', 'read']
    assert [e['data'] for e in reads(events)] == [A_REPLY, B_REPLY]


def test_capture_attributes_a_reply_deferred_past_the_next_write_to_its_arrival():
    """
    The failure this endpoint exists to measure: a reply that arrives after the
    following command has already gone out. It must show up where it arrived,
    not folded into the first command's reply.
    """
    serial = FakeSerial([(0.15, A_REPLY), (0.01, B_REPLY)])
    events = matrix_with(serial).send_raw(['spoasi02', 'spobsi04'], gap=0.05, read_seconds=0.3)

    assert kinds(events) == ['write', 'write', 'read']
    late = reads(events)[0]
    assert late['data'] == A_REPLY + B_REPLY
    assert late['at_ms'] > 100


def test_capture_reports_silence_as_no_read_events():
    serial = FakeSerial([])
    events = matrix_with(serial).send_raw(['spobcopyoutaoff'], read_seconds=0.05)

    assert kinds(events) == ['write']


def test_capture_clears_stale_input_unless_told_not_to():
    serial = FakeSerial([(0.01, A_REPLY)])
    matrix_with(serial).send_raw(['spoasi02'], read_seconds=0.05)
    assert serial.resets == 1

    serial = FakeSerial([(0.01, A_REPLY)])
    matrix_with(serial).send_raw(['spoasi02'], read_seconds=0.05, reset_input=False)
    assert serial.resets == 0


def test_capture_restores_the_ports_timeout():
    serial = FakeSerial([(0.01, A_REPLY)])
    serial.timeout = 1.0
    matrix_with(serial).send_raw(['spoasi02'], read_seconds=0.05)
    assert serial.timeout == 1.0


def test_capture_holds_the_lock_for_its_whole_window():
    """A capture owns the port: nothing else may write into its read window."""
    serial = FakeSerial([(0.01, A_REPLY)])
    matrix = matrix_with(serial)
    held = []

    def probe():
        held.append(matrix._lock.acquire(blocking=False))

    thread = threading.Thread(target=probe)
    original = serial.read

    def read_once_then_probe(size=1):
        serial.read = original
        thread.start()
        thread.join()
        return original(size)

    serial.read = read_once_then_probe
    matrix.send_raw(['spoasi02'], read_seconds=0.05)

    assert held == [False]
